"""Streaming EMI reminder agent (OpenAI-compatible chat API, e.g. Groq).

Text-turn interface, so the same core serves the browser voice demo and the eval simulator.
respond() yields events as soon as each SENTENCE is complete, so TTS starts on sentence one
while the model is still generating the rest.
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import AsyncIterator

from .guard import check_sentence
from openai import APIError, RateLimitError

from .llm import RateLimitClock, chat_with_failover, make_client, to_openai_tools
from .prompts import opening_line, system_prompt
from .tools import SLOW_TOOLS, TOOL_SCHEMAS, CallState, run_tool

MAX_TOOL_ROUNDS = 5
TOOLS = to_openai_tools(TOOL_SCHEMAS)

FILLERS = {
    "en": ["Sure, one moment.", "Alright, sending that over now.", "Of course, just a second."],
    "hi": ["ज़रूर, एक सेकंड।", "ठीक है, अभी भेज रही हूँ।"],
}
_SENT_END = re.compile(r"(?<=[.!?।])\s+")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Some models write a tool call as plain text ("record_outcome({...})", "[Calling record_outcome]{...}")
# instead of calling it. On a voice call that would be read aloud AND the action would not happen.
# The agent never says a tool name in natural speech, so any occurrence is a leak: hide it and execute it.
TEXT_CALL_START = re.compile(r"(?:\[\s*Calling\s+)?\b(verify_identity|get_loan_details|send_payment_link|record_outcome)\b")


def extract_text_calls(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (text without tool-call junk, [(tool_name, json_args_string)])."""
    calls, out, i = [], [], 0
    while True:
        m = TEXT_CALL_START.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        j = m.end()
        while j < len(text) and text[j] in " ]:\t\n(":
            j += 1
        args = "{}"
        if j < len(text) and text[j] == "{":
            depth, k = 0, j
            while k < len(text):
                depth += (text[k] == "{") - (text[k] == "}")
                k += 1
                if depth == 0:
                    break
            args = text[j:k]
            j = k
        while j < len(text) and text[j] in " )\t\n":
            j += 1
        calls.append((m.group(1), args))
        i = j
    return "".join(out).strip(), calls


def lang_of(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if _DEVANAGARI.match(c)) / len(letters) > 0.3:
        return "hi"
    return "en"


@dataclass
class TurnMetrics:
    first_sentence_ms: float | None = None   # user turn received -> first sentence ready for TTS
    total_ms: float = 0.0
    rate_limit_wait_ms: float = 0.0          # time lost to provider 429s (free tier), reported separately
    tool_calls: list[str] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    agent_text: str = ""


