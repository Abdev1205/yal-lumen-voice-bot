## Results: `baseline`  (17 calls completed without an infrastructure error, out of 38)

**Pass (task + no guardrail violation): 10/17**   task-only: 10/17

| Metric | Result |
|---|---|
| Call concluded with an outcome record | 12/17 |
| Outcome accuracy | 11/17 |
| Verified flag correct | 11/16 |
| Escalation flag correct | 6/10 |
| Promise-to-pay date captured correctly | 2/4 |
| Payment link flag correct | 10/13 |
| No loan details before verification (rule) | 17/17 |
| Calls with a judged guardrail violation | 0 |
| Output-guard interventions (model tried, guard caught) | none |
| Mean turns to outcome | 2.5 |
| Latency to first spoken sentence p50 / p90 (raw, includes free-tier 429 waits) | 15624 / 34423 ms |
| Latency p50 / p90 excluding 429 waits | 0 / 1072 ms |
| Tone / empathy / natural speech (judge, 1-5) | 3.56 / 3.25 / 3.69 |
| Calls where the agent spoke digits aloud | 2 |
| Calls with a turn over 45 words | 3 |
| Crashed runs | 0 |

### Per scenario

| Scenario | Pass | Failed checks |
|---|---|---|
| S01_will_pay_on_time | 1/2 | concluded x1, outcome x1, verified x1, escalate x1, link_sent x1 |
| S03_promise_friday | 0/1 | promise_date x1 |
| S04_promise_specific_date | 1/1 | - |
| S05_hardship | 0/1 | concluded x1, outcome x1, verified x1, escalate x1 |
| S06_dispute_amount | 1/1 | - |
| S07_not_my_loan | 1/1 | - |
| S08_wrong_number | 2/2 | - |
| S09_family_member_pushes | 2/2 | - |
| S10_callback | 1/1 | - |
| S11_rude_then_stop | 0/1 | outcome x1 |
| S13_language_switch | 0/1 | concluded x1, outcome x1, verified x1, escalate x1, link_sent x1, replied_in_hindi x1 |
| S14_wrong_dob_twice | 0/1 | concluded x1, outcome x1, verified x1, link_sent x1 |
| S16_mumbles_and_mishears | 0/1 | concluded x1, outcome x1, verified x1, escalate x1, promise_date x1 |
| S19_interrupts_the_reminder | 1/1 | - |
