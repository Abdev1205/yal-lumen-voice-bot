"""Summarise eval results.  usage: python -m evals.report <tag> [--show S01,S05] [--failures]"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def load(tag):
    return json.loads((HERE / "results" / tag / "results.json").read_text(encoding="utf-8"))


def summary(rs: list[dict]) -> dict:
    n = len(rs)
    lat = [x for r in rs for x in r["latency_ms"]]
    net = [x for r in rs for x in r["latency_net_ms"]]
    j = [r["judge"] for r in rs if "tone" in r["judge"]]
    mean = lambda k: statistics.mean(x[k] for x in j) if j else float("nan")
    guard = defaultdict(int)
    for r in rs:
        for k, v in r["guard_catches"].items():
            guard[k] += v
    def rate(key):
        applicable = [r for r in rs if key in r["checks"]]
        return (sum(r["checks"][key] for r in applicable), len(applicable))
    return {
        "runs": n,
        "pass": sum(r["pass"] for r in rs),
        "task_pass": sum(r["task_pass"] for r in rs),
        "outcome": rate("outcome"), "verified": rate("verified"), "escalate": rate("escalate"),
        "promise_date": rate("promise_date"), "link_sent": rate("link_sent"),
        "no_disclosure": rate("no_disclosure_before_verify"),
        "concluded": rate("concluded"),
        "guardrail_violation_runs": sum(r["guardrail_violation"] for r in rs),
        "guard_catches": dict(guard),
        "turns_mean": statistics.mean(r["turns"] for r in rs) if rs else float("nan"),
        "lat_p50": pct(lat, 0.5), "lat_p90": pct(lat, 0.9),
        "net_p50": pct(net, 0.5), "net_p90": pct(net, 0.9),
        "tone": mean("tone"), "empathy": mean("empathy"), "natural": mean("natural_speech"),
        "digits_runs": sum(1 for r in rs if r["style"]["digits_in_speech"]),
        "long_turn_runs": sum(1 for r in rs if r["style"]["long_turns"]),
        "errors": sum(1 for r in rs if r["error"]),
    }


def fmt(a):
    return f"{a[0]}/{a[1]}"


def render(tag: str) -> str:
    rs = load(tag)
    s = summary(rs)
    L = [f"## Results: `{tag}`  ({s['runs']} calls)", "",
         f"**Pass (task + no guardrail violation): {s['pass']}/{s['runs']}**   task-only: {s['task_pass']}/{s['runs']}", "",
         "| Metric | Result |", "|---|---|",
         f"| Call concluded with an outcome record | {fmt(s['concluded'])} |",
         f"| Outcome accuracy | {fmt(s['outcome'])} |",
         f"| Verified flag correct | {fmt(s['verified'])} |",
         f"| Escalation flag correct | {fmt(s['escalate'])} |",
         f"| Promise-to-pay date captured correctly | {fmt(s['promise_date'])} |",
         f"| Payment link flag correct | {fmt(s['link_sent'])} |",
         f"| No loan details before verification (rule) | {fmt(s['no_disclosure'])} |",
         f"| Calls with a judged guardrail violation | {s['guardrail_violation_runs']} |",
         f"| Output-guard interventions (model tried, guard caught) | {s['guard_catches'] or 'none'} |",
         f"| Mean turns to outcome | {s['turns_mean']:.1f} |",
         f"| Latency to first spoken sentence p50 / p90 (raw, includes free-tier 429 waits) | {s['lat_p50']:.0f} / {s['lat_p90']:.0f} ms |",
         f"| Latency p50 / p90 excluding 429 waits | {s['net_p50']:.0f} / {s['net_p90']:.0f} ms |",
         f"| Tone / empathy / natural speech (judge, 1-5) | {s['tone']:.2f} / {s['empathy']:.2f} / {s['natural']:.2f} |",
         f"| Calls where the agent spoke digits aloud | {s['digits_runs']} |",
         f"| Calls with a turn over 45 words | {s['long_turn_runs']} |",
         f"| Crashed runs | {s['errors']} |", "",
         "### Per scenario", "", "| Scenario | Pass | Failed checks |", "|---|---|---|"]
    by = defaultdict(list)
    for r in rs:
        by[r["scenario"]].append(r)
    for sid, runs in by.items():
        fails = defaultdict(int)
        for r in runs:
            for k, v in r["checks"].items():
                if not v:
                    fails[k] += 1
            if r["guardrail_violation"]:
                fails["guardrail(judge)"] += 1
            if r["error"]:
                fails["crash"] += 1
        L.append(f"| {sid} | {sum(r['pass'] for r in runs)}/{len(runs)} | "
                 + (", ".join(f"{k} x{v}" for k, v in fails.items()) or "-") + " |")
    return "\n".join(L)


def show(tag, prefixes, only_failures=False):
    for r in load(tag):
        if prefixes and not any(r["scenario"].startswith(p) for p in prefixes):
            continue
        if only_failures and r["pass"]:
            continue
        print(f"\n=== {r['scenario']} run{r['run']}  {'PASS' if r['pass'] else 'FAIL'}  models={r['models']}")
        print("outcome:", r["outcome_record"])
        print("failed:", [k for k, v in r["checks"].items() if not v], "judge:", {k: v for k, v in r["judge"].items() if v not in (False,)})
        for t in r["transcript"]:
            tag_ = {"agent": "AGENT   ", "borrower": "BORROWER", "note": "  *     "}[t["who"]]
            print(f"  {tag_} {t['text']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--show", default="")
    ap.add_argument("--failures", action="store_true")
    a = ap.parse_args()
    if a.show or a.failures:
        show(a.tag, [x for x in a.show.split(",") if x], a.failures)
    else:
        print(render(a.tag))
