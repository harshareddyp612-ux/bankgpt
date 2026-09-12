from cua.agent.recorder import _templatize, stable_text, url_pattern
import re


def test_templatize_longest_value_first():
    params = {"member_id": "12345", "nickname": "Vacation Fund 12345"}
    assert _templatize("Vacation Fund 12345 / 12345", params) == "{{nickname}} / {{member_id}}"


def test_url_pattern_generalizes_digits_but_not_words():
    pat = url_pattern("http://127.0.0.1:5055/teller/members/12345", {"member_id": "12345"})
    rx = re.compile(pat)
    assert rx.match("http://10.0.0.9:8080/teller/members/777")
    assert rx.match("https://tenant-b.example.com/teller/members/12345?x=1")
    assert not rx.match("http://127.0.0.1:5055/teller/members/search")


def test_url_pattern_generalizes_generated_ids():
    pat = url_pattern("http://h/teller/members/12345/subaccounts/confirm/CF1A2B3C4D", {"member_id": "12345"})
    assert re.match(pat, "http://h/teller/members/999/subaccounts/confirm/CF00FFEE11")


def test_stable_text_strips_run_specific_values():
    params = {"member_id": "12345"}
    assert stable_text("Sub-account S03 opened successfully for member 12345.", params, {"CF87FEC423"}) == "opened successfully for member {{member_id}}"
    assert stable_text("S01 Regular Savings Primary Savings $4,512.78 Open", params, {"$4,512.78"}) == "Regular Savings Primary Savings"
    assert stable_text("Confirmation Number CF87FEC423", params, {"CF87FEC423"}) == "Confirmation Number"
    assert stable_text("Member Inquiry", params, set()) == "Member Inquiry"
    assert stable_text("Posted 09/11/2026 at 14:05 - Transfer complete", params, set()) == "Transfer complete"


def test_stable_text_falls_back_when_nothing_stable():
    assert stable_text("$4,512.78", {}, set()) == "$4,512.78"