class CallAgent:
    def __init__(self, borrower: dict, today: date | None = None, client=None,
                 state: CallState | None = None):
        self.today = today or date.today()
        self.state = state or CallState(borrower=borrower, today=self.today)
        self.client = client or make_client()
        self.messages: list[dict] = [{"role": "system", "content": system_prompt(borrower, self.today)}]
        self.turns: list[TurnMetrics] = []
        self.ended = False
        self.last_lang = borrower["language"]

    # ---- call lifecycle -------------------------------------------------
    def start(self) -> str:
        line = opening_line(self.state.borrower)
        self.messages.append({"role": "assistant", "content": line})
        return line

    def apply_barge_in(self, spoken_so_far: str) -> None:
        """The caller cut in. Truncate our last utterance to what was actually heard."""
        for m in reversed(self.messages):
            if m["role"] == "assistant" and m.get("content"):
                m["content"] = (spoken_so_far.strip() + " [interrupted by caller]").strip()
                break

    # ---- one turn -------------------------------------------------------
    async def respond(self, user_text: str) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        tm = TurnMetrics()
        self.turns.append(tm)
        self._add_user(user_text)
        self.state.last_user_text = user_text
        self.state.user_texts.append(user_text)

        excluded: set[str] = set()   # models that returned a malformed tool call this turn
        retries = 0
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            buf, full = "", ""
            calls: dict[int, dict] = {}
            said_this_round = False
            filler_sent = False
            suppress = False
            used = None
            try:
                w0 = RateLimitClock.waited_s
                stream, used = await chat_with_failover(
                    self.client, messages=self.messages, tools=TOOLS, exclude=excluded,
                    temperature=0.4, max_tokens=300)
                tm.models_used.append(used)
                tm.rate_limit_wait_ms += (RateLimitClock.waited_s - w0) * 1000
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    for tc in d.tool_calls or []:
                        c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        c["id"] = tc.id or c["id"]
                        if tc.function:
                            c["name"] = tc.function.name or c["name"]
                            if c["name"] in SLOW_TOOLS and not said_this_round and not filler_sent:
                                # no dead air: speak the moment the model commits to a slow tool
                                filler_sent = True
                                fev = self._emit(random.choice(FILLERS[self.last_lang]), tm, t0, guard=False)
                                if fev:
                                    fev["filler"] = True
                                    said_this_round = True
                                    yield fev
                            c["args"] += tc.function.arguments or ""
                    if d.content:
                        full += d.content
                        if suppress:
                            continue
                        buf += d.content
                        m = TEXT_CALL_START.search(full)
                        if m:
                            suppress = True
                            # drop the tool-call text from what is about to be spoken
                            buf = buf[:max(0, len(buf) - (len(full) - m.start()))]
                        parts = _SENT_END.split(buf)
                        for sent in parts[:-1]:
                            ev = self._emit(sent, tm, t0)
                            if ev:
                                said_this_round = True
                                yield ev
                        buf = parts[-1]
                if buf.strip():
                    ev = self._emit(buf, tm, t0)
                    if ev:
                        said_this_round = True
                        yield ev

            except RateLimitError:
                raise
            except APIError as e:
                # e.g. Groq 'tool call validation failed': the model emitted a malformed tool call.
                # Retry the round on a different model instead of crashing the call.
                retries += 1
                tm.violations.append('malformed_tool_call')
                if retries > 3:
                    raise
                if used:
                    excluded.add(used)
                rounds -= 1
                continue
            if not calls:
                full, text_calls = extract_text_calls(full)
                for i, (name, args) in enumerate(text_calls):
                    calls[i] = {"id": f"txtcall_{len(self.messages)}_{i}", "name": name, "args": args}
                    tm.violations.append("text_tool_call")
            asst: dict = {"role": "assistant", "content": full or None}
            if calls:
                asst["tool_calls"] = [{"id": c["id"], "type": "function",
                                       "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                                      for _, c in sorted(calls.items())]
            self.messages.append(asst)
            if not calls:
                break

            for _, c in sorted(calls.items()):
                tm.tool_calls.append(c["name"])
                try:
                    args = json.loads(c["args"] or "{}")
                except json.JSONDecodeError:
                    res = {"error": "invalid JSON arguments; call the tool again with valid JSON"}
                    self.state.tool_log.append({"tool": c["name"], "args": c["args"], "result": res})
                else:
                    res = await run_tool(self.state, c["name"], args)
                self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res, ensure_ascii=False)})

        tm.total_ms = (time.perf_counter() - t0) * 1000
        if self.state.outcome is not None:
            self.ended = True
        yield {"type": "turn_done", "metrics": tm, "ended": self.ended}

    # ---- helpers --------------------------------------------------------
    def _add_user(self, text: str) -> None:
        # Merge consecutive user messages (e.g. after a barge-in) to keep the API happy.
        last = self.messages[-1]
        if last["role"] == "user":
            last["content"] += " " + text
        else:
            self.messages.append({"role": "user", "content": text})

    def _emit(self, sentence: str, tm: TurnMetrics, t0: float, guard: bool = True) -> dict | None:
        sentence = sentence.strip()
        if not sentence:
            return None
        if guard:
            sentence, violation = check_sentence(sentence, self.state.verified)
            if violation:
                tm.violations.append(violation)
        if tm.first_sentence_ms is None:
            tm.first_sentence_ms = (time.perf_counter() - t0) * 1000
        tm.agent_text += (" " if tm.agent_text else "") + sentence
        lang = lang_of(sentence)
        self.last_lang = lang
        return {"type": "sentence", "text": sentence, "lang": lang}
