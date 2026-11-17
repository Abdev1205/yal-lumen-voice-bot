# Lumen Finance EMI Reminder Voice Agent: Build Plan

Goal: a narrow agent that *sounds* good, plus an honest eval that finds real failures.
Graded mostly by listening, so the time goes to conversation quality, then evals. Infra is not graded.

## Stack (and the trade-off to explain in the review)
| Piece | Choice | Why / trade-off |
|---|---|---|
| Voice in/out | Browser Web Speech API (STT + TTS), `en-IN` / `hi-IN` | Zero extra keys, runs on a laptop, free barge-in control. Quality is below Deepgram/ElevenLabs; the speech layer is a thin adapter so it can be swapped. |
| Transport | FastAPI WebSocket, text turns plus interrupt events | Simple, streaming, easy to measure per-hop latency. |
| Agent LLM | Llama 3.3 70B on Groq (free), streaming, tool use | Very fast first token. Any OpenAI-compatible endpoint works. Streamed sentence-by-sentence so TTS starts on the first sentence. |
| Simulated borrower and judge | Same endpoint (swap `SIM_MODEL` for a different model) | A different model than the agent is better, so it does not grade its own homework. Trade-off to state in the report. |

## Conversation design (the heart)
- **Verification gate enforced in code, not only in the prompt.** The LLM never sees loan data until `verify_identity` passes. `get_loan_details` returns an error before that. Leakage is impossible rather than merely unlikely.
- **Spoken-form formatting done in code.** Tools return `"twelve thousand five hundred rupees"` and `"the fifth of December"`, so the LLM can't read digits like a form.
- **Short turns.** Max about 2 sentences per turn, enforced by prompt plus a post-check metric.
- **No dead air.** Before a slow tool call the agent says a filler ("one moment, let me check that"). A tool timeout is simulated and recovered.
- **Barge-in.** The browser cancels TTS the moment the caller speaks and sends the text actually spoken. The server truncates its history to that, so the agent doesn't believe it said something the caller never heard.
- **Silence handling.** After N seconds of quiet the agent gives a gentle nudge. After two nudges it offers a callback and ends the call.
- **Guardrails.** Prompt rules plus an output guard (regex for threats and for waiver/discount promises, and for loan figures before verification).
- **Outcomes.** A single `record_outcome` tool with an enum schema.

## Evals
1. `evals/scenarios.yaml` has 14 or more scenarios: the 8 required responses, plus tricky ones (family member asks for the amount, a wrong DOB twice, a prompt-injection attempt, an "I'll pay Friday" date resolved against the call date, mid-call Hindi switch, hardship that turns rude, "stop calling me").
2. `evals/simulator.py`: an LLM borrower with a persona and a script of behaviour. Each scenario runs N=3 times at temperature>0.
3. Metrics:
   - Outcome accuracy (rule): record fields vs expected.
   - Verification-before-disclosure (rule): no loan figures in agent text before verified.
   - Guardrail violations (rule regex plus LLM judge).
   - Promise-date capture (rule).
   - Call efficiency (turns).
   - Latency p50/p90 (measured on LLM first sentence).
   - Tone (LLM judge, rubric), spot-checked by hand on a sample. Reported with an honest "how much I trust it".
4. `evals/report.md`: results, 2 or 3 real failures found, the fix for each, what's next. Must show a before/after.
5. Stretch only if time allows: voice-level sim, a dashboard of outcomes, Hindi code-mixing polish.

## Build order
1. [x] Scaffold, venv, plan
2. [ ] Mock data, spoken-form helpers, gated tools
3. [ ] Agent core (streaming, tools, barge-in truncation, guard, silence)
4. [ ] CLI text chat to iterate on conversation quality
5. [ ] Browser UI + WebSocket (voice demo)
6. [ ] Scenarios, simulator, runner, judge
7. [ ] Run evals, find failures, fix, rerun
8. [ ] README, report, demo script for the 3-5 min recording

## Needs from the user
- `LLM_API_KEY` in `.env` (nothing is set in this environment).
- You must run the final live demo and be able to explain every file. The review has a live borrower plus an unseen scenario.
