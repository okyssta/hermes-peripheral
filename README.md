# Peripheral — Hermes gets a memory of what you've seen

You saw the error. You just can't remember where.
Your agent never saw it at all.

**Peripheral** gives Hermes a searchable memory of your screen. Every screenshot on
the machine is read locally with OCR, indexed with the time it was captured, and
answered back by four tools the agent calls on its own:

> **You:** what was that card error I saw on Monday?
>
> **Hermes:** *(calls `screen_recall("card error", since="2026-11-02")`)*
> The closest thing on Monday was a Stripe error-code page, open at 11:05 AM.
> It lists `expired_card` — "The card has expired. Check the expiration date or
> use a different card." One caveat: this was the reference docs, not a live
> error in your app — want me to widen the search?

Nothing leaves the machine. No account, no API key, no capture daemon, no cloud
index. One SQLite file and a folder of thumbnails, both inside the profile's own
plugin-data directory, both deletable with a command.

**[74-second demo (silent)](docs/demo.mp4)** · **[verification dossier (PDF)](docs/peripheral-dossier.pdf)**

---

## Why this exists

Hermes can read any file you hand it, and it can look at the screen *now*. What it
cannot do is answer a question about a screen that is already gone — which is the
shape of most real questions ("that error", "that price", "that message", "what
was I doing when it broke"). The workaround everyone uses is re-taking the
screenshot and pasting it back, which means the memory lives in the user's head.

This is a **sense**, not a prompt: the agent cannot get it by being asked nicely,
because no amount of instruction lets a model read pixels it was never given.

### The honest competitive audit

The plugin catalog (978 entries at the time of writing), the 480-repo wave created
since the hackathon opened, and the community directory were searched before this
was built. Nearest neighbours and what each actually does:

| Nearest thing | What it does | Why this is not that |
|---|---|---|
| `mediarag` | Semantic search over a *document/media corpus* you point it at | No screen capture, no timeline, no "what was on screen" — it is a library indexer |
| `document-reader` (Desktop) | OCR of documents into a viewer | Reads documents you give it; no history, no agent tools, no search by time |
| `modlens`, `hermes-vision-inline` | Vision bridge: describes an image a *text-only model* cannot see | Per-image perception, no persistence and no index |
| `source-tray` | Records the URLs the *agent* opened | Captures the agent's browsing, not the user's screen; stores no content |
| `hermes-time-machine`, `session-diff` | Timeline/scrubber over *sessions* | Conversation history, not the screen |
| OS features (macOS Spotlight+Live Text, Windows Recall) | Search text inside screenshots on one OS | Not agent-native: no tools, no time semantics, no interleaving with what the agent did, no Hermes tool surface |

The delta is the mechanism, not the packaging: **a persistent, time-indexed,
locally-derived percept of the user's screen, exposed as tools, joined to a log of
what the agent itself did.** That join is the part nothing else has — the timeline
shows your screens and the agent's tool calls on one clock, so "what was on screen
when Hermes changed that config?" is answerable.

Scope of the claim: this is "no catalog entry and no repo in the competition wave
does it", not "no software anywhere does this". Windows Recall and screenpipe
prove the demand; neither is a Hermes plugin, and neither gives the agent tools.

---

## Install

```bash
hermes plugins install okyssta/hermes-peripheral --enable
```

That is the whole setup if your screenshots land in the usual place — the plugin
auto-detects `~/Pictures/Screenshots`, `~/Screenshots`, `~/Desktop` (macOS puts
them on the Desktop), `~/Pictures/Screenshots` on Windows, plus Hermes' own
inbound media folder so screenshots you *send* the agent are indexed too.

OCR needs Tesseract (a system binary, not a Python package):

```bash
brew install tesseract            # macOS
sudo apt install tesseract-ocr    # Debian/Ubuntu
winget install UB-Mannheim.TesseractOCR   # Windows
```

Without it the plugin still indexes screens visually — it records the time, the
path and a thumbnail, and the agent can open any of them with `screen_show` — but
text search stays empty, and `/peripheral` says so plainly instead of pretending.

Point it somewhere else with one setting:

```bash
hermes config set plugins.entries.peripheral.settings.watch_dirs '["~/Pictures/Screenshots","~/Desktop"]'
```

### Settings

Every key is on the plugin's page in **Desktop → Settings → Plugins**, and in
`config.yaml` under `plugins.entries.peripheral.settings`:

| Key | Default | What it does |
|---|---|---|
| `watch_dirs` | auto-detected | Folders scanned for images (recursive to two levels) |
| `ocr_languages` | `eng` | Tesseract codes, `+`-joined (`eng+deu+fra`); a missing pack falls back to English |
| `auto_scan_minutes` | `10` | Background rescan interval; `0` disables it (queries still index first) |
| `include_photos` | `false` | Index images with almost no text |
| `max_text_chars` | `20000` | Cap on text kept per screen |
| `retention_days` | `0` | Drop entries older than N days (`0` = keep) |
| `redact` | `true` | Strip keys, tokens, private keys and card numbers before storing |
| `thumbnails` | `true` | Keep a 360px thumbnail so a deleted screenshot still resolves |
| `record_agent_events` | `true` | Log the agent's tool calls next to your screens |

---

## Using it

The agent reaches for the tools itself. You can also drive them:

```
/peripheral                     what is indexed, where it looks, OCR status
/peripheral scan                index new screenshots now
/peripheral stripe expired      search the screen history
/peripheral stripe since yesterday
/peripheral timeline yesterday  what was on screen, in order, with agent actions
/peripheral open 8              one screen's full text
/peripheral forget 8            remove one screen from the index (the file stays)
/peripheral forget all
/peripheral pause | resume
```

| Tool | Purpose |
|---|---|
| `screen_recall(query, since, until, limit)` | Rank screens by the words that were on them, recency-weighted |
| `screen_timeline(since, until, limit)` | What was on screen over a period, interleaved with the agent's tool calls |
| `screen_show(id, chars)` | One screen's full text, its path and thumbnail |
| `screen_index(paths, batch)` | Index now, from the watched folders or explicit paths |

A bundled skill teaches the agent *when* screen memory beats asking the user, and
the honesty rules for quoting OCR ("the screen shows", never "you meant").

---

## How it works

```
screenshot folders ──► incremental scan ──► Tesseract (one subprocess, 1 thread, 25 s cap)
                            │                        │
                            │                        ▼
                            │                 clean → redact → title
                            ▼                        │
                    mtime+size+sha256               ▼
                    bookkeeping ──────────► shots + FTS5 (porter) + 64-bit dHash
                                                     │
                            agent tool calls ──► events (20k capped)
                                                     ▼
                                    screen_recall / timeline / show
```

- **Incremental**: a file is only re-read when its mtime or size changed; identical
  content is skipped by SHA-256; a screenshot within Hamming distance 3 of one
  already stored is dropped, so scrolling a page does not fill the index with
  forty near-copies.
- **Bounded**: a scan OCRs at most `batch` new images (default 60 in the
  background, 12 on a lazy query) so a first run over a big folder never blocks a
  turn. The next scan continues.
- **Ranked, not just matched**: FTS5 BM25, re-ranked with recency and text
  richness; a query is sanitized into quoted prefix terms so nothing a user types
  can turn into FTS operators.
- **Measured on this machine** (2 vCPU ARM, no GPU): 10 screenshots at
  1440×900 → 10.6 s end to end (≈1.06 s/image), 12,616 characters of text stored,
  1.3 MB of source images. A warm re-scan of the same folder reads zero images.
- **Fails open everywhere**: no Tesseract, unreadable image, locked database,
  bad regex, background thread crash — each is logged, skipped, and the rest of
  the plugin keeps answering.

---

## Privacy, plainly

This plugin reads your screenshots, so it is built to be boring about them:

- **No network calls at all.** No telemetry, no sync, no model API for indexing.
  The only thing that ever leaves the machine is whatever the agent chooses to
  quote in its answer.
- **No capture.** It does not take screenshots. It reads files your own screenshot
  tool already wrote. (A capture mode is deliberately not shipped: the failure
  mode of a botched screen recorder is silent and personal.)
- **Redaction on by default**: API keys (`sk-`, `ghp_`, `AKIA`, `AIza`, Slack
  tokens), JWTs, bearer/basic headers, `password:`/`token:` JSON values, private
  keys, card numbers and 32+ char hex strings are replaced before anything is
  stored.
- **You can forget**: `/peripheral forget <id>` or `forget all` removes rows (and
  their thumbnails' references) — the image files are never touched.
- **You can pause**: `/peripheral pause` stops indexing immediately.
- **Retention**: `retention_days` prunes old entries on every scan.
- **Profile-scoped**: the index lives in
  `<HERMES_HOME>/plugin-data/peripheral/`, one per profile, and dies with the
  plugin if you remove it.

---

## Tests

```bash
uv venv .venv && uv pip install --python .venv/bin/python pytest pillow pyyaml
.venv/bin/python -m pytest tests -q       # 22 passed
hermes plugins validate .                 # Validation passed (security scan: safe)
```

The tests build real PNGs with real text and run the real pipeline: indexing,
ranked retrieval, incremental rescans, duplicate and near-duplicate rejection,
retention, forget, redaction, FTS operator injection, time-expression parsing, and
a manifest contract test that keeps `plugin.yaml` and the code naming the same
things.

---

## Limits, stated up front

- OCR is good, not perfect: small text, dark-on-dark UI and unusual fonts degrade.
  The agent quotes what it read and says so; `screen_show` gives the path when the
  layout is the answer.
- Text inside images (charts, screenshots-of-screenshots, video frames) is indexed
  as text; *understanding* a picture is still `vision_analyze`'s job.
- HEIC needs `pillow-heif` in the host environment to be read at all; unsupported
  files are skipped, not retried forever.
- The timeline is only as complete as the screenshot folder. If your OS is
  configured to save screenshots elsewhere, set `watch_dirs`.

MIT licensed. Disclosure for the catalog: reads image files under `watch_dirs`
only, writes one SQLite database and WebP thumbnails under
`<HERMES_HOME>/plugin-data/peripheral/`, runs the local `tesseract` binary with a
25-second cap, makes no network calls, and never modifies or deletes the user's
files.
