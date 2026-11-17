import asyncio
from datetime import date

from lumen.guard import check_sentence
from lumen.speech import date_spoken, number_to_words, rupees_spoken
from lumen.tools import CallState, load_borrowers, run_tool

TODAY = date(2026, 11, 17)


def st(bid="B001"):
    return CallState(borrower=load_borrowers()[bid], today=TODAY)


def run(coro):
    return asyncio.run(coro)


def test_numbers():
    assert rupees_spoken(12500) == "twelve thousand five hundred rupees"
    assert number_to_words(38400) == "thirty eight thousand four hundred"
    assert number_to_words(125000) == "one lakh twenty five thousand"
    assert number_to_words(2999) == "two thousand nine hundred ninety nine"


def test_dates():
    assert date_spoken(date(2026, 12, 5), TODAY) == "the fifth of December"
    assert date_spoken(date(2027, 1, 3), TODAY) == "the third of January, 2027"


def test_gate_blocks_before_verify():
    s = st()
    assert run(run_tool(s, "get_loan_details", {}))["error"] == "not_verified"
    assert run(run_tool(s, "send_payment_link", {}))["error"] == "not_verified"


def _v(s, ans):
    s.last_user_text = ans
    return run(run_tool(s, "verify_identity", {"answer": ans}))


def test_verify_variants():
    for ans in ["4821", "four eight two one", "14 March 1990", "14/03/1990", "14th march 1990"]:
        s = st()
        assert _v(s, ans)["verified"] is True, ans


def test_lockout_after_two_failures():
    s = st()
    _v(s, "1111")
    _v(s, "2222")
    r = _v(s, "4821")
    assert r["verified"] is False and r.get("locked")


def test_past_date_rejected_and_idempotent():
    s = st()
    s.verified = True
    assert "error" in run(run_tool(s, "record_outcome", {"outcome": "promise_to_pay", "promise_to_pay_date": "2026-11-01", "escalate_to_human": False}))
    run(run_tool(s, "record_outcome", {"outcome": "will_pay", "escalate_to_human": False, "notes": "a"}))
    run(run_tool(s, "record_outcome", {"outcome": "hardship", "escalate_to_human": True, "notes": "b"}))
    assert s.outcome["outcome"] == "will_pay"


def test_guard():
    assert check_sentence("Your EMI of twelve thousand rupees is due.", verified=False)[1] == "pre_verify_leak"
    assert check_sentence("Your EMI of twelve thousand rupees is due.", verified=True)[1] is None
    assert check_sentence("We may take legal action.", True)[1] == "threat"
    assert check_sentence("I can waive the late fee.", True)[1] == "false_promise"
    assert check_sentence("I cannot waive anything, but a colleague can talk to you.", True)[1] is None


def test_invented_answer_rejected_and_free():
    s = st()
    s.last_user_text = "yes speaking"
    r = run(run_tool(s, "verify_identity", {"answer": "27 April 1985"}))
    assert r.get("not_provided") and s.verify_attempts == 0


def test_name_or_yes_is_not_an_attempt():
    s = st()
    s.last_user_text = "yes this is Rahul"
    for guess in ["Rahul", "yes", "this is Rahul"]:
        r = run(run_tool(s, "verify_identity", {"answer": guess}))
        assert r.get("not_provided"), guess
    assert s.verify_attempts == 0


def test_last_four_inside_a_sentence():
    # found in evals: the word "four" in "the last four" must not be read as a digit
    s = st("B007")
    r = _v(s, "What the hell do you want with my DOB? If you just need the last four, it's 3364.")
    assert r["verified"] is True


def test_relative_date_checked_against_callers_words():
    s = st()
    s.verified = True
    s.user_texts = ["I'll pay this coming Friday"]
    bad = run(run_tool(s, "record_outcome", {"outcome": "promise_to_pay", "promise_to_pay_date": "2026-11-24", "escalate_to_human": False, "notes": "x"}))
    assert "2026-11-20" in bad["error"]
    ok = run(run_tool(s, "record_outcome", {"outcome": "promise_to_pay", "promise_to_pay_date": "2026-11-20", "escalate_to_human": False, "notes": "x"}))
    assert ok["promise_date_spoken"] == "Friday, the twentieth of November"


def test_tomorrow_and_day_of_month():
    s = st()
    s.user_texts = ["kal tak pay kar dunga, I mean tomorrow"]
    assert "2026-11-18" in run(run_tool(s, "record_outcome", {"outcome": "will_pay", "promise_to_pay_date": "2026-11-30", "escalate_to_human": False, "notes": "x"}))["error"]
    s2 = st()
    s2.user_texts = ["I will pay on the 28th of November"]
    assert "error" in run(run_tool(s2, "record_outcome", {"outcome": "will_pay", "promise_to_pay_date": "2026-11-27", "escalate_to_human": False, "notes": "x"}))
    assert "recorded" in run(run_tool(s2, "record_outcome", {"outcome": "will_pay", "promise_to_pay_date": "2026-11-28", "escalate_to_human": False, "notes": "x"}))


def test_text_tool_calls_are_extracted():
    from lumen.agent import extract_text_calls
    t, calls = extract_text_calls('Thank you. [Calling record_outcome]{"outcome": "will_pay", "notes": "a {b}"} Goodbye.')
    assert calls[0][0] == "record_outcome" and '"will_pay"' in calls[0][1]
    assert "record_outcome" not in t and "will_pay" not in t and "Goodbye" in t
    t2, c2 = extract_text_calls('Sure. record_outcome({"outcome": "dispute"}) Bye.')
    assert c2 and "record_outcome" not in t2
