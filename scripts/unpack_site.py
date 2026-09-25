#!/usr/bin/env python3
"""Verify the bot's release archive and unpack it without links or path escapes."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


def unpack(archive, destination, record):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", record.get("version") or ""):
        raise ValueError("No published package release is configured")
    expected_name = f"linux-packages-{record['version']}.tar.gz"
    if record.get("archive") != expected_name or Path(archive).name != expected_name:
        raise ValueError("Release archive filename does not match the release record")
    with Path(archive).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != record.get("sha256"):
        raise ValueError("Package repository archive failed SHA-256 verification")
    if Path(destination).exists():
        raise ValueError("Extraction directory must be new")
    with tarfile.open(archive) as source:
        members = source.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile() or member.name in names:
                raise ValueError("Repository archive contains an unsafe or duplicate entry")
            names.add(member.name)
        if sum(m.size for m in members) > 950_000_000:
            raise ValueError("Repository archive exceeds the deployment size budget")
        required = {"signing-key.asc", "signing-key-fingerprint.txt", "apt/dists/stable/InRelease"}
        if not required.issubset(names):
            raise ValueError("Repository archive is incomplete")
        source.extractall(destination, members=members, filter="data")
    fingerprint = (Path(destination) / "signing-key-fingerprint.txt").read_text().strip()
    if fingerprint != record.get("signing_fingerprint"):
        raise ValueError("The signing key differs from the release record")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    unpack(args.archive, args.destination, json.loads((root / "release.json").read_text()))
