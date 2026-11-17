"""Output guard: last line of defence on every sentence BEFORE it is spoken."""
from __future__ import annotations

import re

THREAT = re.compile(
    r"legal action|court|police|arrest|blacklist|cibil|credit (score|rating)|seiz|recovery agent|"
    r"\bjail\b|your family will|tell your (family|employer|neighbou?rs)|everyone will know|"
    r"कानूनी|पुलिस|कोर्ट", re.I)
# only affirmative promises; "I cannot waive" must not trip this
PROMISE = re.compile(
    r"\b(i|we)(\s+will|'ll|\s+can|\s+could|\s+shall)\s+(\w+\s+){0,3}"
    r"(waive|reduce|restructure|reverse|discount|extend|remove the (late )?(fee|charge|penalty))", re.I)
PRE_VERIFY_LEAK = re.compile(r"₹|rupee|\bemi\b|\bloan\b|\d{3,}|lakh|thousand|overdue|due date|due on|ईएमआई|लोन|रुपये", re.I)

FALLBACK_VERIFY = "Sorry about that. Could you confirm your date of birth, or the last four digits of your registered number?"
FALLBACK_SAFE = "I understand. Let me have a colleague follow up with you on that."


def check_sentence(text: str, verified: bool) -> tuple[str, str | None]:
    """Return (text_to_speak, violation_or_None)."""
    if THREAT.search(text):
        return FALLBACK_SAFE, "threat"
    if PROMISE.search(text):
        return FALLBACK_SAFE, "false_promise"
    if not verified and PRE_VERIFY_LEAK.search(text):
        return FALLBACK_VERIFY, "pre_verify_leak"
    return text, None
