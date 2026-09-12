from __future__ import annotations

from copy import deepcopy

MEMBERS: dict[str, dict] = {
    "12345": {
        "member_number": "12345",
        "name": "Dana Whitfield",
        "ssn_last4": "4821",
        "dob": "1984-03-17",
        "address": "410 Elm Street, Springfield, IL 62704",
        "phone": "(217) 555-0142",
        "status": "Active",
        "branch": "Main Street Branch",
        "accounts": [
            {"suffix": "S01", "type": "Regular Savings", "nickname": "Primary Savings", "balance": 4512.78, "status": "Open"},
            {"suffix": "S02", "type": "Share Draft Checking", "nickname": "Everyday Checking", "balance": 1287.40, "status": "Open"},
        ],
    },
    "20481": {
        "member_number": "20481",
        "name": "Marcus Oyelaran",
        "ssn_last4": "1190",
        "dob": "1976-11-02",
        "address": "88 Harbor View Rd, Portland, ME 04101",
        "phone": "(207) 555-0199",
        "status": "Active",
        "branch": "Harbor Branch",
        "accounts": [
            {"suffix": "S01", "type": "Regular Savings", "nickname": "Savings", "balance": 15230.00, "status": "Open"},
        ],
    },
    "30977": {
        "member_number": "30977",
        "name": "Priya Ramaswamy",
        "ssn_last4": "7734",
        "dob": "1991-06-25",
        "address": "1902 Cedar Loop, Austin, TX 78745",
        "phone": "(512) 555-0117",
        "status": "Restricted",
        "branch": "South Congress Branch",
        "accounts": [
            {"suffix": "S01", "type": "Regular Savings", "nickname": "Savings", "balance": 92.15, "status": "Open"},
            {"suffix": "S05", "type": "Money Market", "nickname": "Rainy Day", "balance": 8000.00, "status": "Open"},
        ],
    },
}

PRODUCTS = [
    ("SAV", "Regular Savings"),
    ("CHK", "Share Draft Checking"),
    ("MMK", "Money Market"),
    ("CLB", "Holiday Club"),
]


def fresh_state() -> dict[str, dict]:
    return deepcopy(MEMBERS)
