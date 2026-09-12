from __future__ import annotations

import re

from ..surface.base import ElementInfo, Observation
from .llm import Decision, TurnContext, _guard


class MockLLM:
    name = "mock:rule-based"

    def __init__(self) -> None:
        _guard()
        self.calls = 0

    @staticmethod
    def _find(obs: Observation, **kw) -> ElementInfo | None:
        for e in obs.elements:
            ok = True
            for k, v in kw.items():
                if k == "name_rx":
                    ok &= bool(re.search(v, e.name or e.text or ""))
                elif k == "attr_name":
                    ok &= e.attrs.get("name") == v
                elif k == "row":
                    ok &= bool(e.table_cell and e.table_cell.get("row_anchor") == v)
                elif k == "col":
                    ok &= bool(e.table_cell and e.table_cell.get("header") == v)
                else:
                    ok &= getattr(e, k, None) == v
            if ok:
                return e
        return None

    def _did(self, ctx: TurnContext, tool: str, **match) -> bool:
        for h in ctx.history_structured:
            if h.get("tool") == tool and h.get("ok") and all(str(h.get("args", {}).get(k)) == str(v) for k, v in match.items()):
                return True
        return False

    def decide(self, system: str, ctx: TurnContext, tools: list[dict]) -> Decision:
        _guard()
        self.calls += 1
        obs, goal = ctx.obs, ctx.goal.lower()
        d = lambda tool, **args: Decision(tool=tool, args=args, text="(mock rule fired)", model=self.name)

        if obs.overlays:
            btn = self._find(obs, in_overlay=True, role="button")
            if btn:
                return d("click", element_index=btn.index, reason="Dismiss the notice covering the screen.")
        text = obs.page_text
        if "Session Expired" in text or "Access denied" in text:
            return d("request_help", reason="The screen requires operator credentials or elevated access.")

        wants_balance = "balance" in goal
        wants_open = "sub-account" in goal or "subaccount" in goal or "open" in goal and "account" in goal

        if obs.url.endswith("/teller/members/search"):
            if "No member found" in text or "Invalid member number" in text:
                return d("request_help", reason="The member number was not found; a human should verify it.")
            box = self._find(obs, attr_name="acct_q")
            btn = self._find(obs, role="button", name="Search")
            if box is None or btn is None:
                return d("request_help", reason="Cannot find the member lookup controls.")
            if box.attrs.get("value") != ctx.params.get("member_id"):
                return d("type_text", element_index=box.index, text="{{member_id}}", press_enter=False, reason="Enter the member number to look up.")
            return d("click", element_index=btn.index, reason="Run the member lookup.")

        if re.search(r"/teller/members/\d+$", obs.url):
            if wants_balance:
                if not self._did(ctx, "extract", output_name="savings_balance"):
                    cell = self._find(obs, row="S01", col="Current Balance")
                    if cell:
                        return d("extract", element_index=cell.index, output_name="savings_balance", observed_value=cell.text, reason="This cell holds the regular savings (S01) current balance.")
                    return d("request_help", reason="Cannot find the savings balance cell.")
                return d("done", summary="Looked up the member and read the S01 regular savings current balance.", success_text="Member Inquiry")
            if wants_open:
                link = self._find(obs, role="link", name="Open Sub-Account")
                if link:
                    return d("click", element_index=link.index, reason="Open the new sub-account form for this member.")

        if obs.url.endswith("/subaccounts/new"):
            if "Error: The request could not be processed" in text:
                return d("request_help", reason="The form was rejected; a human should review the inputs.")
            prod = self._find(obs, attr_name="prod_cd")
            nick = self._find(obs, attr_name="acct_nick")
            dep = self._find(obs, attr_name="init_dep")
            btn = self._find(obs, role="button", name="Open Sub-Account")
            if prod and not self._did(ctx, "select_option", element_index=prod.index):
                return d("select_option", element_index=prod.index, option_label=ctx.params.get("product", "Regular Savings"), reason="Choose the product type.")
            if nick and nick.attrs.get("value") != ctx.params.get("nickname"):
                return d("type_text", element_index=nick.index, text="{{nickname}}", press_enter=False, reason="Enter the account nickname.")
            if dep and dep.attrs.get("value") != ctx.params.get("deposit"):
                return d("type_text", element_index=dep.index, text="{{deposit}}", press_enter=False, reason="Enter the initial deposit.")
            if btn:
                return d("click", element_index=btn.index, reason="Submit the new sub-account request.")

        if "/subaccounts/confirm/" in obs.url:
            if not self._did(ctx, "extract", output_name="confirmation_number"):
                cell = self._find(obs, row="Confirmation Number")
                if cell:
                    return d("extract", element_index=cell.index, output_name="confirmation_number", observed_value=cell.text, reason="Record the confirmation number.")
            if not self._did(ctx, "extract", output_name="new_suffix"):
                cell = self._find(obs, row="New Suffix")
                if cell:
                    return d("extract", element_index=cell.index, output_name="new_suffix", observed_value=cell.text, reason="Record the new account suffix.")
            return d("done", summary="Opened the sub-account and reached the confirmation screen.", success_text="opened successfully")

        return d("request_help", reason="Mock agent has no rule for this screen.")
