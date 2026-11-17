"""Mock tools. The verification gate lives HERE, in code, not just in the prompt.

The LLM cannot see loan details until verify_identity has passed: get_loan_details
returns an error before that, so leakage is structurally impossible, not just unlikely.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .speech import date_spoken, rupees_spoken

DATA = Path(__file__).resolve().parent.parent / "data" / "borrowers.json"

OUTCOMES = [
    "will_pay", "already_paid", "promise_to_pay", "hardship", "dispute",
    "wrong_person", "callback_requested", "refused_to_engage", "no_verification",
    "language_unsupported", "stopped_by_borrower",
]


def load_borrowers() -> dict[str, dict]:
    return {b["id"]: b for b in json.loads(DATA.read_text(encoding="utf-8"))}


@dataclass
class CallState:
    borrower: dict
    today: date
    verified: bool = False
    verify_attempts: int = 0
    payment_link_sent: bool = False
    outcome: dict | None = None
    escalate: bool = False
    tool_log: list[dict] = field(default_factory=list)
    # eval knobs: simulate a slow or failing tool
    last_user_text: str = ""   # what the caller actually said this turn (grounding check)
    tool_delay: float = 0.0
    fail_payment_link: bool = False


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]


def _norm(text: str) -> str:
    """Lowercase, and turn spoken digits ('four eight two one') into digits."""
    return re.sub(r"\b(" + "|".join(_DIGIT_WORDS) + r")\b", lambda m: _DIGIT_WORDS[m.group(1)], text.lower())


def _plausible(answer: str) -> bool:
    """Looks like a DOB or last-4 answer at all (a name or 'yes' is not an attempt)."""
    a = _norm(answer)
    return len(_digits(a)) >= 4 or any(m in a for m in _MONTHS)


def _grounded(answer: str, user_text: str) -> bool:
    """The answer must come from the caller's own words, not from the model's imagination."""
    u, a = _norm(user_text), _norm(answer)
    ad = _digits(a)
    if ad:
        return ad in _digits(u)
    words = re.findall(r"[a-z]+", a)
    return bool(words) and all(w in u for w in words)


def verify_identity(state: CallState, answer: str = "") -> dict:
    """Verify using what the CALLER just said (state.last_user_text), never a model-supplied value.

    Found in evals: the model invents the answer (a made-up date of birth) and burns the attempts.
    So the tool ignores any argument and reads the caller's own words.
    """
    if state.verified:
        return {"verified": True}
    heard = _norm(state.last_user_text or "")
    if not _plausible(heard):
        # no date of birth or digits in what the caller said: ask again, this is NOT an attempt
        return {"not_provided": True, "status": "need_answer",
                "message": "The caller has not given a date of birth or last four digits yet. Ask for it. Not a failed attempt."}
    if state.verify_attempts >= 2:
        return {"verified": False, "locked": True,
                "message": "Too many attempts. Do not share any details. Politely end the call."}
    state.verify_attempts += 1
    b = state.borrower
    a = heard
    ok = False
    if _digits(a) == b["last4"]:
        ok = True
    else:
        y, m, d = b["dob"].split("-")
        month_name = date(int(y), int(m), 1).strftime("%B").lower()
        digits = _digits(a)
        if digits in (f"{d}{m}{y}", f"{y}{m}{d}"):
            ok = True
        elif month_name in a and y in a and re.search(rf"\b0?{int(d)}(st|nd|rd|th)?\b", a):
            ok = True
    state.verified = ok
    if ok:
        return {"verified": True}
    left = 2 - state.verify_attempts
    return {"verified": False, "attempts_left": left,
            "message": "That did not match. Do not hint what is correct."}


def get_loan_details(state: CallState) -> dict:
    if not state.verified:
        return {"error": "not_verified",
                "message": "Identity not verified. You may not share or discuss any loan details."}
    b = state.borrower
    overdue = b["days_overdue"]
    due = date.fromisoformat(b["due_date"])
    return {
        "loan_type": b["loan_type"],
        "emi_amount_spoken": rupees_spoken(b["emi_amount"]),
        "due_date_spoken": date_spoken(due, state.today),
        "status": "overdue" if overdue else "upcoming",
        "days_overdue_spoken": f"{overdue} days" if overdue > 1 else ("one day" if overdue == 1 else None),
    }


async def send_payment_link(state: CallState) -> dict:
    if not state.verified:
        return {"error": "not_verified"}
    await asyncio.sleep(state.tool_delay)
    if state.fail_payment_link:
        return {"error": "service_unavailable",
                "message": "Link service is down. Tell the borrower, offer to try later or escalate."}
    state.payment_link_sent = True
    return {"sent": True, "channel": "SMS to the registered mobile number",
            "link": f"https://pay.lumenfinance.example/{state.borrower['id']}"}


def record_outcome(state: CallState, outcome: str, notes: str = "",
                   promise_to_pay_date: str | None = None,
                   escalate_to_human: bool = False) -> dict:
    if outcome not in OUTCOMES:
        return {"error": f"outcome must be one of {OUTCOMES}"}
    if promise_to_pay_date:
        try:
            pd = date.fromisoformat(promise_to_pay_date)
        except ValueError:
            return {"error": "promise_to_pay_date must be YYYY-MM-DD"}
        if pd < state.today:
            return {"error": f"promise_to_pay_date is in the past (today is {state.today})"}
    # idempotent: first record wins; a retry returns the same result
    if state.outcome is None:
        state.outcome = {
            "borrower_id": state.borrower["id"],
            "verified": state.verified,
            "outcome": outcome,
            "promise_to_pay_date": promise_to_pay_date,
            "payment_link_sent": state.payment_link_sent,
            "escalate_to_human": escalate_to_human,
            "notes": notes,
        }
    return {"recorded": True}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "verify_identity",
     "description": "Verify the caller from what they JUST said (date of birth or last 4 digits). Call only after they have stated one. Takes no arguments.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_loan_details",
     "description": "EMI amount and due date in spoken form. Only after verification.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_payment_link",
     "description": "SMS a payment link to the registered number. Only after verification.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "record_outcome",
     "description": "Record the call outcome. Exactly once, right before goodbye.",
     "input_schema": {"type": "object", "properties": {
         "outcome": {"type": "string", "enum": OUTCOMES},
         "promise_to_pay_date": {"type": "string", "description": "YYYY-MM-DD the borrower promised to pay; omit otherwise"},
         "escalate_to_human": {"type": "boolean"},
         "notes": {"type": "string", "description": "One short factual sentence"}},
         "required": ["outcome", "escalate_to_human", "notes"]}},
]

# Tools slow enough to deserve a spoken filler first.
SLOW_TOOLS = {"send_payment_link"}


async def run_tool(state: CallState, name: str, args: dict) -> dict:
    if name == "verify_identity":
        res = verify_identity(state)
    elif name == "get_loan_details":
        res = get_loan_details(state)
    elif name == "send_payment_link":
        res = await send_payment_link(state)
    elif name == "record_outcome":
        res = record_outcome(state, args.get("outcome", ""), args.get("notes", ""),
                             args.get("promise_to_pay_date"), bool(args.get("escalate_to_human")))
    else:
        res = {"error": f"unknown tool {name}"}
    state.tool_log.append({"tool": name, "args": args, "result": res})
    return res
