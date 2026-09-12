from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from . import __version__
from .artifact.store import list_artifacts, load_artifact, save_artifact
from .escalation.control import ControlChannel
from .escalation.handoff import CliOperatorConsole, EscalationManager, NoOperatorConsole, OperatorConsole
from .escalation.scripted import scripted_console
from .evidence.logger import RunEvidence, new_run_id
from .policy.engine import Policy
from .replay.result import ReplayStatus

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "policy" / "allowlist.yaml"
DEFAULT_PROFILE = ROOT / "profiles" / "cu_core.yaml"
DEFAULT_EVIDENCE = ROOT / "evidence"
DEFAULT_ARTIFACTS = ROOT / "artifacts"


def _parse_params(items: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        if "=" not in it:
            raise click.BadParameter(f"--param expects name=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def _console(name: str) -> OperatorConsole:
    if name == "cli":
        return CliOperatorConsole()
    if name == "none":
        return NoOperatorConsole()
    if name.startswith("scripted:"):
        return scripted_console(name.split(":", 1)[1])
    raise click.BadParameter("--operator must be cli, none, or scripted:<reauth|approve|deny|dismiss_overlay>")


@click.group()
@click.version_option(__version__)
def main() -> None:
    ...


@main.command("serve-app")
@click.option("--port", default=5055, show_default=True)
def serve_app(port: int) -> None:
    from target_app.app import app

    click.echo(f"CU-Core Teller Desk (mock) on http://127.0.0.1:{port}/teller/members/search")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


@main.command()
@click.option("--goal", required=True, help="Natural-language goal. Reference parameters as {{name}}.")
@click.option("--target", default=lambda: os.environ.get("CUA_TARGET_URL", "http://127.0.0.1:5055") + "/teller/members/search", show_default="mock app search screen")
@click.option("--name", "cap_name", required=True, help="Capability name for the artifact (snake_case).")
@click.option("--param", "params", multiple=True, help="name=value; repeatable.")
@click.option("--sensitive", "sensitive", multiple=True, help="Parameter names that must never be logged or stored.")
@click.option("--llm", type=click.Choice(["anthropic", "mock"]), default="anthropic", show_default=True)
@click.option("--model", default=None, help="Model id (default: $CUA_MODEL or claude-opus-5).")
@click.option("--headed/--headless", default=True, show_default=True, help="Headed keeps the live window available for handoff.")
@click.option("--max-steps", default=25, show_default=True)
@click.option("--operator", default="cli", show_default=True, help="cli | none | scripted:<name>")
@click.option("--policy", "policy_path", default=str(DEFAULT_POLICY), show_default=True)
@click.option("--profile", "profile_path", default=str(DEFAULT_PROFILE), show_default=True)
@click.option("--out", default=None, help="Artifact path (default artifacts/<name>.json)")
@click.option("--evidence-dir", default=str(DEFAULT_EVIDENCE), show_default=True)
@click.option("--cdp-port", default=None, type=int, help="Expose Chromium DevTools on this port for remote operator attach.")
@click.option("--run-name", default=None, help="Evidence folder name (default: discovery-<timestamp>-<id>).")
def discover(goal, target, cap_name, params, sensitive, llm, model, headed, max_steps, operator, policy_path, profile_path, out, evidence_dir, cdp_port, run_name) -> None:
    from .agent.loop import DiscoveryRunner
    from .agent.recorder import build_artifact, load_profile
    from .surface.browser import PlaywrightSurface

    os.environ.pop("CUA_NO_LLM", None)
    param_values = _parse_params(params)
    policy = Policy.load(policy_path)
    run_id = run_name or new_run_id("discovery")
    evidence = RunEvidence(evidence_dir, run_id, policy.redactor, echo=True)
    click.echo(f"run {run_id}  evidence -> {evidence.dir}")

    if llm == "mock":
        from .agent.mock_llm import MockLLM

        client = MockLLM()
        click.secho("NOTE: --llm mock is a rule-based stand-in for offline testing; its output is not a real discovery run.", fg="yellow")
    else:
        from .agent.llm import AnthropicLLM

        client = AnthropicLLM(model=model)
    click.echo(f"model: {client.name}")

    surface = PlaywrightSurface(headed=headed, cdp_port=cdp_port)
    surface.start()
    try:
        escalation = EscalationManager(ControlChannel(), _console(operator), evidence, surface)
        runner = DiscoveryRunner(surface, client, policy, evidence, escalation, max_steps=max_steps)
        result = runner.run(goal, target, param_values, set(sensitive))
    finally:
        surface.stop()

    click.echo("")
    for t in result.trace:
        click.echo("  " + t.line())
    if not result.success:
        click.secho(f"\nDiscovery did not succeed: {result.stop_reason}", fg="red")
        sys.exit(1)
    artifact = build_artifact(result, name=cap_name, profile=load_profile(profile_path))
    path = Path(out) if out else DEFAULT_ARTIFACTS / f"{cap_name}.json"
    save_artifact(artifact, path)
    evidence.write_json("artifact.json", artifact.model_dump(mode="json"))
    click.secho(f"\nGoal met in {len(result.trace)} steps ({result.llm_calls} model calls, {result.usage}).", fg="green")
    click.echo(f"outputs: {result.outputs}")
    click.echo(f"artifact: {path}  ({len(artifact.steps)} steps, {len(artifact.inputs)} inputs, {len(artifact.outputs)} outputs, {len(artifact.outcomes)} outcome detectors)")
    click.echo(f"evidence: {evidence.dir}")


def _run_replay(artifact_path: Path, param_values: dict[str, str], *, inject, confirm_risky, headed, operator, policy_path, evidence_dir, allow_coords, quiet=False, run_name=None):
    from .replay.engine import ReplayEngine
    from .surface.browser import PlaywrightSurface

    artifact = load_artifact(artifact_path)
    policy = Policy.load(policy_path)
    run_id = run_name or new_run_id("replay")
    evidence = RunEvidence(evidence_dir, run_id, policy.redactor, echo=not quiet)
    if not quiet:
        click.echo(f"run {run_id}  artifact {artifact.name} v{artifact.version} [{artifact.approval}]  evidence -> {evidence.dir}")
    surface = PlaywrightSurface(headed=headed)
    surface.start()
    try:
        if inject:
            surface.set_cookie("chaos", inject, artifact.target.base_url)
            evidence.log("test.fault_injected", mode=inject, note="chaos cookie set on the browser context (test-only)")
        escalation = EscalationManager(ControlChannel(), _console(operator), evidence, surface)
        engine = ReplayEngine(surface, policy, evidence, escalation, confirm_risky=confirm_risky, allow_coords=allow_coords)
        result = engine.run(artifact, param_values)
    finally:
        surface.stop()
    return result


REPLAY_OPTS = [
    click.option("--param", "params", multiple=True, help="name=value; repeatable."),
    click.option("--inject", default=None, type=click.Choice(["slow", "dialog", "timeout", "denied", "error500", "sticky_dialog"]), help="Test-only fault injection on the mock app."),
    click.option("--confirm-risky", is_flag=True, help="Caller confirms risky steps may run unattended (still requires an approved artifact)."),
    click.option("--headed/--headless", default=False, show_default=True),
    click.option("--operator", default="none", show_default=True, help="cli | none | scripted:<name>"),
    click.option("--policy", "policy_path", default=str(DEFAULT_POLICY), show_default=True),
    click.option("--evidence-dir", default=str(DEFAULT_EVIDENCE), show_default=True),
    click.option("--allow-coords", is_flag=True, help="Permit the coordinates fallback locator."),
    click.option("--json", "as_json", is_flag=True, help="Print the full result contract as JSON."),
    click.option("--run-name", default=None, help="Evidence folder name (default: replay-<timestamp>-<id>)."),
]


def _apply(opts):
    def deco(f):
        for o in reversed(opts):
            f = o(f)
        return f

    return deco


def _exit_for(status: ReplayStatus) -> int:
    return {ReplayStatus.SUCCESS: 0, ReplayStatus.BUSINESS_OUTCOME: 2}.get(status, 1)


@main.command()
@click.option("--artifact", "artifact_path", required=True, type=click.Path(exists=True, dir_okay=False))
@_apply(REPLAY_OPTS)
def replay(artifact_path, params, inject, confirm_risky, headed, operator, policy_path, evidence_dir, allow_coords, as_json, run_name) -> None:
    result = _run_replay(Path(artifact_path), _parse_params(params), inject=inject, confirm_risky=confirm_risky, headed=headed, operator=operator,
                         policy_path=policy_path, evidence_dir=evidence_dir, allow_coords=allow_coords, quiet=as_json, run_name=run_name)
    click.echo(json.dumps(result.model_dump(mode="json"), indent=2) if as_json else "\n" + result.brief())
    sys.exit(_exit_for(result.status))


@main.command()
@click.argument("capability")
@click.option("--artifacts-dir", default=str(DEFAULT_ARTIFACTS), show_default=True)
@_apply(REPLAY_OPTS)
def invoke(capability, artifacts_dir, params, inject, confirm_risky, headed, operator, policy_path, evidence_dir, allow_coords, as_json, run_name) -> None:
    path = Path(artifacts_dir) / f"{capability}.json"
    if not path.exists():
        raise click.ClickException(f"no capability '{capability}' in {artifacts_dir} (run `cua catalog`)")
    result = _run_replay(path, _parse_params(params), inject=inject, confirm_risky=confirm_risky, headed=headed, operator=operator,
                         policy_path=policy_path, evidence_dir=evidence_dir, allow_coords=allow_coords, quiet=True, run_name=run_name)
    click.echo(json.dumps(result.model_dump(mode="json"), indent=2))
    sys.exit(_exit_for(result.status))


@main.command()
@click.option("--artifacts-dir", default=str(DEFAULT_ARTIFACTS), show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit function-calling tool definitions.")
def catalog(artifacts_dir, as_json) -> None:
    arts = list_artifacts(artifacts_dir)
    if as_json:
        click.echo(json.dumps([a.tool_definition() for a in arts], indent=2))
        return
    if not arts:
        click.echo("no artifacts yet")
    for a in arts:
        ins = ", ".join(f"{p.name}:{p.type}" + ("(sensitive)" if p.sensitive else "") for p in a.inputs)
        outs = ", ".join(f"{o.name}:{o.type}" for o in a.outputs)
        biz = ", ".join(o.code for o in a.outcomes if o.kind == "business")
        risky = sum(1 for s in a.steps if s.risk == "risky")
        click.echo(f"{a.name} v{a.version} [{a.approval}]  inputs({ins}) -> outputs({outs})  steps={len(a.steps)} risky={risky}\n    {a.description}\n    business outcomes: {biz}")


@main.command()
@click.option("--artifact", "artifact_path", required=True, type=click.Path(exists=True, dir_okay=False))
def validate(artifact_path) -> None:
    a = load_artifact(artifact_path)
    click.echo(f"OK {a.name} v{a.version} schema={a.schema_version} steps={len(a.steps)} inputs={[p.name for p in a.inputs]} outputs={[o.name for o in a.outputs]} outcomes={len(a.outcomes)}")
    for s in a.steps:
        strategies = [c.strategy for c in s.target.candidates] if s.target else []
        click.echo(f"  {s.id} {s.action:8s} {s.description[:60]:60s} risk={s.risk} locators={strategies}")


@main.command()
@click.option("--artifact", "artifact_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--by", default=os.environ.get("USER", "reviewer"), show_default=True)
def approve(artifact_path, by) -> None:
    a = load_artifact(artifact_path)
    a.approval = "approved"
    a.notes.append(f"Approved by {by} on {__import__('datetime').datetime.now().isoformat(timespec='seconds')}.")
    save_artifact(a, artifact_path)
    click.echo(f"{a.name} v{a.version} -> approved")


if __name__ == "__main__":
    main()
