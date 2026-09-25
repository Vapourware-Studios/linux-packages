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
        if sum(m.size for m in members) > 20_000_000:
            raise ValueError("Repository archive exceeds the deployment size budget")
        required = {"signing-key.asc", "signing-key-fingerprint.txt",
                    "apt/dists/stable/InRelease", "manifest.json"}
        if not required.issubset(names):
            raise ValueError("Repository archive is incomplete")
        # Payloads are served by redirect, never from the worker's own assets.
        if any(name.endswith((".deb", ".rpm", ".pkg.tar.zst")) for name in names):
            raise ValueError("Repository archive must not contain package payloads")
        # Check the redirect map before anything reaches the deployment
        # directory, so a bad target can never be served even briefly.
        manifest = json.loads(source.extractfile("manifest.json").read())
        if manifest.get("version") != record["version"] or not manifest.get("redirects"):
            raise ValueError("The redirect map does not match the release record")
        for target in manifest["redirects"].values():
            if not re.fullmatch(r"https://github\.com/Vapourware-Studios/(?:sshclient|linux-packages)"
                                r"/releases/download/v[0-9.]+/[^/\s]+", target):
                raise ValueError(f"Redirect leaves the project's releases: {target}")
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
