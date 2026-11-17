"""Spoken-form helpers: numbers and dates the way a person says them, not read like a form."""
from __future__ import annotations

from datetime import date

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
        8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth",
        14: "fourteenth", 15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
        19: "nineteenth", 20: "twentieth", 21: "twenty first", 22: "twenty second",
        23: "twenty third", 24: "twenty fourth", 25: "twenty fifth", 26: "twenty sixth",
        27: "twenty seventh", 28: "twenty eighth", 29: "twenty ninth", 30: "thirtieth",
        31: "thirty first"}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, o = divmod(n, 10)
    return _TENS[t] + (f" {_ONES[o]}" if o else "")


def _below_thousand(n: int) -> str:
    h, rest = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_ONES[h]} hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def number_to_words(n: int) -> str:
    """Indian numbering: 125000 -> 'one lakh twenty five thousand'."""
    if n == 0:
        return "zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(f"{_below_hundred(crore)} crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def rupees_spoken(amount: int) -> str:
    return f"{number_to_words(amount)} rupees"


def date_spoken(d: date, today: date | None = None) -> str:
    """'the fifth of December'; adds the year only when it is not the current year."""
    out = f"the {_ORD[d.day]} of {_MONTHS[d.month - 1]}"
    if today and d.year != today.year:
        out += f", {d.year}"
    return out


def digits_spoken(s: str) -> str:
    return " ".join(_ONES[int(c)] for c in s if c.isdigit())
