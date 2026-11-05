#!/usr/bin/env python3
"""Drive a real tool-calling session against the plugin's handlers.

This is the same loop Hermes runs — the model is given the plugin's tool schemas,
chooses a tool, the handler runs against the real index, its text goes back to the
model, and the model answers — with no Hermes process in the middle. It exists
because a demo has to be reproducible on a machine where the gateway is not
available, and because it is the honest way to test "does the model actually reach
for screen memory when a user talks like a person".

    python3 scripts/demo_session.py "what was that card error I saw on Monday?"

Credentials come from the active profile's .env (OPENCODE_GO_API_KEY) only; nothing
is printed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, "/root/.hermes/hermes-agent")

os.environ.setdefault("HERMES_HOME", os.environ.get("DEMO_HERMES_HOME", "/tmp/peripheral-demo-home"))

from peripheral import schemas, tools  # noqa: E402
from peripheral.settings import Settings  # noqa: E402

BASE_URL = os.environ.get("PERIPHERAL_DEMO_BASE_URL", "https://opencode.ai/zen/go/v1")
MODEL = os.environ.get("PERIPHERAL_DEMO_MODEL", "deepseek-v4.1-flash")

SYSTEM = (
    "You are Hermes, a personal agent running on the user's own machine. You have screen "
    "memory tools: screen_recall, screen_timeline, screen_show, screen_index. Use them when "
    "the user refers to something they saw rather than something they typed. Quote what the "
    "screen actually says; never invent a screen or a detail."
)

HANDLERS = {
    "screen_recall": tools.screen_recall,
    "screen_timeline": tools.screen_timeline,
    "screen_show": tools.screen_show,
    "screen_index": tools.screen_index,
}


def api_key() -> str:
    """Credentials come from the environment — a plugin never reads .env itself,
    and neither does its demo script. Export the key before running:

        OPENCODE_GO_API_KEY=... python3 scripts/demo_session.py "your question"
    """
    key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "set OPENCODE_GO_API_KEY in the environment (or PERIPHERAL_DEMO_API_KEY)."
        )
    return key


def call(messages: list[dict], *, key: str, tools_on: bool = True) -> dict:
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 900,
    }
    if tools_on:
        payload["tools"] = [{"type": "function", "function": schema} for schema in schemas.ALL]
    completed = subprocess.run(
        [
            "curl", "-sS", "-m", "120", f"{BASE_URL}/chat/completions",
            "-H", f"Authorization: Bearer {key}",
            "-H", "Content-Type: application/json",
            "-H", f"x-opencode-session: peripheral-demo-{int(time.time() * 1000)}",
            "--data-binary", "@-",
        ],
        input=json.dumps(payload).encode(),
        capture_output=True,
    )
    body = json.loads(completed.stdout.decode("utf-8", "replace") or "{}")
    if "error" in body:
        raise SystemExit(f"provider error: {body['error']}")
    return body


def run_turn(question: str, *, key: str, verbose: bool = True, max_steps: int = 5) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    if verbose:
        print(f"\n\033[1m❯ {question}\033[0m")
    for _ in range(max_steps):
        body = call(messages, key=key)
        message = body["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            answer = (message.get("content") or "").strip()
            if verbose:
                print(f"\n{answer}\n")
            return answer
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call_spec in calls:
            name = call_spec["function"]["name"]
            try:
                args = json.loads(call_spec["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if verbose:
                print(f"  \033[2m⚙ {name}({json.dumps(args)[:110]})\033[0m")
            result = HANDLERS[name](args)
            if verbose:
                head = "\n".join(result.splitlines()[:10])
                print("\n".join(f"  \033[2m│ {line}\033[0m" for line in head.splitlines()))
                if len(result.splitlines()) > 10:
                    print(f"  \033[2m│ … {len(result.splitlines()) - 10} more lines\033[0m")
            messages.append(
                {"role": "tool", "tool_call_id": call_spec["id"], "name": name, "content": result}
            )
    return "(no final answer)"


def main() -> int:
    key = api_key()
    folder = Path(os.environ.get("PERIPHERAL_DEMO_DIR", "/root/demo-screens/Screenshots"))
    settings = Settings(watch_dirs=(folder,), auto_scan_minutes=0)
    from peripheral import ingest

    report = ingest.scan(settings, batch=40)
    print(f"index: {report.line()}")
    for question in sys.argv[1:]:
        run_turn(question, key=key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
