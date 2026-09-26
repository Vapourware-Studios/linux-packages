#!/usr/bin/env python3
"""Publish packages before committing the signed indexes that reference them."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from build_repository import PACKAGES, ROOT, checksum, should_publish, version_tuple


def run(*args, capture=False):
    result = subprocess.run(args, cwd=ROOT, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def upload_order(file):
    if file.name.endswith((".deb", ".rpm", ".pkg.tar.zst", ".pkg.tar.zst.sig")):
        return 0
    if file.name == "InRelease":
        return 3
    if file.name.endswith((".sig", ".gpg")):
        return 2
    return 1


def publish_assets(assets, invoke=run):
    # The versioned RPM packages must exist before their metadata reaches Git.
    for directory in sorted(assets.iterdir(), key=lambda p: (not p.name.startswith("v"), p.name)):
        tag = directory.name
        exists = subprocess.run(["gh", "release", "view", tag, "--repo", PACKAGES],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not exists:
            invoke("gh", "release", "create", tag, "--repo", PACKAGES,
                   "--target", "main", "--draft", "--title", tag,
                   "--notes", "Signed SSH Client packages maintained by Vapourware-Studios.")
        # Reruns repair partial uploads even when a release already exists.
        for file in sorted(directory.iterdir(), key=lambda p: (upload_order(p), p.name)):
            invoke("gh", "release", "upload", tag, str(file), "--repo", PACKAGES, "--clobber")
        invoke("gh", "release", "edit", tag, "--repo", PACKAGES, "--draft=false", "--latest=false")
        release = json.loads(invoke("gh", "api", f"repos/{PACKAGES}/releases/tags/{tag}", capture=True))
        uploaded = {asset["name"]: asset for asset in release["assets"]}
        for file in directory.iterdir():
            asset = uploaded.get(file.name, {})
            if asset.get("state") != "uploaded" or asset.get("size") != file.stat().st_size:
                raise ValueError(f"Upload verification failed: {tag}/{file.name}")
            digest = asset.get("digest")
            if digest and digest != f"sha256:{checksum(file)}":
                raise ValueError(f"Uploaded checksum differs: {tag}/{file.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    version_tuple(args.version)
    existing = json.loads((ROOT / "release.json").read_text())
    if not should_publish(existing.get("version"), args.version):
        print("A complete repository update for this version or a newer one is already committed.")
        return
    work = args.work_dir.resolve()
    metadata = work / "metadata"
    run(sys.executable, "scripts/build_repository.py", "--version", args.version,
        "--work-dir", str(work), "--output", str(metadata))
    record = json.loads((work / "release.json").read_text())
    # Check generated public data before any upload or commit.
    shutil.copytree(metadata / "rpm", ROOT / "metadata/rpm", dirs_exist_ok=True)
    shutil.copy2(metadata / "signing-key.asc", ROOT / "signing-key.asc")
    shutil.copy2(work / "release.json", ROOT / "release.json")
    readme = ROOT / "README.md"
    readme.write_text(readme.read_text().replace(
        "The first signed package publication is pending. The setup URLs below become available after that publication succeeds.",
        "Signed packages are available. Complete the one-time setup below, then use your normal package manager."))
    run("git", "add", "release.json", "README.md", "signing-key.asc", "metadata/rpm")
    run(sys.executable, "scripts/check_staged.py")
    publish_assets(work / "release-assets")
    # This commit is the completion marker. A failed upload leaves main on the
    # previous version, so a rerun rebuilds and finishes instead of skipping.
    run("git", "config", "user.name", "thecoolraven[bot]")
    run("git", "config", "user.email", "306463211+thecoolraven[bot]@users.noreply.github.com")
    run("git", "commit", "-m", f"sshclient {record['version']}")
    identity = run("git", "log", "-1", "--format=%an <%ae>", capture=True).strip()
    if identity != "thecoolraven[bot] <306463211+thecoolraven[bot]@users.noreply.github.com>":
        raise ValueError("Unexpected publication author")
    run("git", "push", "origin", "HEAD:main")


if __name__ == "__main__":
    main()
