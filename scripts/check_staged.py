#!/usr/bin/env python3
"""Validate the small set of files the release bot is allowed to commit."""
import json
from pathlib import Path
import re
import subprocess

paths = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True).splitlines()
if not set(paths).issubset({"release.json", "README.md"}):
    raise SystemExit("Unexpected staged path in release update")
for path in paths:
    contents = subprocess.check_output(["git", "show", f":{path}"], text=True)
    if re.search(r"/(?:home|Users)/[^\s/]+/|-----BEGIN .*PRIVATE KEY|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|Co-Authored-By:|Generated with", contents, re.I):
        raise SystemExit(f"Privacy/identity scan failed for {path}")
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contents)
    if any(not email.endswith("@users.noreply.github.com") and not email.endswith("@vapourware-studios.net") for email in emails):
        raise SystemExit(f"Unexpected email in {path}")
record = json.loads(Path("release.json").read_text())
if not re.fullmatch(r"\d+\.\d+\.\d+", record.get("version") or ""):
    raise SystemExit("Invalid release version")
if record.get("signing_fingerprint") != Path("signing-key-fingerprint.txt").read_text().strip():
    raise SystemExit("Release does not use the pinned signing key")
if not re.fullmatch(r"[a-f0-9]{64}", record.get("sha256", "")):
    raise SystemExit("Invalid archive checksum")
if record.get("source") != f"https://github.com/Vapourware-Studios/sshclient/releases/tag/v{record['version']}":
    raise SystemExit("Unexpected release source")
for filename, checksum in record.get("source_checksums", {}).items():
    if not re.fullmatch(r"sshclient-[0-9.]+-linux-[a-z0-9_]+\.(?:deb|rpm|pkg\.tar\.zst)", filename) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise SystemExit("Invalid source package record")
if not isinstance(record.get("redirects"), int) or record["redirects"] < 1:
    raise SystemExit("Release records no package redirects")
# rpmsign rewrites the RPM, so the signed copies are republished here and must
# be recorded with the checksums the repository metadata actually indexes.
signed = record.get("signed_rpms") or {}
if not signed:
    raise SystemExit("Release records no signed RPMs")
for filename, checksum in signed.items():
    if not re.fullmatch(r"sshclient-[0-9.]+-linux-[a-z0-9_]+\.rpm", filename) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise SystemExit("Invalid signed RPM record")
print("Staged release privacy and identity checks passed")
