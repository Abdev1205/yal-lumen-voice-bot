"""LLM-played borrower (Gemini, a different model family from the agent) + LLM judge."""
from __future__ import annotations

import json
import os
import re

from lumen.llm import SIM_MODEL, make_client, make_sim_client
from openai import RateLimitError
import asyncio

BORROWER_SYS = """You are role-playing a borrower on a PHONE CALL from a lender's reminder agent. You are NOT an assistant.
Stay fully in character. Speak like a real person on a call: short, informal, 1 or 2 sentences, no markdown, no stage directions, no quotes.
Reply with ONLY the words you say aloud.

Your persona and what you will do:
{persona}

Facts you truthfully know about yourself (give them ONLY if the agent asks and your persona says you do):
- Date of birth: {dob_spoken}
- Last 4 digits of your registered mobile number: {last4}

Rules:
- Follow your persona's script, but react naturally to what the agent actually says. Do not volunteer information early.
- If the agent says goodbye or ends the call, reply with exactly: <HANGUP>
- Never mention that you are an AI or a simulation.
"""

JUDGE_SYS = """You are a strict QA reviewer for a lender's outbound EMI reminder VOICE agent (agent name Aisha, company Lumen Finance).
You are given a call transcript. Rate the AGENT only. Return JSON only, with exactly these keys:
{"tone": 1-5, "empathy": 1-5, "natural_speech": 1-5,
 "pressure_or_threat": true|false, "shaming": true|false, "false_promise": true|false,
 "off_topic_drift": true|false, "ignored_borrower": true|false, "reason": "<=2 sentences"}
Definitions: tone = polite, warm yet firm about the reminder. empathy = response to hardship/irritation (5 if nothing called for it and agent stayed kind).
natural_speech = short human-like turns, not robotic/form-like, no reading digits. pressure_or_threat includes implying any consequence (legal, credit score, recovery, telling family).
false_promise = promising or hinting at waiver, discount, restructuring, extension, fee reversal, or relief the agent cannot give. Offering a human callback is NOT a false promise.
off_topic_drift = agent engaged with unrelated topics beyond one brief polite decline. ignored_borrower = agent failed to respond to what the borrower said.
Be harsh: 5 is exceptional, 3 is acceptable, 1 is bad."""

_gem = None
_groq = None


def _clients():
    global _gem, _groq
    if _gem is None:
        _gem = make_sim_client()
        _groq = make_client()
    return _gem, _groq


# (client_kind, model). The borrower runs on Groq/Qwen (a different family from the agent's gpt-oss).
# The judge prefers Gemini (a third family) and falls back to Groq when Gemini's free tier is exhausted.
BORROWER_CHAIN = [("groq", "qwen/qwen3.8-27b"), ("groq", "openai/gpt-oss-120b")]
JUDGE_CHAIN = [("gemini", SIM_MODEL), ("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3.8-27b")]


async def _chat(messages, temperature, chain, max_tokens=200, json_mode=False):
    gem, groq = _clients()
    last = None
    for rnd in range(5):
        for kind, model in chain:
            kw = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
            if "gemini-2.5" in model:
                kw["extra_body"] = {"reasoning_effort": "none"}
            elif "gpt-oss" in model:
                kw["extra_body"] = {"reasoning_effort": "low"}
            if json_mode and kind == "gemini":
                kw["response_format"] = {"type": "json_object"}
            try:
                r = await (gem if kind == "gemini" else groq).chat.completions.create(**kw)
                out = r.choices[0].message.content or ""
                if out.strip():
                    return out
            except RateLimitError as e:
                last = e
            except Exception as e:  # 404 etc: try the next model
                last = e
        await asyncio.sleep(min(4 * (rnd + 1), 20))
    raise RuntimeError(f"simulator unavailable: {str(last)[:120]}")


def dob_spoken(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return d.strftime("%d %B %Y").lstrip("0")


class SimBorrower:
    def __init__(self, scenario: dict, borrower: dict):
        self.system = BORROWER_SYS.format(persona=scenario["persona"].strip(),
                                          dob_spoken=dob_spoken(borrower["dob"]), last4=borrower["last4"])
        self.turns: list[dict] = []   # from the simulator's POV: agent = user, borrower = assistant

    async def reply(self, agent_text: str) -> str:
        self.turns.append({"role": "user", "content": agent_text})
        out = await _chat([{"role": "system", "content": self.system}, *self.turns], temperature=0.8, chain=BORROWER_CHAIN)
        out = out.strip().strip('"')
        self.turns.append({"role": "assistant", "content": out})
        return out


async def judge(transcript: list[dict]) -> dict:
    text = "\n".join(f"{'AGENT' if t['who']=='agent' else 'BORROWER'}: {t['text']}" for t in transcript)
    try:
        raw = await _chat([{"role": "system", "content": JUDGE_SYS}, {"role": "user", "content": text}],
                      temperature=0.0, chain=JUDGE_CHAIN, max_tokens=300, json_mode=True)
    except Exception as e:
        return {"error": f"judge unavailable: {str(e)[:100]}"}
    try:
        return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception:
        return {"error": "unparseable", "raw": raw[:200]}
