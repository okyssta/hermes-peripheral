"""Open the catalog PR from the fork, without cloning the whole repository."""
import base64
import json
import subprocess
import time

REPO = "NousResearch/hermes-agent"
FORK = "okyssta/hermes-agent"
BRANCH = "add-peripheral-catalog-entry"
ENTRY = "/root/peripheral/catalog-entry.yaml"

TITLE = "catalog: add peripheral — a searchable memory of what you've seen on screen"
BODY = """## What this adds

`plugin-catalog/peripheral.yaml` for **Peripheral**, a plugin that gives Hermes a
memory of the user's screen.

Hermes can read a file you hand it and can look at the screen *now*. It cannot
answer a question about a screen that is already gone — "that error I got
yesterday", "the price on that page", "what was on screen when it broke" — which is
the shape of most real questions. Peripheral indexes the screenshots the OS already
captures, reads them locally with Tesseract, and exposes the result as four tools
the agent calls on its own:

| Tool | Purpose |
|---|---|
| `screen_recall(query, since, until, limit)` | Ranked search over the words that were on screen |
| `screen_timeline(since, until, limit)` | What was on screen over a period, interleaved with the agent's own tool calls |
| `screen_show(id, chars)` | One screen's full OCR text, its path (for `vision_analyze`) and thumbnail |
| `screen_index(paths, batch)` | Index now, from the watched folders or explicit paths |

Plus a `/peripheral` command (status, scan, search, open, forget, pause, resume) and
a bundled skill that teaches the agent when screen memory beats asking the user to
re-send a screenshot.

## Surfaces used

- `ctx.register_tool` ×4 (toolset `peripheral`), `ctx.register_command("peripheral")`,
  `ctx.register_skill`, one observer hook (`post_tool_call`, to log agent actions on
  the same clock as the screens).
- Storage through `plugins.plugin_storage.plugin_db` / `plugin_data_dir` (documented
  API). No core overrides, no `sys.modules` writes, no patching of core objects.
- No network calls at all, and no Tesseract dependency at import time: missing OCR
  degrades to a visual index instead of failing to load.

## Disclosure (as in the entry)

Reads image files under `watch_dirs` only; writes one SQLite database plus WebP
thumbnails under `<HERMES_HOME>/plugin-data/peripheral/`; runs the local `tesseract`
binary as a subprocess with a 25 s timeout and a single thread; redacts keys, JWTs,
bearer headers, private keys and card numbers before storing text; never captures
the screen; never modifies or deletes the user's files.

## Verification at the pinned SHA (8ccab32)

```
$ hermes plugins validate .
✓ manifest … ✓ config schema … ✓ capability probe
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
Validation passed.
```

`pytest tests -q` → **22 passed** (real PNGs → real Tesseract → real FTS5 index:
ranked retrieval, incremental rescans, duplicate and near-duplicate rejection,
retention, forget, redaction, FTS operator injection, time parsing, manifest
contract).

Measured on a 2 vCPU ARM host: 10 screenshots at 1440×900 indexed in 10.6 s
(≈1.06 s/image), 12,616 characters of text stored; a warm rescan reads zero images.

## Prior art, checked

Nearest entries and why this differs: `mediarag` (semantic search over a document
corpus — no screen capture, no timeline), `document-reader` (OCR of documents into
a viewer — no history, no tools), `modlens`/`hermes-vision-inline` (vision bridges
for text-only models — per-image perception, no persistence), `source-tray` (the
URLs the *agent* opened — no screen content), `hermes-time-machine`/`session-diff`
(session timelines, not the screen). No entry in the catalog and no repository in
the post-announcement wave does screen history for the agent.

## Owner submission

The plugin repository owner (`okyssta`) is submitting this entry, per admission
rule 5.
"""


def run(*args: str) -> str:
    completed = subprocess.run(["gh", *args], capture_output=True, text=True)
    if completed.returncode:
        raise SystemExit(f"gh {' '.join(args)} failed: {completed.stderr.strip()[:600]}")
    return completed.stdout


def main() -> int:
    head = json.loads(run("api", f"repos/{REPO}/commits/main"))["sha"]
    print("base sha:", head)

    existing = subprocess.run(
        ["gh", "api", f"repos/{FORK}/git/ref/heads/{BRANCH}"], capture_output=True, text=True
    )
    if existing.returncode == 0:
        print("branch exists; reusing")
    else:
        run(
            "api", "-X", "POST", f"repos/{FORK}/git/refs",
            "-f", f"ref=refs/heads/{BRANCH}", "-f", f"sha={head}",
        )
        print("branch created from main")

    content = base64.b64encode(open(ENTRY, "rb").read()).decode()
    payload = {
        "message": "catalog: add peripheral — screen memory for Hermes",
        "content": content,
        "branch": BRANCH,
    }
    path = "plugin-catalog/peripheral.yaml"
    check = subprocess.run(
        ["gh", "api", f"repos/{FORK}/contents/{path}?ref={BRANCH}"], capture_output=True, text=True
    )
    if check.returncode == 0:
        payload["sha"] = json.loads(check.stdout)["sha"]
        with open("/tmp/catalog-update.json", "w") as handle:
            json.dump(payload, handle)
        run("api", "-X", "PUT", f"repos/{FORK}/contents/{path}", "--input", "/tmp/catalog-update.json")
        print("entry updated")
    else:
        with open("/tmp/catalog-payload.json", "w") as handle:
            json.dump(payload, handle)
        run("api", "-X", "PUT", f"repos/{FORK}/contents/{path}", "--input", "/tmp/catalog-payload.json")
        print("entry created")

    time.sleep(1)
    with open("/tmp/pr-payload.json", "w") as handle:
        json.dump({"title": TITLE, "body": BODY, "head": f"okyssta:{BRANCH}", "base": "main"}, handle)
    pr = json.loads(
        run("api", "-X", "POST", f"repos/{REPO}/pulls", "--input", "/tmp/pr-payload.json")
    )
    print("PR:", pr.get("html_url"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
