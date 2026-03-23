"""
WebSocket Demo Client — Trango Tech AI Sales Agent
Simulates a real-time voice conversation via WebSocket (text mode).

Usage:
    python scripts/ws_demo_client.py
    python scripts/ws_demo_client.py --session my-session-001
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import websockets


async def run_demo(session_id: str, server_url: str) -> None:
    uri = f"{server_url}/ws/voice/{session_id}"
    print(f"\n{'='*60}")
    print("  Trango Tech AI Sales Agent — WebSocket Demo")
    print(f"  Session: {session_id[:16]}...")
    print(f"  Server:  {uri}")
    print(f"{'='*60}")
    print("  Type your messages. Commands: /quit, /state, /lead")
    print(f"{'='*60}\n")

    async with websockets.connect(uri) as ws:
        async def listen():
            agent_response = []
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")

                if t == "token":
                    text = msg.get("text", "")
                    print(text, end="", flush=True)
                    agent_response.append(text)

                elif t == "state":
                    print(f"\n\n  [State: {msg.get('agent_state')} | "
                          f"Temp: {msg.get('lead_temperature')} | "
                          f"Score: {msg.get('qualification_score', 0):.2f}]")
                    if msg.get("lead_saved"):
                        print("  ✓ Lead saved to Excel!")
                    agent_response.clear()
                    print("\nYou: ", end="", flush=True)

                elif t == "lead_saved":
                    print(f"\n  ✓ Lead captured: {msg.get('lead_id')}")

                elif t == "transcript":
                    print(f"\n  [Transcript: {msg.get('text')}]")
                    print("\nAlex: ", end="", flush=True)

                elif t == "interrupted":
                    print("\n  [AI interrupted]")

                elif t == "session_ended":
                    print(f"\n  Session ended. Lead ID: {msg.get('lead_id', 'N/A')}")
                    return

                elif t == "error":
                    print(f"\n  [ERROR]: {msg.get('message')}")

        async def send_messages():
            print("Alex: [Connecting...]\n")
            # Trigger greeting
            await ws.send(json.dumps({"type": "text", "content": "hello"}))
            print("Alex: ", end="", flush=True)

            await asyncio.sleep(0.2)

            while True:
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("")
                    )
                except (EOFError, KeyboardInterrupt):
                    break

                if user_input.strip() == "/quit":
                    await ws.send(json.dumps({"type": "end_session"}))
                    await asyncio.sleep(1)
                    break
                elif user_input.strip() == "/state":
                    print("  [Use /quit to end session and see final state]")
                    print("\nYou: ", end="", flush=True)
                    continue

                if user_input.strip():
                    print("Alex: ", end="", flush=True)
                    await ws.send(json.dumps({
                        "type": "text",
                        "content": user_input.strip(),
                    }))

        listener_task = asyncio.create_task(listen())
        await send_messages()
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass

    print("\n\nSession complete.")


def main():
    parser = argparse.ArgumentParser(description="Trango Agent WS Demo")
    parser.add_argument("--session", default=str(uuid.uuid4()), help="Session ID")
    parser.add_argument("--server", default="ws://localhost:8000", help="Server URL")
    args = parser.parse_args()
    asyncio.run(run_demo(args.session, args.server))


if __name__ == "__main__":
    main()
