#!/usr/bin/env python3
"""Check the release invariants that drifted twice: version, pin and shipped docs.

The catalog entry pins a commit; the dossier lives inside that commit and therefore
cannot print its own SHA. The dossier names the release tag instead, and this script
is the proof that the two agree:

    git rev-parse v<version>  ==  sha:  in catalog-entry.yaml

It also verifies that the PDF in the tree is the PDF those sources describe — its
own checksum is recorded next to it, its pin line names the same tag, and the artifact
hashes printed inside it match the files on disk. Nothing here touches the network.

    python3 scripts/check_release.py            # exit 0 = consistent
    python3 scripts/check_release.py --pre-tag  # before v<version> exists
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
PDF = DOCS / "peripheral-dossier.pdf"
PDF_SUM = DOCS / "peripheral-dossier.sha256"


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO, check=False).stdout.strip()


def field(path: Path, key: str) -> str:
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-tag", action="store_true", help="the release tag does not exist yet")
    args = parser.parse_args()

    failures: list[str] = []
    notes: list[str] = []

    version = field(REPO / "plugin.yaml", "version")
    entry_version = field(REPO / "catalog-entry.yaml", "version")
    pin = field(REPO / "catalog-entry.yaml", "sha")
    tag = f"v{version}" if version else ""

    if not version:
        failures.append("plugin.yaml has no version")
    if entry_version != version:
        failures.append(f"catalog version {entry_version!r} != plugin.yaml version {version!r}")
    else:
        notes.append(f"version {version} agrees in plugin.yaml and catalog-entry.yaml")

    if not pin:
        failures.append("catalog-entry.yaml has no sha")
    tag_commit = sh("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}") if tag else ""
    if not tag_commit:
        message = f"tag {tag} does not exist"
        if args.pre_tag:
            notes.append(message + " (--pre-tag: skipping the pin check)")
        else:
            failures.append(message + " — tag the release commit before pinning the catalog")
    elif pin and tag_commit != pin:
        failures.append(f"catalog pins {pin[:12]} but tag {tag} is {tag_commit[:12]}")
    else:
        notes.append(f"catalog pin {pin[:12]} is exactly tag {tag}")

    if not PDF.is_file():
        failures.append("docs/peripheral-dossier.pdf is missing")
        return report(notes, failures, tag)

    recorded = PDF_SUM.read_text().split()[0] if PDF_SUM.is_file() else ""
    actual = sha256(PDF)
    if not recorded:
        failures.append("docs/peripheral-dossier.sha256 is missing — no way to tell which build shipped")
    elif recorded != actual:
        failures.append(f"committed PDF checksum {recorded[:12]} != on-disk PDF {actual[:12]}")
    else:
        notes.append(f"shipped PDF is the recorded build ({actual[:12]}, {PDF.stat().st_size:,} bytes)")

    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        notes.append("pdftotext not installed: PDF text checks skipped")
        return report(notes, failures, tag)

    text = subprocess.run(
        [pdftotext, "-layout", str(PDF), "-"], capture_output=True, text=True, check=False
    ).stdout
    flat = re.sub(r"\s+", "", text)
    if tag and tag not in text:
        failures.append(f"the PDF never names {tag}")
    elif tag:
        notes.append(f"the PDF names {tag}")
    if version and f"Version {version}" not in text:
        failures.append(f"the PDF does not say 'Version {version}'")
    if re.search(r"catalog pin\s+[0-9a-f]{7,}", text):
        failures.append("the PDF still prints a commit SHA as the pin — it will drift again")

    for name in ("demo.mp4", "demo-transcript.json", "demo-transcript.txt"):
        path = DOCS / name
        if not path.is_file():
            continue
        digest = sha256(path)
        if digest[:16] not in flat:
            failures.append(f"the PDF's sha256 for {name} does not match the file on disk")
        else:
            notes.append(f"{name} hash in the PDF matches the file")

    return report(notes, failures, tag)


def report(notes: list[str], failures: list[str], tag: str) -> int:
    for note in notes:
        print(f"  ok   {note}")
    for failure in failures:
        print(f"  FAIL {failure}")
    if failures:
        print(f"\nrelease check failed ({len(failures)} problem(s))")
        return 1
    print(f"\nrelease check passed: {tag} is what the catalog pins and what the dossier describes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
