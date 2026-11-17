"""Text chat with the agent for fast iteration on conversation quality.
usage: python chat.py B001
"""
import asyncio
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()
from lumen.agent import CallAgent  # noqa: E402
from lumen.tools import load_borrowers  # noqa: E402


async def main(bid: str):
    agent = CallAgent(load_borrowers()[bid], today=date(2026, 11, 17))
    print("AGENT:", agent.start())
    while not agent.ended:
        user = input("YOU:   ")
        async for ev in agent.respond(user):
            if ev["type"] == "sentence":
                print("AGENT:", ev["text"], "(filler)" if ev.get("filler") else "")
            else:
                m = ev["metrics"]
                print(f"   [first sentence {m.first_sentence_ms:.0f} ms, tools={m.tool_calls}, violations={m.violations}]")
    print("\nOUTCOME:", agent.state.outcome)

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "B001"))
