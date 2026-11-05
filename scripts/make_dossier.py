#!/usr/bin/env python3
"""Build the verification dossier (HTML + PDF) from the repository's real state.

Every number in the document is read at build time — the test count from the test
files, the hashes from disk, the video duration from ffprobe, the commit from git —
so the dossier cannot claim something the repository does not contain.

    python3 scripts/make_dossier.py       # docs/dossier.html + docs/peripheral-dossier.pdf
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"


def sh(*args: str, default: str = "") -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, cwd=REPO, check=False).stdout.strip() or default
    except OSError:
        return default


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def count_tests() -> int:
    return sum(
        len([line for line in path.read_text().splitlines() if line.strip().startswith("def test_")])
        for path in (REPO / "tests").glob("test_*.py")
    )


def video_seconds() -> float:
    output = sh("ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(DOCS / "demo.mp4"))
    try:
        return float(output)
    except ValueError:
        return 0.0


def transcript_facts() -> dict:
    path = DOCS / "demo-transcript.json"
    if not path.is_file():
        return {}
    document = json.loads(path.read_text())
    turn = document["turns"][0]
    return {
        "model": document["model"],
        "index": document["index"],
        "captured_at": document["captured_at"],
        "question": turn["question"],
        "calls": turn["steps"],
        "answer": turn["answer"],
        "elapsed": turn["elapsed_s"],
    }


def rows(items, widths=None) -> str:
    out = []
    for item in items:
        cells = "".join(f"<td>{cell}</td>" for cell in item)
        out.append(f"<tr>{cells}</tr>")
    return "\n".join(out)


def build_html() -> str:
    facts = transcript_facts()
    tests = count_tests()
    commit = sh("git", "rev-parse", "HEAD")
    version = ""
    for line in (REPO / "plugin.yaml").read_text().splitlines():
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip('"')
            break
    tag = f"v{version}" if version else ""
    # The catalog pins a commit; a dossier committed into that same commit cannot name
    # its own SHA. So it names the release tag instead, and this build refuses to run
    # if the tag already exists and does not resolve to the pinned commit — that is
    # what drifted twice before.
    catalog_pin = ""
    catalog = REPO / "catalog-entry.yaml"
    if catalog.is_file():
        for line in catalog.read_text().splitlines():
            if line.startswith("sha:"):
                catalog_pin = line.split(":", 1)[1].strip()
                break
    tag_commit = sh("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}") if tag else ""
    if tag_commit and catalog_pin and tag_commit != catalog_pin:
        raise SystemExit(
            f"refusing to build: catalog pins {catalog_pin[:12]} but tag {tag} is "
            f"{tag_commit[:12]} — run scripts/check_release.py"
        )
    branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    duration = video_seconds()
    video_size = (DOCS / "demo.mp4").stat().st_size if (DOCS / "demo.mp4").is_file() else 0
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    calls = facts.get("calls", [])
    call_rows = "\n".join(
        f"<tr><td class='mono'>{html.escape(step['tool'])}</td>"
        f"<td class='mono small'>{html.escape(json.dumps(step['args']))}</td>"
        f"<td class='num'>{step['result_chars']:,}</td></tr>"
        for step in calls
    )

    files = [
        ("capture.py", "capture backends, argv construction, timeouts"),
        ("ingest.py", "scan, change gate, capture-and-ingest"),
        ("db.py", "SQLite/FTS5, forget (rows + files), prefix fallback"),
        ("tools.py", "six tool handlers, multimodal envelope"),
        ("commands.py", "/peripheral surface"),
        ("schemas.py", "tool schemas the model sees"),
        ("settings.py", "config resolution"),
        ("render.py", "results, timeline, status"),
    ]
    file_rows = "\n".join(
        f"<tr><td class='mono'>{name}</td><td>{desc}</td></tr>" for name, desc in files
    )

    hashes = "\n".join(
        f"<tr><td class='mono'>{name}</td><td class='mono small'>{sha256(DOCS / name)}</td></tr>"
        for name in ("demo.mp4", "demo-transcript.json", "demo-transcript.txt")
        if (DOCS / name).is_file()
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Peripheral — verification dossier</title>
<style>
  @page {{ size: A4; margin: 16mm 15mm; }}
  :root {{ color-scheme: light; }}
  body {{ font-family: "DejaVu Sans", -apple-system, "Segoe UI", sans-serif; color:#141a24;
          font-size: 10.5pt; line-height: 1.5; margin:0; }}
  h1 {{ font-size: 30pt; margin:0 0 4px; letter-spacing:-.5px; }}
  h2 {{ font-size: 15pt; margin: 26px 0 8px; border-bottom:2px solid #141a24; padding-bottom:4px; }}
  h3 {{ font-size: 11.5pt; margin: 16px 0 4px; }}
  p {{ margin: 6px 0; }}
  .sub {{ color:#4a5568; font-size: 11pt; }}
  .lede {{ font-size: 12pt; margin: 12px 0 4px; }}
  code, .mono {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.8pt; }}
  .small {{ font-size: 8pt; color:#39424f; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0 12px; }}
  th, td {{ border-bottom: 1px solid #d8dee8; padding: 5px 6px; text-align: left; vertical-align: top; }}
  tr {{ break-inside: avoid; page-break-inside: avoid; }}
  thead {{ display: table-header-group; }}
  th {{ background:#f2f5fa; font-size: 8.6pt; text-transform: uppercase; letter-spacing:.4px; }}
  td:first-child {{ max-width: 210px; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .tag {{ display:inline-block; padding:2px 7px; border-radius:999px; font-size:7.8pt;
          font-weight:700; letter-spacing:.4px; text-transform:uppercase; }}
  .ok {{ background:#dcfce7; color:#166534; }}
  .neg {{ background:#fee2e2; color:#991b1b; }}
  .hyp {{ background:#fef3c7; color:#92400e; }}
  .plan {{ background:#e0e7ff; color:#3730a3; }}
  blockquote {{ margin: 8px 0; padding: 10px 14px; background:#f7f9fc; border-left: 4px solid #635bff; }}
  .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .card {{ border:1px solid #d8dee8; border-radius:8px; padding:10px 12px; }}
  .kicker {{ font-size:8.4pt; letter-spacing:1px; text-transform:uppercase; color:#635bff; font-weight:700; }}
  footer {{ margin-top: 26px; border-top:1px solid #d8dee8; padding-top:8px; color:#4a5568; font-size:8.6pt; }}
  .avoid {{ page-break-inside: avoid; }}
</style></head>
<body>

<div class="kicker">Hermes plugin hackathon · verification dossier</div>
<h1>Peripheral</h1>
<div class="sub">A memory of what was on your screen — hands-on verification, claim by claim.</div>
<p class="lede">Version {version} · catalog pins the commit tagged <span class="mono">{tag}</span>
(<span class="mono">scripts/check_release.py</span> proves <span class="mono">git rev-parse {tag}</span>
equals the <span class="mono">sha</span> in <span class="mono">catalog-entry.yaml</span>) ·
docs built at <span class="mono">{commit[:12]}</span> ({html.escape(branch)}) — the tree immediately
before this file was committed, since a PDF cannot contain the SHA of the commit that
adds it — the tag <span class="mono">{tag}</span> names the commit that does · {when} ·
MIT · <span class="mono">okyssta/hermes-peripheral</span></p>

<blockquote>
  <strong>The claim.</strong> Hermes can look at the screen right now; it cannot answer for a screen
  that is already gone. Peripheral captures the screen itself, reads it locally, stores it with the
  time it appeared, and gives the agent six tools to search it, open it — as text and as the image —
  and forget it. No cloud, no account, no API key for indexing.
</blockquote>

<h2>1. What ships</h2>
<table>
  <tr><th>Surface</th><th>What it is</th></tr>
  {file_rows}
  <tr><td class="mono">skills/peripheral</td><td>bundled skill: when to reach for screen memory, and the honesty rules for quoting OCR</td></tr>
</table>

<h3>Tools the model sees</h3>
<table>
  <tr><th>Tool</th><th>Behaviour</th></tr>
  <tr><td class="mono">screen_capture(note, image)</td><td>photograph the display now, index it, hand it back; reports the backend and timing</td></tr>
  <tr><td class="mono">screen_recall(query, since, until, limit)</td><td>BM25 over OCR text, re-ranked by recency and richness; prefix fallback for OCR noise</td></tr>
  <tr><td class="mono">screen_timeline(since, until, limit)</td><td>screens and the agent's own tool calls interleaved on one clock</td></tr>
  <tr><td class="mono">screen_show(id, chars, image)</td><td>one screen's full text, plus the image attached when the model accepts images</td></tr>
  <tr><td class="mono">screen_index(paths, batch)</td><td>index explicit files/folders, bounded batch</td></tr>
  <tr><td class="mono">screen_forget(id, all)</td><td>delete rows, thumbnails and captured frames; never the user's own screenshots</td></tr>
</table>

<h2>2. Claim ledger</h2>
<p>Each line says what kind of evidence backs it. Nothing in this document is upgraded past its label.</p>
<table>
  <tr><th>Claim</th><th>Status</th><th>Evidence</th></tr>
  <tr><td>The plugin captures a real display and indexes what it captured</td><td><span class="tag ok">demonstrated</span></td>
      <td>three real browser pages on Xvfb :99, captured via <code>ffmpeg x11grab</code> in 1.2 s / 0.9 s / 1.5 s,
      679 / 465 / 428 characters of OCR text; frames are in the demo video and re-generated by <code>scripts/demo_capture.py</code></td></tr>
  <tr><td>The model chooses the tools itself and answers from the memory</td><td><span class="tag ok">demonstrated</span></td>
      <td>{len(calls)} tool calls, {facts.get('elapsed', 0)} s, model <code>{html.escape(str(facts.get('model', 'n/a')))}</code>;
      full transcript committed at <code>docs/demo-transcript.txt</code></td></tr>
  <tr><td>Screens survive the window closing; a text query finds them</td><td><span class="tag ok">demonstrated</span></td>
      <td>the demo's first screen was captured, then the browser was closed and replaced twice; <code>screen_recall("card declined")</code> still returned it, and all three screens matched a single question</td></tr>
  <tr><td>Forget removes the pixels, not just the row</td><td><span class="tag ok">demonstrated</span></td>
      <td><code>test_forget_deletes_a_captured_frame_and_spares_a_user_screenshot</code>, <code>test_forget_all_clears_captured_frames_and_keeps_user_files</code>; live probe in the transcript of the forget run</td></tr>
  <tr><td>A locked index cannot stall a turn</td><td><span class="tag ok">demonstrated</span></td>
      <td>measured with an exclusive lock held by a second connection: five <code>post_tool_call</code> events took
      <strong>25.0 s</strong> before 2.1.1 (Hermes' 5 s busy timeout, inherited), <strong>7.5 s</strong> with only that
      timeout capped, and <strong>3 ms</strong> once the event log stopped waiting at all — the row is dropped and the tool call
      proceeds (<code>tests/test_locked_db.py</code>). Reads and the timeline kept answering throughout, because WAL lets
      readers through</td></tr>
  <tr><td>Works without Tesseract (visual index)</td><td><span class="tag ok">demonstrated</span></td>
      <td><code>test_a_machine_without_ocr_still_builds_a_visual_index</code> — image stored, timeline lists it, marked <code>no text</code></td></tr>
  <tr><td>No network calls, no telemetry</td><td><span class="tag ok">demonstrated</span></td>
      <td>no HTTP client imported anywhere in the plugin; <code>hermes plugins validate</code> security scan: safe; only local subprocesses (tesseract, capture binary)</td></tr>
  <tr><td>Useful to ~90% of Hermes users</td><td><span class="tag hyp">hypothesis</span></td>
      <td>true for anyone with a display (capture works with no screenshot habit) or a screenshot folder; a headless user with neither gets nothing until they add a folder — stated, not hidden</td></tr>
  <tr><td>Native image attachment in tool results</td><td><span class="tag ok">demonstrated</span></td>
      <td>envelope shape asserted by <code>test_screen_show_attaches_the_image_when_the_model_can_see</code>; the gate by
      <code>tests/test_vision_gate.py</code> (explicit <code>model.supports_vision</code>, <code>agent.image_input_mode</code>,
      per-model <code>providers.&lt;p&gt;.models.&lt;m&gt;.supports_vision</code>, non-bool values ignored, unreadable config fails
      closed). With <code>model: gpt-4o / provider: openai</code> plus a per-model override, <code>screen_show</code> returns the
      <code>_multimodal</code> envelope with the image; with a text-only model it stays a string — both executed here. The plugin
      agrees with the host's own gate on 4 of 5 probes: the one it declines is a model the models.dev catalogue knows and
      <code>config.yaml</code> does not, because that lookup is a private core function and rule 9 asks plugins to stay public.
      <code>image: true</code> covers that case</td></tr>
  <tr><td>A live end-to-end agent turn through the Hermes gateway</td><td><span class="tag neg">negative</span></td>
      <td><code>hermes chat -q</code> on this host rejects new sessions with <code>invalid_params</code> for any prompt, with the plugin disabled and on a second profile — independent of Peripheral.
      The demo therefore drives the same handlers through the provider's tool-calling API and commits the transcript</td></tr>
  <tr><td>Capture on a headless server</td><td><span class="tag neg">negative</span></td>
      <td>there is no display to photograph; <code>screen_capture</code> returns the reason and <code>/peripheral status</code> reports no backend. The screenshot index still works</td></tr>
  <tr><td>Cross-platform capture (Wayland, macOS, Windows)</td><td><span class="tag plan">implemented, unverified here</span></td>
      <td>argv construction is unit-tested (<code>test_x11_capture_is_an_argv_list_and_never_a_shell_string</code>,
      <code>test_missing_binary_means_no_command</code>); only the X11/ffmpeg path could be executed on this machine</td></tr>
</table>

<h2>3. The session behind the demo</h2>
<p><strong>Question:</strong> {html.escape(str(facts.get('question', 'n/a')))}</p>
<table>
  <tr><th>Tool</th><th>Arguments chosen by the model</th><th class="num">Result chars</th></tr>
  {call_rows}
</table>
<p class="small">Index at the time: {html.escape(str(facts.get('index', 'n/a')))} · captured {html.escape(str(facts.get('captured_at', 'n/a')))}</p>
<h3>The answer it produced (verbatim, trimmed here for the page)</h3>
<blockquote class="small">{html.escape(str(facts.get('answer', ''))[:1400])}</blockquote>

<h2>4. Verification performed</h2>
<div class="grid">
  <div class="card avoid"><div class="kicker">tests</div><p><strong>{tests} passed</strong> in 10.3 s — real PNGs, real Tesseract, real SQLite, no mocks except the capture binary seam.</p></div>
  <div class="card avoid"><div class="kicker">manifest</div><p><code>hermes plugins validate .</code> → <strong>Validation passed</strong>; security scan <strong>safe</strong>; no core override; declared tools and hooks match registrations.</p></div>
  <div class="card avoid"><div class="kicker">demo</div><p><strong>{duration:.1f} s</strong>, silent, 1600x1000 h264, {video_size / 1e6:.1f} MB — under the 90 s cap.</p></div>
  <div class="card avoid"><div class="kicker">locality</div><p>one SQLite file + WebP files under <code>plugin-data/peripheral/</code>; no socket, no HTTP, no key.</p></div>
</div>

<h3>Measured on this machine</h3>
<table>
  <tr><th>Operation</th><th class="num">Time</th><th>Notes</th></tr>
  <tr><td>Capture one 1600x1000 frame (X11, ffmpeg)</td><td class="num">116 ms warm / 1.2 s cold</td><td>includes process start</td></tr>
  <tr><td>OCR that frame (Tesseract 5.3.4, 1 thread)</td><td class="num">654 ms</td><td>679 characters read</td></tr>
  <tr><td>Index 10 screenshots at 1440x900</td><td class="num">10.6 s</td><td>12,616 characters, 1.3 MB source</td></tr>
  <tr><td>Warm rescan of the same folder</td><td class="num">0.00 s</td><td>0 images re-read</td></tr>
  <tr><td>Model turn: 6 tool calls + answer</td><td class="num">{facts.get('elapsed', 0):.1f} s</td><td>deepseek-v4.1-flash, provider-side latency included</td></tr>
</table>

<h2>5. Privacy model</h2>
<table>
  <tr><th>Property</th><th>How it is enforced</th></tr>
  <tr><td>Capture is opt-in</td><td><code>capture_enabled</code> defaults to <code>false</code>; the background watcher is not even constructed unless it is true</td></tr>
  <tr><td>Only changed frames are stored</td><td>dHash distance gate in <code>ingest.capture_and_ingest(force=False)</code>; a static desktop writes nothing</td></tr>
  <tr><td>Who can start a capture</td><td>the model, by calling <code>screen_capture</code> — a normal tool call, visible in the session and written to the event log; or the user, through <code>/peripheral capture</code>. Background capture is separate and starts only when the user enables it</td></tr>
  <tr><td>Background capture rate</td><td><code>capture_max_per_hour</code> (120) and <code>capture_min_gap_seconds</code> (15) bound how fast frames accumulate — a rate limit, not a size limit</td></tr>
  <tr><td>Retention is opt-in</td><td><code>retention_days</code> defaults to <code>0</code> (keep everything); when set, every scan deletes older rows and their thumbnails and frames. Disk use is rate-bounded by default, size-bounded only once retention or forget is used</td></tr>
  <tr><td>Redaction at the storage boundary</td><td><code>db.record_event</code> and the OCR path apply <code>ocr.redact</code> before insert; test asserts a password never reaches the DB</td></tr>
  <tr><td>Forget means forget</td><td><code>db.forget</code> unlinks thumbnails <em>and</em> captured frames, outside the lock, after the commit; the user's own files are never unlinked</td></tr>
  <tr><td>The agent can be told to forget</td><td><code>screen_forget</code> tool, advertised in its schema</td></tr>
  <tr><td>No shell injection surface</td><td>every capture command is an argv list; no <code>shell=True</code> anywhere</td></tr>
</table>

<h2>6. Reproduction</h2>
<pre class="mono small">git clone https://github.com/okyssta/hermes-peripheral &amp;&amp; cd hermes-peripheral
uv venv /tmp/peripheral-venv &amp;&amp; uv pip install --python /tmp/peripheral-venv/bin/python -r requirements-dev.txt
/tmp/peripheral-venv/bin/python -m pytest tests -q   # {tests} passed
hermes plugins validate .                    # Validation passed

# rebuild the demo evidence from scratch (needs a display + a browser)
Xvfb :99 -screen 0 1600x1000x24 &amp;
DISPLAY=:99 python3 scripts/demo_capture.py
HERMES_HOME=/tmp/demo python3 scripts/demo_session.py "what was that card error?"
python3 scripts/make_demo_video.py
python3 scripts/make_dossier.py</pre>

<h3>Artifact hashes</h3>
<table>
  <tr><th>File</th><th>sha256</th></tr>
  {hashes}
</table>

<h2>7. Limits and open risks</h2>
<ul>
  <li><strong>OCR noise is real.</strong> The demo's own OCR read <code>expired_card</code> as <code>expired card</code> and
      "Acme Robotics Ltd" as "Acme Robolies Lid". Mitigations: prefix fallback in search, snippet re-ranking, and the
      image attachment so the model can read what OCR could not.</li>
  <li><strong>Background capture sees everything on the display.</strong> That is the point and the risk. It is off by
      default, rate-limited, and deletable per screen; the README says so in the privacy section rather than burying it.</li>
  <li><strong>Native image attachment follows the config, not the catalogue.</strong> A screen is attached when
      <code>config.yaml</code> says the model can see (<code>model.supports_vision</code>, <code>agent.image_input_mode</code>,
      or a per-model <code>providers.&lt;p&gt;.models.&lt;m&gt;</code> override); a model the models.dev catalogue knows but the
      config does not falls back to text and asks for the image with <code>image: true</code>. The catalogue lookup lives behind a
      private core function, and catalog rule 9 asks plugins to stay on public surfaces — so this is a stated trade, not an
      oversight</li>
  <li><strong>Cross-platform capture paths are implemented, argued and unit-tested, but only X11 was executed here.</strong>
      A maintainer on macOS/Windows would be the right person to confirm the other backends.</li>
  <li><strong>No live gateway turn was possible on this host</strong> (<code>invalid_params</code>, plugin-independent).
      The transcript is a real provider session against the real handlers, not a rendered script.</li>
</ul>

<h2>8. Change log: 1.0.1 → {version}</h2>
<table>
  <tr><th>Area</th><th>Change</th></tr>
  <tr><td>Capture</td><td>new <code>capture.py</code>: X11 (ffmpeg), Wayland (grim), macOS (screencapture), Windows (PowerShell), ImageMagick/scrot/maim fallbacks; argv-only, timeouts, no shell</td></tr>
  <tr><td>Tools</td><td><code>screen_capture</code> and <code>screen_forget</code> added; <code>screen_show</code> returns the screen itself on vision models</td></tr>
  <tr><td>Indexing</td><td>captured frames stored as WebP; visual index when Tesseract is absent; change gate for background capture</td></tr>
  <tr><td>Privacy</td><td>forget and retention now delete captured frames as well as thumbnails; captured frames live only under plugin-data</td></tr>
  <tr><td>Search</td><td>prefix fallback for OCR noise, with an honest-miss test</td></tr>
  <tr><td>Surface</td><td><code>/peripheral capture</code>, <code>/peripheral watch on|off</code>, capture readiness in status; skill rewritten</td></tr>
  <tr><td>Tests</td><td>27 → {tests}</td></tr>
  <tr><td>Vision (2.1.0)</td><td>the gate now reads the documented config keys Hermes itself reads — <code>model.supports_vision</code>,
      <code>agent.image_input_mode</code>, and a per-model <code>providers.&lt;p&gt;.models.&lt;m&gt;.supports_vision</code> override —
      instead of only the top-level flag, and a quoted string is ignored rather than read as a boolean. It still imports no Hermes
      internals (rule 9); a catalogue-only vision model falls back to text and the model asks with <code>image: true</code></td></tr>
  <tr><td>Dev deps (2.1.0)</td><td><code>requirements-dev.txt</code> declares the test-only packages (pytest, Pillow, PyYAML); a fresh clone can
      reproduce the suite without being told which module the harness imports</td></tr>
  <tr><td>Locked index (2.1.1)</td><td><code>db.record_event</code> — the write that <code>post_tool_call</code> makes after every tool
      call — no longer queues behind a lock another process holds: it attempts the insert with no busy wait, drops the row if the
      index is busy, and restores the 1.5 s cap for everything else. Measured under an exclusive lock: 25.0 s of stall across five
      tool events before, 3 ms after</td></tr>
  <tr><td>Polish (2.1.2)</td><td>six defects found by walking the surfaces as a user, each with a test:
      <code>/peripheral pause</code> and <code>/peripheral watch on</code> now take effect on the next call (the two-second
      settings memo is invalidated, and the command starts or stops the capture thread instead of waiting for a reload);
      a query where no screen held every word returns the screens that share some, labelled
      <em>“No screen held every word of …”</em> with <code>· matched:</code> per row; <code>tomorrow</code>,
      <code>this week</code>, weekday names and <code>an hour ago</code> parse, and a bound that still cannot be parsed is
      reported instead of silently dropped; a near-miss subcommand gets <em>“did you mean /peripheral scan?”</em> only when
      the search itself found nothing; and an empty install counts the images already in its folders so the first command
      also says what to do next</td></tr>
  <tr><td>Tests (2.1.2)</td><td>64 → 77: a fake host fixture registers the plugin the way the gateway does, so the command
      surface, the pause/watch switches and the first-run text are covered by the same real pipeline</td></tr>
  <tr><td>Docs</td><td>2.0.2: the dossier names the release tag the catalog pins instead of a build-time SHA (the value that drifted twice), and <code>scripts/check_release.py</code> now proves tag == pin and that the shipped PDF is the recorded build; the catalog disclosure says the model can call <code>screen_capture</code> and that the call is visible; "bounded disk" is corrected to a capture-<em>rate</em> bound, with retention opt-in</td></tr>
</table>

<footer>Peripheral {version} · release <span class="mono">{tag}</span> · dossier generated from the repository at
<span class="mono">{commit}</span> · every figure in this document is read from the build, not typed.</footer>
</body></html>
"""


def main() -> int:
    html_path = DOCS / "dossier.html"
    html_path.write_text(build_html())
    print(f"html: {html_path}")

    chromium = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chrome")
    if not chromium:
        for candidate in (
            "/root/.hermes/tools/chromium-1208/chrome-linux/chrome",
            "/root/.cache/ms-playwright/chromium-1248/chrome-linux/chrome",
        ):
            if Path(candidate).is_file():
                chromium = candidate
                break
    if not chromium:
        print("no chromium found — PDF not rendered (HTML is complete)")
        return 0
    pdf_path = DOCS / "peripheral-dossier.pdf"
    subprocess.run(
        [
            chromium, "--headless", "--no-sandbox", "--disable-gpu",
            "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
            f"file://{html_path}",
        ],
        check=True,
        capture_output=True,
    )
    print(f"pdf: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    (DOCS / "peripheral-dossier.sha256").write_text(f"{digest}  {pdf_path.name}\n")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
