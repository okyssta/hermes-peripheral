"""Probe the real pipeline against the demo screenshot folder (no gateway needed)."""
import os, sys, time
from pathlib import Path

os.environ.setdefault("HERMES_HOME", "/tmp/peripheral-probe-home")
sys.path.insert(0, "/root")
sys.path.insert(0, "/root/.hermes/hermes-agent")

from peripheral import db, ingest, render, tools
from peripheral.settings import Settings

folder = Path("/root/demo-screens/Screenshots")
settings = Settings(watch_dirs=(folder,), include_photos=False, auto_scan_minutes=0, record_agent_events=True)

started = time.time()
report = ingest.scan(settings, batch=40)
print(render.scan_result(report, prefix="scan"))
print("elapsed", round(time.time() - started, 2), "s")
print("stats", db.stats())
print()

for query in ("stripe expired card", "assertionerror 502", "unrecognised transaction", "retry budget", "estimated charges"):
    hits = db.search(query, limit=3)
    print(f"--- {query!r}: {len(hits)} hits")
    for hit in hits[:2]:
        print(f"    [{hit.id}] {hit.when} {hit.title[:60]!r} score={hit.score:.2f}")
        print(f"        {' '.join(hit.snippet.split())[:150]}")
print()
shots = db.recent(5)
print(render.timeline(shots, [], title="Recent"))
print()
one = db.search("expired card", limit=1)
if one:
    full = db.get(one[0].id) or one[0]
    print(render.record(full, body_chars=700))
