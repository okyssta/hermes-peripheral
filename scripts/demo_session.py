#!/usr/bin/env python3
"""Drive a real tool-calling session against the plugin's handlers.

This is the same loop Hermes runs — the model is given the plugin's tool schemas,
chooses a tool, the handler runs against the real index, its text goes back to the
model, and the model answers — with no Hermes process in the middle. It exists
because a demo has to be reproducible on a machine where the gateway is not
available, and because it is the honest way to test "does the model actually reach
for screen memory when a user talks like a person".

    OPENCODE_GO_API_KEY=... python3 scripts/demo_session.py "what was that card error I saw on Monday?"

Credentials come from the environment (OPENCODE_GO_API_KEY) only; nothing is
printed. Every run writes `docs/demo-transcript.{json,txt}` — the question, every
tool call the model chose, the exact tool output it saw and the answer it gave — so
"the model chose the tools" is a file that can be re-read, not a story.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, "/root/.hermes/hermes-agent")

os.environ["HERMES_HOME"] = os.environ.get("DEMO_HERMES_HOME", "/tmp/peripheral-demo-home")

from peripheral import schemas, tools  # noqa: E402
from peripheral.settings import Settings  # noqa: E402

BASE_URL = os.environ.get("PERIPHERAL_DEMO_BASE_URL", "https://opencode.ai/zen/go/v1")
MODEL = os.environ.get("PERIPHERAL_DEMO_MODEL", "deepseek-v4.1-flash")

SYSTEM = (
    "You are Hermes, a personal agent running on the user's own machine. You have screen "
    "memory tools: screen_recall, screen_timeline, screen_show, screen_index. Use them when "
    "the user refers to something they saw rather than something they typed. Quote what the "
    "screen actually says; never invent a screen or a detail. A day the user names ('Monday') "
    "is a since/until window rather than a reason to give up: search the words that would "
    "have been on the screen, widen once if nothing matches, and answer in a few sentences."
)

HANDLERS = {
    "screen_recall": tools.screen_recall,
    "screen_timeline": tools.screen_timeline,
    "screen_show": tools.screen_show,
    "screen_index": tools.screen_index,
}

REPO = Path(__file__).resolve().parents[1]
TRANSCRIPT_JSON = Path(
    os.environ.get("PERIPHERAL_DEMO_TRANSCRIPT") or REPO / "docs" / "demo-transcript.json"
)
TRANSCRIPT_TXT = TRANSCRIPT_JSON.with_suffix(".txt")


def api_key() -> str:
    """Credentials come from the environment — a plugin never reads .env itself,
    and neither does its demo script. Export the key before running:

        OPENCODE_GO_API_KEY=... python3 scripts/demo_session.py "your question"
    """
    key = (os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("PERIPHERAL_DEMO_API_KEY") or "").strip()
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
            "-H", "Authorization: Bearer " + key,
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
    if not body.get("choices"):
        raise SystemExit(
            "provider returned no choices: "
            + completed.stdout.decode("utf-8", "replace")[:400]
        )
    return body


def run_turn(question: str, *, key: str, verbose: bool = True, max_steps: int = 8) -> dict:
    """One user question → however many tool calls the model asks for → its answer."""
    record: dict = {"question": question, "steps": [], "answer": "", "started_at": time.time()}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    if verbose:
        print(f"\n\033[1m❯ {question}\033[0m")
    for _ in range(max_steps):
        body = call(messages, key=key)
        message = body["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            answer = (message.get("content") or "").strip()
            record["answer"] = answer
            record["elapsed_s"] = round(time.time() - record["started_at"], 2)
            if verbose:
                print(f"\n{answer}\n")
            return record
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
            record["steps"].append(
                {
                    "tool": name,
                    "args": args,
                    "result_chars": len(result),
                    # Thumbnail paths point into the throwaway demo home; they are
                    # noise in a transcript someone reads later.
                    "result_preview": "\n".join(
                        line
                        for line in result.splitlines()
                        if not line.strip().startswith("thumb:")
                    )[:1200],
                }
            )
            messages.append(
                {"role": "tool", "tool_call_id": call_spec["id"], "name": name, "content": result}
            )
    # The loop is bounded, so it must still end with an answer: one last call with
    # the tools withdrawn forces the model to summarise what it already retrieved.
    body = call(
        messages
        + [{"role": "user", "content": "Answer now from what you retrieved above."}],
        key=key,
        tools_on=False,
    )
    record["answer"] = (body["choices"][0]["message"].get("content") or "").strip() or "(no final answer)"
    record["elapsed_s"] = round(time.time() - record["started_at"], 2)
    if verbose:
        print(f"\n{record['answer']}\n")
    return record


def render_text(document: dict) -> str:
    lines = [
        "peripheral — captured tool-calling session",
        "",
        f"captured : {document['captured_at']}",
        f"provider : {document['base_url']}",
        f"model    : {document['model']}",
        f"index    : {document['index']}",
        "",
        "The model was given the plugin's four tool schemas and the user's question;",
        "every tool call and answer below came back from the provider, not from this script.",
        "Re-run scripts/demo_session.py (see README) to reproduce it.",
    ]
    for turn in document["turns"]:
        lines += ["", f"❯ {turn['question']}", ""]
        for step in turn["steps"]:
            lines.append(f"  ⚙ {step['tool']}({json.dumps(step['args'])})")
            for line in step["result_preview"].splitlines():
                lines.append(f"  │ {line}")
            if step["result_chars"] > len(step["result_preview"]):
                lines.append(f"  │ … {step['result_chars'] - len(step['result_preview'])} more chars")
            lines.append("")
        lines += [f"answer ({turn['elapsed_s']}s):", "", turn["answer"], ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    key = api_key()
    folder = Path(os.environ.get("PERIPHERAL_DEMO_DIR", "/root/demo-screens/Screenshots"))
    settings = Settings(watch_dirs=(folder,), auto_scan_minutes=0)
    from peripheral import ingest

    report = ingest.scan(settings, batch=40)
    index_line = report.line()
    print(f"index: {index_line}")

    document = {
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_url": BASE_URL,
        "model": MODEL,
        "index": index_line,
        "turns": [run_turn(question, key=key) for question in sys.argv[1:]],
    }
    if not document["turns"]:
        return 0
    TRANSCRIPT_JSON.parent.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_JSON.write_text(json.dumps(document, indent=2) + "\n")
    TRANSCRIPT_TXT.write_text(render_text(document))
    print(f"transcript: {TRANSCRIPT_TXT}  ·  {TRANSCRIPT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
