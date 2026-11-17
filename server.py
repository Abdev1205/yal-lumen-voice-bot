"""Browser voice demo server.  python server.py  ->  http://localhost:8000  (use Chrome or Edge)

The browser does speech-to-text and text-to-speech (Web Speech API); this server runs the agent
over a WebSocket, one sentence at a time, so audio starts on the first sentence.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from lumen.agent import CallAgent  # noqa: E402
from lumen.tools import load_borrowers  # noqa: E402

ROOT = Path(__file__).parent
CALL_DATE = date(2026, 11, 17)   # fixed so the demo matches the mock data (EMIs due around this date)
LOG = ROOT / "data" / "call_log.jsonl"
app = FastAPI()


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/borrowers")
def borrowers():
    return [{"id": b["id"], "name": b["name"], "language": b["language"], "dob": b["dob"], "last4": b["last4"],
             "loan_type": b["loan_type"], "emi_amount": b["emi_amount"], "due_date": b["due_date"],
             "days_overdue": b["days_overdue"]} for b in load_borrowers().values()]


@app.get("/api/calls")
def calls():
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()][-50:]


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    agent: CallAgent | None = None
    turn = 0

    async def send(**kw):
        await sock.send_text(json.dumps(kw, ensure_ascii=False))

    async def run_turn(text: str):
        nonlocal turn
        turn += 1
        my = turn
        async for ev in agent.respond(text):
            if ev["type"] == "sentence":
                await send(type="agent", turn=my, text=ev["text"], lang=ev["lang"], filler=bool(ev.get("filler")))
            else:
                m = ev["metrics"]
                await send(type="turn_done", turn=my, first_sentence_ms=m.first_sentence_ms, total_ms=m.total_ms,
                           tools=m.tool_calls, violations=m.violations, rate_limit_wait_ms=m.rate_limit_wait_ms,
                           models=m.models_used)
        if agent.ended:
            rec = agent.state.outcome
            LOG.parent.mkdir(exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": date.today().isoformat(), **rec}, ensure_ascii=False) + "\n")
            await send(type="ended", outcome=rec)

    try:
        while True:
            msg = json.loads(await sock.receive_text())
            t = msg.get("type")
            if t == "start":
                b = load_borrowers()[msg["borrower_id"]]
                agent = CallAgent(b, today=CALL_DATE)
                await send(type="agent", turn=0, text=agent.start(), lang=b["language"])
            elif agent is None:
                continue
            elif t == "barge_in":
                agent.apply_barge_in(msg.get("spoken", ""))
            elif t == "user":
                try:
                    await run_turn(msg["text"])
                except Exception as e:  # never leave the caller in silence
                    await send(type="agent", turn=turn, text="Sorry, I am having a technical problem. A colleague will call you back.", lang="en")
                    await send(type="error", detail=f"{type(e).__name__}: {str(e)[:200]}")
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(app, host="127.0.0.1", port=8000)
