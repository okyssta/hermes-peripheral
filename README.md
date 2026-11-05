# Peripheral — Hermes remembers what was on your screen

You saw the error. You just can't remember where.
Your agent never saw it at all.

**Peripheral** gives Hermes a memory of your screen. It captures the screen itself —
on request, or on change in the background — and it also indexes the screenshots your
OS already writes. Everything is read locally with OCR, stored with the time it
appeared, and answered back by six tools the agent calls on its own:

> **You:** I got a card error a little while ago — what did it actually say? And was
> anyone else talking about it?
>
> **Hermes:** *(calls `screen_recall(query="card declined", since="today")`, then
> `screen_show(1)`, `screen_show(2)`, `screen_show(3)`)* Your card was declined — the
> card has expired, error code `expired_card`. Two people were talking about it in
> `#deploys`: Sam flagged that the checkout service throws it for test cards, Priya
> said the fixture is pinned to 03/24. **But the production dashboard shows 402
> card_declined 18,402 times in 24 hours** — worth confirming the sandbox
> explanation actually covers production.

That exchange is not a mock-up. The three screens were **captured by the plugin from a
real display**, and the tool calls and answer came back from `deepseek-v4.1-flash`
with the plugin's real schemas — see [the captured transcript](docs/demo-transcript.txt)
(six tool calls, 18.8 s) and [the 80-second silent demo](docs/demo.mp4).

Nothing leaves the machine. No account, no API key, no cloud index. One SQLite file
and a folder of frames, inside the profile's own plugin-data directory, both
deletable with a command.

**[80-second demo (silent)](docs/demo.mp4)** · **[captured transcript](docs/demo-transcript.txt)** · **[verification dossier (PDF)](docs/peripheral-dossier.pdf)**

---

## Why this exists

