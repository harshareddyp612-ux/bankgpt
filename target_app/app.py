from __future__ import annotations

import itertools
import re
import time
import uuid

from flask import Flask, abort, make_response, redirect, render_template, request, url_for

from .data import PRODUCTS, fresh_state

CHAOS_MODES = {
    "slow": "Next member page takes ~4s to render (transient slowness).",
    "dialog": "Next member page shows a 'System Notice' modal that must be dismissed.",
    "timeout": "Next member page returns a 'Session expired' screen requiring re-authentication.",
    "denied": "Next member page returns 403 'Access denied'.",
    "error500": "Next member page returns a 500 application error.",
    "sticky_dialog": "Every member page shows the 'System Notice' modal (does not self-clear).",
}


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "mock-app-not-a-secret"
    state = {"members": fresh_state(), "confirmations": {}, "suffix_counter": itertools.count(3)}

    def _consume_chaos() -> str | None:
        mode = request.cookies.get("chaos")
        if not mode:
            return None
        return mode

    def _clear_chaos_on(resp, mode: str):
        if mode != "sticky_dialog":
            resp.set_cookie("chaos", "", expires=0)
        return resp

    def _apply_chaos_to_member_page(render):
        mode = _consume_chaos()
        if mode is None:
            return render(dialog=False)
        if mode == "slow":
            time.sleep(4)
            return _clear_chaos_on(make_response(render(dialog=False)), mode)
        if mode in ("dialog", "sticky_dialog"):
            return _clear_chaos_on(make_response(render(dialog=True)), mode)
        if mode == "timeout":
            resp = make_response(render_template("session_expired.html", next_url=request.full_path.rstrip("?")), 200)
            return _clear_chaos_on(resp, mode)
        if mode == "denied":
            resp = make_response(render_template("denied.html"), 403)
            return _clear_chaos_on(resp, mode)
        if mode == "error500":
            resp = make_response(render_template("error500.html", ref=uuid.uuid4().hex[:10].upper()), 500)
            return _clear_chaos_on(resp, mode)
        return render(dialog=False)

    def _money(v: float) -> str:
        return "${:,.2f}".format(v)

    app.jinja_env.filters["money"] = _money

    @app.get("/")
    def home():
        return redirect(url_for("member_search"))

    @app.get("/__chaos/<mode>")
    def set_chaos(mode: str):
        if mode == "clear":
            resp = make_response("chaos cleared")
            resp.set_cookie("chaos", "", expires=0)
            return resp
        if mode not in CHAOS_MODES:
            abort(404)
        resp = make_response(f"chaos mode set: {mode} - {CHAOS_MODES[mode]}")
        resp.set_cookie("chaos", mode)
        return resp

    @app.get("/__health")
    def health():
        return {"ok": True}

    @app.route("/teller/members/search", methods=["GET", "POST"])
    def member_search():
        message = None
        query = ""
        if request.method == "POST":
            query = (request.form.get("acct_q") or "").strip()
            if not query:
                message = ("warn", "Please enter a member number to search.")
            elif not re.fullmatch(r"\d{3,8}", query):
                message = ("warn", f"Invalid member number format: '{query}'. Member numbers are 3-8 digits.")
            elif query in state["members"]:
                return redirect(url_for("member_detail", member_number=query))
            else:
                message = ("info", f"No member found matching {query}. Verify the member number and try again.")
        return render_template("search.html", message=message, query=query)

    @app.get("/teller/members/<member_number>")
    def member_detail(member_number: str):
        member = state["members"].get(member_number)
        if member is None:
            abort(404)

        def render(dialog: bool):
            return render_template("member.html", m=member, dialog=dialog, products=PRODUCTS)

        return _apply_chaos_to_member_page(render)

    @app.post("/teller/session/reauth")
    def reauth():
        user = (request.form.get("op_user") or "").strip()
        pw = request.form.get("op_pass") or ""
        next_url = request.form.get("next_url") or url_for("member_search")
        if user == "teller1" and pw == "demo-pass":
            return redirect(next_url)
        return render_template("session_expired.html", next_url=next_url, error="Invalid operator credentials."), 200

    @app.route("/teller/members/<member_number>/subaccounts/new", methods=["GET", "POST"])
    def subaccount_new(member_number: str):
        member = state["members"].get(member_number)
        if member is None:
            abort(404)
        errors: list[str] = []
        form = {"product": "", "nickname": "", "deposit": ""}
        if request.method == "POST":
            form["product"] = request.form.get("prod_cd") or ""
            form["nickname"] = (request.form.get("acct_nick") or "").strip()
            form["deposit"] = (request.form.get("init_dep") or "").strip()
            if form["product"] not in dict(PRODUCTS):
                errors.append("Select a product type.")
            if not form["nickname"]:
                errors.append("Account nickname is required.")
            try:
                dep = float(form["deposit"].replace(",", "").replace("$", ""))
                if dep < 25:
                    errors.append("Initial deposit must be at least $25.00.")
            except ValueError:
                errors.append("Initial deposit must be a dollar amount.")
                dep = 0.0
            if member.get("status") == "Restricted":
                errors.append("Member is in Restricted status; new sub-accounts require supervisor override.")
            if not errors:
                suffix = f"S{next(state['suffix_counter']):02d}"
                member["accounts"].append(
                    {
                        "suffix": suffix,
                        "type": dict(PRODUCTS)[form["product"]],
                        "nickname": form["nickname"],
                        "balance": dep,
                        "status": "Open",
                    }
                )
                conf = "CF" + uuid.uuid4().hex[:8].upper()
                state["confirmations"][conf] = {"member": member_number, "suffix": suffix, "deposit": dep}
                return redirect(url_for("subaccount_confirm", member_number=member_number, conf=conf))
        return render_template("subaccount_new.html", m=member, products=PRODUCTS, errors=errors, form=form)

    @app.get("/teller/members/<member_number>/subaccounts/confirm/<conf>")
    def subaccount_confirm(member_number: str, conf: str):
        member = state["members"].get(member_number)
        rec = state["confirmations"].get(conf)
        if member is None or rec is None:
            abort(404)
        return render_template("subaccount_confirm.html", m=member, conf=conf, rec=rec)

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("notfound.html"), 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)
