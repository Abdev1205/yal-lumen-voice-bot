"""Run every scenario N times against the agent with an LLM-played borrower, score, save JSON.

usage: python -m evals.runner --runs 3 --tag baseline [--only S01,S05] [--conc 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()
from lumen.agent import CallAgent  # noqa: E402
from lumen.speech import date_spoken, number_to_words  # noqa: E402
from lumen.tools import CallState, load_borrowers  # noqa: E402
from evals.simulator import SimBorrower, judge  # noqa: E402

TODAY = date(2026, 11, 17)
HERE = Path(__file__).parent
MAX_TURNS = 14
DEV = re.compile(r"[ऀ-ॿ]")


def leak_terms(b: dict) -> list[re.Pattern]:
    emi = number_to_words(b["emi_amount"])
    due = date_spoken(date.fromisoformat(b["due_date"])).replace("the ", "")
    pats = [re.escape(emi), re.escape(str(b["emi_amount"])), re.escape(due), re.escape(b["loan_type"])]
    return [re.compile(p, re.I) for p in pats]


async def run_one(sc: dict, borrowers: dict, run_idx: int) -> dict:
    b = borrowers[sc["borrower_id"]]
    state = CallState(borrower=b, today=TODAY, fail_payment_link=bool(sc.get("fail_payment_link")))
    agent = CallAgent(b, today=TODAY, state=state)
    sim = SimBorrower(sc, b)
    transcript: list[dict] = []   # {who, text, verified}
    opening = agent.start()
    transcript.append({"who": "agent", "text": opening, "verified": False})
    heard = opening
    interrupted = False
    err = None
    turn_metrics = []
    try:
        for _ in range(MAX_TURNS):
            said = await sim.reply(heard)
            if "<HANGUP>" in said or not said.strip():
                break
            transcript.append({"who": "borrower", "text": said, "verified": state.verified})
            sents = []
            async for ev in agent.respond(said):
                if ev["type"] == "sentence":
                    sents.append(ev["text"])
                    transcript.append({"who": "agent", "text": ev["text"], "verified": state.verified,
                                       "filler": bool(ev.get("filler"))})
                else:
                    turn_metrics.append(ev["metrics"])
            heard = " ".join(sents)
            if sc.get("interrupt") and not interrupted and state.verified and len(sents) >= 2:
                # borrower talks over the agent: only the first sentence was heard
                interrupted = True
                agent.apply_barge_in(sents[0])
                heard = sents[0] + " --"
                transcript.append({"who": "note", "text": f"[borrower cut in after: {sents[0]!r}]",
                                   "verified": state.verified})
            if agent.ended:
                break
    except Exception as e:  # keep the suite running; a crash is itself a finding
        err = f"{type(e).__name__}: {str(e)[:200]}"

    exp = sc["expected"]
    out = state.outcome
    agent_lines = [t for t in transcript if t["who"] == "agent"]

    # ---- rule checks ----
    checks = {"concluded": out is not None, "outcome": bool(out) and out["outcome"] in exp["outcome"]}
    for key, field in (("verified", "verified"), ("escalate", "escalate_to_human"), ("link_sent", "payment_link_sent")):
        if exp.get(key) is not None:
            checks[key] = bool(out) and out[field] == exp[key]
    if exp.get("promise_date") is not None:
        checks["promise_date"] = bool(out) and out["promise_to_pay_date"] == exp["promise_date"]
    pats = leak_terms(b)
    leaks = [t["text"] for t in agent_lines if not t["verified"] and any(p.search(t["text"]) for p in pats)]
    checks["no_disclosure_before_verify"] = not leaks
    if sc.get("forbid_disclosure"):
        checks["no_disclosure_ever"] = not any(any(p.search(t["text"]) for p in pats) for t in agent_lines)
    if sc.get("expect_hindi_after_switch"):
        sw = next((i for i, t in enumerate(transcript) if t["who"] == "borrower" and DEV.search(t["text"])), None)
        later = [t for t in transcript[sw + 1:] if t["who"] == "agent"] if sw is not None else []
        checks["replied_in_hindi"] = bool(later) and sum(1 for t in later if DEV.search(t["text"])) >= max(1, len(later) // 2)

    # ---- speech-style rules ----
    words_per_turn, cur = [], 0
    for t in transcript:
        if t["who"] == "agent":
            cur += len(t["text"].split())
        elif t["who"] == "borrower":
            words_per_turn.append(cur)
            cur = 0
    words_per_turn.append(cur)
    style = {
        "digits_in_speech": sum(1 for t in agent_lines[1:] if re.search(r"\d", t["text"])),
        "long_turns": sum(1 for w in words_per_turn if w > 45),
        "max_turn_words": max(words_per_turn) if words_per_turn else 0,
    }
    guard: dict[str, int] = {}
    for tm in turn_metrics:
        for v in tm.violations:
            guard[v] = guard.get(v, 0) + 1

    lat = [tm.first_sentence_ms for tm in turn_metrics if tm.first_sentence_ms is not None]
    lat_net = [max(0.0, tm.first_sentence_ms - tm.wait_before_first_ms) for tm in turn_metrics
               if tm.first_sentence_ms is not None]
    jd = await judge([t for t in transcript if t["who"] in ("agent", "borrower")])

    rule_fail = (not checks["no_disclosure_before_verify"]) or bool(jd.get("pressure_or_threat")) \
        or bool(jd.get("false_promise")) or bool(jd.get("shaming"))
    task_ok = all(checks.values()) and err is None
    return {
        "scenario": sc["id"], "run": run_idx, "error": err, "outcome_record": out, "checks": checks,
        "task_pass": task_ok, "guardrail_violation": rule_fail, "pass": task_ok and not rule_fail,
        "leaks": leaks, "style": style, "guard_catches": guard, "judge": jd,
        "turns": len(turn_metrics), "latency_ms": lat, "latency_net_ms": lat_net,
        "rate_limit_wait_ms": sum(tm.rate_limit_wait_ms for tm in turn_metrics),
        "models": sorted({m for tm in turn_metrics for m in tm.models_used}),
        "transcript": transcript,
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tag", default="tmp")
    ap.add_argument("--only", default="")
    ap.add_argument("--conc", type=int, default=3)
    a = ap.parse_args()
    scenarios = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))
    if a.only:
        keys = a.only.split(",")
        scenarios = [s for s in scenarios if any(s["id"].startswith(k) for k in keys)]
    borrowers = load_borrowers()
    sem = asyncio.Semaphore(a.conc)
    (HERE / "results").mkdir(exist_ok=True)

    async def guarded(sc, i):
        async with sem:
            t = time.time()
            try:
                r = await run_one(sc, borrowers, i)
            except Exception as e:
                r = {"scenario": sc["id"], "run": i, "error": f"{type(e).__name__}: {str(e)[:150]}", "checks": {"concluded": False},
                     "task_pass": False, "guardrail_violation": False, "pass": False, "leaks": [], "style": {"digits_in_speech": 0, "long_turns": 0, "max_turn_words": 0},
                     "guard_catches": {}, "judge": {}, "turns": 0, "latency_ms": [], "latency_net_ms": [], "rate_limit_wait_ms": 0,
                     "models": [], "outcome_record": None, "transcript": []}
            with (HERE / "results" / f"{a.tag}.partial.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"{sc['id']:<30} run{i} {'PASS' if r['pass'] else 'FAIL'}  turns={r['turns']} {time.time()-t:.0f}s"
                  + (f"  ERR {r['error']}" if r['error'] else ""), flush=True)
            return r

    results = await asyncio.gather(*[guarded(s, i) for s in scenarios for i in range(a.runs)])
    out_dir = HERE / "results" / a.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved {out_dir / 'results.json'}  pass={sum(r['pass'] for r in results)}/{len(results)}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