Hermes can read any file you hand it, and it can look at the screen *now*. What it
cannot do is answer a question about a screen that is already gone — which is the
shape of most real questions ("that error", "that price", "that message", "what was I
doing when it broke"). The workaround everyone uses is re-taking the screenshot and
pasting it back, which means the memory lives in the user's head.

This is a **sense**, not a prompt: no amount of instruction lets a model read pixels
it was never given.

### What changed in 2.0

Version 1 read only the screenshot folder, which quietly excluded everyone who does
not take screenshots. 2.0 closes that gap and two more:

| | 1.0 | 2.0 |
|---|---|---|
| Where screens come from | your screenshot folder | **the screen itself**, plus the folder |
| If the screen changes | — | opt-in background capture, changed frames only, capped per hour |
| If Tesseract is missing | nothing indexed | **visual index** — frames are kept and can be looked at |
| Opening a screen | text + path | **text + the image attached to the result** (on vision models) |
| Forgetting | slash command | **`screen_forget` tool** the agent can call, rows *and* files |

### The honest competitive audit

The plugin catalog (978 entries at the time of writing), the 480-repo wave created
since the hackathon opened, and the community directory were searched before this was
built. Nearest neighbours and what each actually does:

| Nearest thing | What it does | Why this is not that |
|---|---|---|
| `mediarag` | Semantic search over a *document/media corpus* you point it at | No screen capture, no timeline, no "what was on screen" — it is a library indexer |
| `document-reader` (Desktop) | OCR of documents into a viewer | Reads documents you give it; no history, no agent tools, no search by time |
| `modlens`, `hermes-vision-inline` | Vision bridge: describes an image a *text-only model* cannot see | Per-image perception, no persistence and no index |
| `computer-use`, `phone-harness`, `0bull` | Drive a device or a desktop *now* | Live control, no memory: nothing to ask later |
| `source-tray` | Records the URLs the *agent* opened | Captures the agent's browsing, not the user's screen; stores no content |
| `hermes-time-machine`, `session-diff` | Timeline/scrubber over *sessions* | Conversation history, not the screen |
| OS features (macOS Spotlight+Live Text, Windows Recall) | Search text inside screenshots on one OS | Not agent-native: no tools, no time semantics, no interleaving with what the agent did, no Hermes tool surface |

The delta is the mechanism, not the packaging: **a persistent, time-indexed, locally
derived percept of the user's screen, exposed as tools, joined to a log of what the
agent itself did.** That join is the part nothing else has — the timeline shows your
screens and the agent's tool calls on one clock, so "what was on screen when Hermes
changed that config?" is answerable.

Scope of the claim: this is "no catalog entry and no repo in the competition wave does
it", not "no software anywhere does this". Windows Recall and screenpipe prove the
demand; neither is a Hermes plugin, and neither gives the agent tools.

---

## Install

```bash
hermes plugins install okyssta/hermes-peripheral --enable
```

That is the whole setup. The plugin auto-detects the usual screenshot folders
(`~/Pictures/Screenshots`, `~/Screenshots`, `~/Desktop`, the Windows screenshot
folder, Hermes' own inbound media folder) and, separately, checks whether this
machine has a way to capture the screen: `ffmpeg` on X11, `grim` on Wayland,
`screencapture` on macOS, PowerShell on Windows. `/peripheral status` tells you what
it found.

OCR is optional. With Tesseract installed, screens are searchable by the words on
them:

```bash
brew install tesseract            # macOS
sudo apt install tesseract-ocr    # Debian/Ubuntu
winget install UB-Mannheim.TesseractOCR   # Windows
```

Without it the plugin still works as a **visual** memory: frames are captured and
kept, the timeline lists them, and `screen_show` opens them for a vision model. Text
search stays empty and `/peripheral` says so instead of pretending.

### Settings

Every key is on the plugin page in **Desktop → Settings → Plugins**, and in
`config.yaml` under `plugins.entries.peripheral.settings`:

| Key | Default | What it does |
|---|---|---|
| `watch_dirs` | auto-detected | Folders scanned for images (recursive to two levels) |
| `capture_enabled` | `false` | Background capture on a timer — off until you turn it on |
| `capture_interval_seconds` | `30` | How often the background capture looks at the screen |
| `capture_min_gap_seconds` | `15` | Never store another frame sooner than this |
| `capture_max_per_hour` | `120` | Hard ceiling, so a video call cannot fill the disk |
| `show_images` | `true` | Attach the stored image to results when the model can see images |
| `ocr_languages` | `eng` | Tesseract codes, `+`-joined (`eng+deu+fra`) |
| `auto_scan_minutes` | `10` | Screenshot-folder rescan interval; `0` disables it |
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
/peripheral                     what is indexed, where it looks, capture and OCR readiness
/peripheral capture [note]      remember what is on screen right now
/peripheral watch on | off      background capture on change (off by default)
/peripheral scan                index new screenshots now
/peripheral stripe expired      search the screen history
/peripheral stripe since yesterday
/peripheral timeline yesterday  what was on screen, in order, with agent actions
/peripheral open 8              one screen's full text
/peripheral forget 8            remove one screen, its thumbnail and its captured frame
/peripheral forget all
/peripheral pause | resume
```

| Tool | Purpose |
|---|---|
| `screen_capture(note, image)` | Capture the screen **now**, index it, and hand it back (image attached on vision models) |
| `screen_recall(query, since, until, limit)` | Rank screens by the words that were on them, recency-weighted |
| `screen_timeline(since, until, limit)` | What was on screen over a period, interleaved with the agent's tool calls |
| `screen_show(id, chars, image)` | One screen's full text **and the screen itself** when the model can see |
| `screen_index(paths, batch)` | Index now, from the watched folders or explicit paths |
| `screen_forget(id, all)` | Delete rows, thumbnails and captured frames; never your own files |

A bundled skill teaches the agent *when* screen memory beats asking the user, and the
honesty rules for quoting OCR ("the screen shows", never "you meant").

---

## How it works

```
   the display (X11 · Wayland · macOS · Windows)        your screenshot folders
                 |                                                |
                 v                                                |
        capture (116 ms, one frame)                               |
                 |  dHash change gate · hourly ceiling           |
                 v                                                v
        frames/*.webp  ------------> incremental scan ---> Tesseract (1 subprocess, 25 s cap)
                                              |                      |
                                              |                      v
                                              |             clean -> redact -> title
                                              v                      |
                                     mtime+size+sha256               v
                                     bookkeeping -------> shots + FTS5 (porter) + 64-bit dHash
                                                                     |
                                          agent tool calls ---> events (20k capped)
                                                                     v
                                              screen_recall / timeline / show / capture
```

- **Incremental**: a file is only re-read when its mtime or size changed; identical
  content is skipped by SHA-256; a frame within Hamming distance 3 of one already
  stored is dropped, so scrolling a page does not fill the index with forty
  near-copies.
- **Bounded**: a scan OCRs at most `batch` new images (default 60 in the background,
  12 on a lazy query) so a first run over a big folder never blocks a turn. The next
  scan continues.
- **Ranked, not just matched**: FTS5 BM25, re-ranked with recency and text richness;
  a query is sanitized into quoted prefix terms so nothing a user types can turn into
  FTS operators. If a strict search finds nothing, a prefix fallback runs — OCR reads
  `expired_card` as `expired card`, and a query one character off must not look like an
  empty memory.
- **Measured on this machine** (2 vCPU ARM, no GPU): a screen capture takes **116 ms**
  and OCR of the full 1600x1000 page takes **654 ms** (679 characters); a 10-shot
  folder indexes in 10.6 s (about 1.06 s/image). A warm rescan reads zero images.
- **Fails open everywhere**: no capture binary, no Tesseract, unreadable image, locked
  database, bad regex, background thread crash — each is logged, skipped, and the rest
  of the plugin keeps answering.

---

## Privacy, plainly

This plugin looks at your screen, so it is built to be boring about it:

- **No network calls at all.** No telemetry, no sync, no model API for indexing. The
  only thing that ever leaves the machine is whatever the agent chooses to quote in
  its answer.
- **Capture is opt-in and visible.** Background capture is off until you run
  `/peripheral watch on`. When on: changed frames only, a minimum gap, and a hard
  per-hour ceiling. `/peripheral status` always shows whether it is on. The agent can
  always capture on request with `screen_capture`.
- **Redaction on by default**: API keys (`sk-`, `ghp_`, `AKIA`, `AIza`, Slack tokens),
  JWTs, bearer/basic headers, `password:`/`token:` values, credentials inside
  connection URLs (`postgres://user:***@…`), private keys, card numbers and 32+ char
  hex strings are replaced before anything is stored — in the OCR text *and* in the
  agent-event log, which stores tool arguments.
- **You can forget, and it means it**: `screen_forget(id)` / `/peripheral forget <id>`
  deletes the row, the thumbnail, **and the frame the plugin captured**. The
  screenshots you took yourself are never touched — those are yours.
- **You can pause**: `/peripheral pause` stops indexing and capture immediately.
- **Retention**: `retention_days` prunes old entries (and their files) on every scan.
- **Profile-scoped**: the index lives in `<HERMES_HOME>/plugin-data/peripheral/`, one
  per profile, and dies with the plugin if you remove it.

---

## Tests

```bash
uv venv .venv && uv pip install --python .venv/bin/python pytest pillow pyyaml
.venv/bin/python -m pytest tests -q       # 48 passed
hermes plugins validate .                 # Validation passed (security scan: safe)
```

The tests build real PNGs with real text and run the real pipeline: indexing, ranked
retrieval, incremental rescans, duplicate and near-duplicate rejection, retention,
forget (rows **and** thumbnails **and** captured frames), redaction of OCR text and of
agent event details, FTS operator injection, time-expression parsing, `before`/`since`
bounds, the capture backends' argv construction (no shell, no display means no
command), capture dedupe and the change gate, the no-OCR visual index, the multimodal
result envelope, and a manifest contract test that keeps `plugin.yaml` and the code
naming the same things.

The demo is reproducible too:

```bash
Xvfb :99 -screen 0 1600x1000x24 &        # any X display with a browser
DISPLAY=:99 python3 scripts/demo_capture.py     # opens real pages, captures them
HERMES_HOME=/tmp/demo python3 scripts/demo_session.py "what was that card error?"   # real model, real tools
python3 scripts/make_demo_video.py              # renders docs/demo.mp4 from both
```

---

## Limits, stated up front

- OCR is good, not perfect: small text, dark-on-dark UI and unusual fonts degrade. The
  agent quotes what it read and says so; `screen_show` attaches the image when the
  layout is the answer.
- Text inside images (charts, screenshots-of-screenshots, video frames) is indexed as
  text; *understanding* a picture is still a vision model's job — that is why
  `screen_show` can hand it the image.
- Background capture sees the whole display: a password manager or a private tab can
  end up in a frame. It is off by default, forget one screen rather than all, and
  `retention_days` bounds how long anything lives.
- HEIC needs `pillow-heif` in the host environment to be read at all; unsupported
  files are skipped, not retried forever.
- On a headless machine there is nothing to capture; the plugin says so instead of
  failing silently, and still serves the screenshot index.

MIT licensed. Disclosure for the catalog: reads image files under `watch_dirs` and
captures the display only when the user asks or enables background capture; writes one
SQLite database, WebP thumbnails and captured frames under
`<HERMES_HOME>/plugin-data/peripheral/`; runs the local `tesseract` binary and a local
capture binary with timeouts; makes no network calls; never modifies or deletes the
user's own files.
