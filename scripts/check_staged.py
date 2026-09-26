#!/usr/bin/env python3
"""Validate the exact public files staged by the release bot."""
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

from build_repository import validate_public_key


def staged(path):
    return subprocess.check_output(["git", "show", f":{path}"])


paths = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True).splitlines()
for path in paths:
    metadata = re.fullmatch(r"metadata/rpm/(?:x86_64|aarch64)/repodata/(?:repomd\.xml(?:\.asc)?|[a-f0-9]+-(?:primary|filelists|other)\.xml\.gz)", path)
    if path not in {"release.json", "README.md", "signing-key.asc"} and not metadata:
        raise SystemExit(f"Unexpected staged path: {path}")
    contents = staged(path)
    if len(contents) > 2_000_000:
        raise SystemExit(f"Oversized metadata: {path}")
    if path.endswith(".gz"):
        contents = gzip.decompress(contents)
    text = contents.decode()
    if re.search(r"/(?:home|Users)/[^\s/]+/|-----BEGIN .*PRIVATE KEY|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|Co-Authored-By:|Generated with", text, re.I):
        raise SystemExit(f"Privacy/identity scan failed for {path}")
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if any(not email.endswith("@users.noreply.github.com") and not email.endswith("@vapourware-studios.net") for email in emails):
        raise SystemExit(f"Unexpected email in {path}")
record = json.loads(staged("release.json"))
fingerprint = Path("signing-key-fingerprint.txt").read_text().strip()
validate_public_key(staged("signing-key.asc"), fingerprint)
if record.get("schema") != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", record.get("version") or ""):
    raise SystemExit("Invalid release version/schema")
if record.get("signing_fingerprint") != fingerprint:
    raise SystemExit("Release does not use the pinned signing key")
if record.get("source") != f"https://github.com/Vapourware-Studios/sshclient/releases/tag/v{record['version']}":
    raise SystemExit("Unexpected release source")
for filename, digest in record.get("source_checksums", {}).items():
    if not re.fullmatch(r"sshclient-[0-9.]+-linux-[a-z0-9_]+\.(?:deb|rpm|pkg\.tar\.zst)", filename) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise SystemExit("Invalid source package record")
print("Staged release privacy and identity checks passed")
