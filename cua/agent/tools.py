from __future__ import annotations


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
    }


REASON = {"type": "string", "description": "One sentence: why this action moves toward the goal."}
IDX = {"type": "integer", "description": "Index of the element from the current element list."}

TOOLS: list[dict] = [
    _tool("click", "Click an interactive element (link, button, checkbox).", {"element_index": IDX, "reason": REASON}, ["element_index", "reason"]),
    _tool(
        "type_text",
        "Replace the contents of a text field with `text`. For parameters, type the token exactly as given (e.g. {{member_id}}); "
        "the system substitutes the real value locally and it never enters this conversation.",
        {
            "element_index": IDX,
            "text": {"type": "string", "description": "Text or parameter token to type."},
            "press_enter": {"type": "boolean", "description": "Press Enter after typing (submits most forms)."},
            "reason": REASON,
        },
        ["element_index", "text", "press_enter", "reason"],
    ),
    _tool(
        "select_option",
        "Choose an option in a dropdown (select) by its visible label.",
        {"element_index": IDX, "option_label": {"type": "string"}, "reason": REASON},
        ["element_index", "option_label", "reason"],
    ),
    _tool("press_key", "Press a keyboard key (Enter, Escape, Tab, PageDown).", {"key": {"type": "string"}, "reason": REASON}, ["key", "reason"]),
    _tool("navigate", "Load a URL within the allowed application.", {"url": {"type": "string"}, "reason": REASON}, ["url", "reason"]),
    _tool("scroll", "Scroll the page to reveal more elements.", {"direction": {"type": "string", "enum": ["up", "down"]}, "reason": REASON}, ["direction", "reason"]),
    _tool("wait", "Wait for the page to settle (1-5 seconds) when it is visibly loading.", {"seconds": {"type": "integer", "description": "How long to wait, 1 to 5 seconds."}, "reason": REASON}, ["seconds", "reason"]),
    _tool(
        "extract",
        "Record a value the goal asks you to read. Point at the element that contains exactly that value. "
        "Call once per requested value; each becomes a typed output of the recorded capability.",
        {
            "element_index": IDX,
            "output_name": {"type": "string", "description": "snake_case name for this output, e.g. savings_balance"},
            "observed_value": {"type": "string", "description": "The value as you read it on screen."},
            "reason": REASON,
        },
        ["element_index", "output_name", "observed_value", "reason"],
    ),
    _tool(
        "done",
        "Declare the goal achieved. Only call this when the screen proves it (and every requested value has been extracted).",
        {
            "summary": {"type": "string", "description": "What was accomplished, one or two sentences."},
            "success_text": {
                "type": "string",
                "description": "Short STATIC text visible on the current screen that proves success and will be identical on every future run: "
                "a heading, label or message such as 'Sub-Account Opened' or 'Member Inquiry'. Never include balances, amounts, dates, "
                "account/suffix numbers, confirmation numbers or names.",
            },
        },
        ["summary", "success_text"],
    ),
    _tool(
        "request_help",
        "Hand the live session to a human operator when you cannot safely proceed (blocked, credentials needed, ambiguous risky choice).",
        {"reason": {"type": "string", "description": "What is blocking you and what the operator should do."}},
        ["reason"],
    ),
]

TOOL_NAMES = {t["name"] for t in TOOLS}
