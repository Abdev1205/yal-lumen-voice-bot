# Lumen Finance: Conversational EMI Reminder Voice Agent

An outbound voice agent that calls a borrower about an upcoming or overdue EMI, verifies who it is
talking to, handles what the borrower says, offers a payment link, and records a structured outcome.
Runs on a laptop in the browser. A narrow agent, built to sound good and to be measured honestly.

- **Results, failures found, and what is NOT verified:** [REPORT.md](REPORT.md) (read its first paragraph: free-tier API caps stopped a clean post-fix re-run)
- **Design decisions:** below
- **Eval scenarios:** [evals/scenarios.yaml](evals/scenarios.yaml)

## Run it

Needs Python 3.10+ and a free [Groq key](https://console.groq.com/keys) (agent) and a free
[Google AI Studio key](https://aistudio.google.com/apikey) (simulated borrower and judge, a different model family).

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows; use source .venv/bin/activate elsewhere
pip install -r requirements.txt
cp .env.example .env                                # then paste LLM_API_KEY and SIM_API_KEY
python server.py                                    # voice demo -> http://localhost:8000 (Chrome or Edge)
python chat.py B001                                 # or: text chat in the terminal
python -m pytest -q tests                           # unit tests (no keys needed)
python -m evals.runner --runs 2 --tag mytest        # full eval suite (19 scenarios x 2 runs; needs ~400k tokens, see REPORT.md)
python -m evals.report mytest --completed-only      # metrics table (drops calls that crashed on API errors)
python -m evals.report mytest --failures            # transcripts of failing calls
```

In the demo page pick a borrower, press **Start call**, and just talk (or type if the mic is blocked).
The right-hand "cheat sheet" shows each borrower's date of birth and last 4 digits, so you can verify yourself.
You can interrupt the agent at any time.

## How a call is structured

```
 browser mic ──STT (Web Speech)──► WebSocket ──► CallAgent.respond()  ──sentence by sentence──► browser TTS
                                                    │  LLM (streaming, tool calling)
                                                    ├─ verify_identity   (DOB or last 4 digits)
                                                    ├─ get_loan_details  (only after verification)
                                                    ├─ send_payment_link (mock, slow, can fail)
                                                    └─ record_outcome    (structured record, idempotent)
```

1. **Scripted opening** ("Am I speaking with Rahul?"): zero LLM latency at pick-up, reveals only a first name.
2. **Verify** with date of birth or last 4 digits. Two attempts, then the call ends politely.
3. **State the EMI** in one natural sentence, then ask what the borrower plans to do.
4. **Branch** on what they say: pays, promises a date, hardship, dispute, wrong person, callback, rude, off-topic, language switch.
5. **Offer the payment link** where relevant, close in one short line, and record the outcome.

Outcome record: `{borrower_id, verified, outcome, promise_to_pay_date, payment_link_sent, escalate_to_human, notes}`.

## Key design decisions

| Decision | Why |
|---|---|
| **The verification gate is enforced in code, not in the prompt.** The model's system prompt contains no loan data. `get_loan_details` and `send_payment_link` refuse until `verify_identity` passes. | A prompt rule is a request; a missing capability is a guarantee. A family member cannot talk the model into revealing what it does not have. |
| **`verify_identity` must be grounded in the caller's own words.** The tool rejects any answer that does not appear in what the caller just said, and does not count it as an attempt. | Found in testing: the model invented a date of birth and locked the call out. See REPORT.md. |
| **Numbers and dates are spoken-form from the tools** ("twelve thousand five hundred rupees", "the fifth of December", Indian numbering). | The LLM never has digits to read out like a form. |
| **Calendar in the prompt for relative dates** ("Friday", "tomorrow"). | LLMs are unreliable at date arithmetic; the model looks the date up. The tool also rejects past dates. |
| **Streaming, sentence by sentence.** TTS starts on the first sentence while the model is still generating. | Latency is measured to the first spoken sentence, which is what the caller waits for. |
| **No dead air:** a short filler line plays the moment the model commits to the slow payment-link tool. | The caller hears something natural, not silence. |
| **Barge-in:** the browser cancels speech the instant the caller talks, and sends back the text actually heard. The server truncates the agent's history to that. | Otherwise the agent believes it said things the caller never heard. |
| **Silence handling:** after 9 s of quiet the agent gently checks in; after a second silence it offers a callback. | |
| **Output guard on every sentence before it is spoken:** threats, affirmative waiver or discount promises, and loan figures before verification. | A second line of defence. Guard catches are counted as a metric, so a quiet guard is not mistaken for a safe model. |
| **Text tool calls are caught.** If a model writes `record_outcome({...})` as plain text, it is hidden from speech and executed. | Found in evals: it would otherwise be read aloud and the outcome lost. |
| **Model failover on rate limits.** On a 429 the agent switches immediately to another model (own limit pool) instead of waiting. | Free-tier limits caused 14-39 s turns. Failover brought typical turns to about 1-1.5 s. |
| **Single agent, no multi-agent router.** | The task is narrow. A router adds a hop of latency and no measurable gain here. |

## Tech choices and trade-offs

- **Browser Web Speech API for STT/TTS** (Chrome/Edge): zero extra keys, runs on a laptop, supports `en-IN` and `hi-IN`,
  and gives exact control over cancelling speech for barge-in. The cost is voice quality and recognition accuracy: below
  Deepgram + ElevenLabs/Cartesia. The speech layer is isolated in `static/index.html`; the agent core is text-in, text-out,
  so swapping in a streaming ASR/TTS over WebSocket changes only that layer.
- **Groq (gpt-oss-20b, with gpt-oss-120b and Qwen as failover)** for the agent: fastest first token available on a free tier.
  Trade-off: free-tier token-per-minute limits, and different models behave a little differently mid-call.
- **Gemini 2.5 Flash for the simulated borrower and the judge**: a different model family from the agent, so the agent is not grading itself.

## Layout

```
lumen/        agent.py (streaming loop, barge-in, failover)  tools.py (gated mock tools)  guard.py  prompts.py  speech.py  llm.py
server.py     FastAPI + WebSocket demo        static/index.html   browser voice UI
chat.py       terminal chat for fast iteration
data/         borrowers.json (8 mock borrowers)   call_log.jsonl (written by the demo)
evals/        scenarios.yaml  simulator.py (LLM borrower + judge)  runner.py  report.py  results/
tests/        unit tests: verification gate, grounding, spoken numbers and dates, guard
```

## Honest limitations

- Evals are text-level: the simulated borrower types, it does not speak. Barge-in is simulated by truncating the agent's turn,
  not with real overlapping audio. Latency in the evals is LLM time to first sentence, not full speech-to-speech.
- Eval runs happened on free-tier models. 21 of the 38 baseline calls crashed on rate limits or a provider error and are reported separately; no clean post-fix re-run was possible. See REPORT.md.
- The judge is an LLM. It was spot-checked by hand on a sample (see REPORT.md), not calibrated against a panel of human raters.
- No real telephony, voicemail detection or human hand-off beyond the `escalate_to_human` flag.
