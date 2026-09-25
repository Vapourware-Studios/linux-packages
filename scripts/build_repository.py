#!/usr/bin/env python3
"""Build signed native repositories from a complete SSH Client release."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "Vapourware-Studios/sshclient"
PACKAGES = "Vapourware-Studios/linux-packages"
REPO_NAME = "vapourware-studios"
PAYLOADS = (".deb", ".rpm", ".pkg.tar.zst")
ARCHES = {
    "x64": {"deb": "amd64", "rpm": "x86_64", "pacman": "x64", "native": "x86_64"},
    "arm64": {"deb": "arm64", "rpm": "aarch64", "pacman": "aarch64", "native": "aarch64"},
}


def run(*args, cwd=None, capture=False, input=None):
    result = subprocess.run(args, cwd=cwd, input=input, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def version_tuple(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable X.Y.Z release version")
    return tuple(map(int, version.split(".")))


def checksum(file):
    with Path(file).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def expected_assets(version):
    version_tuple(version)
    return {
        arch: {
            kind: f"sshclient-{version}-linux-{names[kind]}."
                  f"{'pkg.tar.zst' if kind == 'pacman' else kind}"
            for kind in ("deb", "rpm", "pacman")
        }
        for arch, names in ARCHES.items()
    }


def parse_checksums(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([a-f0-9]{64}) [ *]([^/\\]+)", line)
        if not match:
            raise ValueError("Malformed SHA-256 release manifest")
        digest, name = match.groups()
        if name in result or name in (".", ".."):
            raise ValueError("Duplicate or invalid release filename")
        result[name] = digest
    return result


def fetch(url, destination):
    if not url.startswith(f"https://github.com/{SOURCE}/releases/download/"):
        raise ValueError("Unexpected package source")
    request = urllib.request.Request(url, headers={"User-Agent": "sshclient-packages"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Package download redirected away from HTTPS")
        with Path(destination).open("wb") as output:
            shutil.copyfileobj(response, output)


def download_release(version, work):
    base = f"https://github.com/{SOURCE}/releases/download/v{version}"
    packages = {}
    digests = {}
    for arch, assets in expected_assets(version).items():
        manifest = work / f"SHA256SUMS-linux-{arch}"
        fetch(f"{base}/{manifest.name}", manifest)
        checksums = parse_checksums(manifest.read_text())
        packages[arch] = {}
        for kind, name in assets.items():
            if name not in checksums:
                raise ValueError(f"Missing release checksum: {name}")
            file = work / name
            fetch(f"{base}/{name}", file)
            if checksum(file) != checksums[name]:
                raise ValueError(f"Release checksum mismatch: {name}")
            packages[arch][kind] = file
            digests[name] = checksums[name]
    return packages, digests


def sign(file, fingerprint, *, armor=False, clear=False):
    suffix = ".asc" if armor else ".sig"
    target = file.parent / "InRelease" if clear else Path(str(file) + suffix)
    args = ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--local-user", fingerprint,
            "--digest-algo", "SHA256", "--output", str(target)]
    if armor:
        args.append("--armor")
    args.extend(["--clearsign" if clear else "--detach-sign", str(file)])
    run(*args)
    run("gpg", "--batch", "--verify", str(target), *([] if clear else [str(file)]))
    return target


def read_pkginfo(package):
    text = run("bsdtar", "-xOf", str(package), ".PKGINFO", capture=True).decode()
    result = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            result.setdefault(key, []).append(value)
    return result


def validate_packages(packages, version):
    for arch, formats in packages.items():
        names = ARCHES[arch]
        fields = run("dpkg-deb", "-f", str(formats["deb"]), "Package", "Version", "Architecture", capture=True).decode()
        deb = dict(line.split(": ", 1) for line in fields.splitlines())
        if deb != {"Package": "sshclient", "Version": version, "Architecture": names["deb"]}:
            raise ValueError(f"Unexpected Debian package metadata for {arch}")
        rpm = run("rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{ARCH}", str(formats["rpm"]), capture=True).decode()
        if rpm != f"sshclient {version} {names['native']}":
            raise ValueError(f"Unexpected RPM package metadata for {arch}")
        info = read_pkginfo(formats["pacman"])
        if info.get("pkgname") != ["sshclient"] or info.get("arch") != [names["native"]] or info.get("pkgver") != [f"{version}-1"]:
            raise ValueError(f"Unexpected Arch package metadata for {arch}")


def build_apt(packages, output, fingerprint):
    apt = output / "apt"
    pool = apt / "pool" / "main"
    pool.mkdir(parents=True)
    for formats in packages.values():
        shutil.copy2(formats["deb"], pool)
    for names in ARCHES.values():
        directory = apt / "dists" / "stable" / "main" / f"binary-{names['deb']}"
        directory.mkdir(parents=True)
        index = run("dpkg-scanpackages", "--arch", names["deb"], "pool", cwd=apt, capture=True)
        (directory / "Packages").write_bytes(index)
        (directory / "Packages.gz").write_bytes(gzip.compress(index, mtime=0))
    release_dir = apt / "dists" / "stable"
    options = {"Origin": "Vapourware-Studios", "Label": "Vapourware-Studios",
               "Suite": "stable", "Codename": "stable", "Architectures": "amd64 arm64", "Components": "main"}
    args = [part for key, value in options.items() for part in ("-o", f"APT::FTPArchive::Release::{key}={value}")]
    release = release_dir / "Release"
    release.write_bytes(run("apt-ftparchive", *args, "release", str(release_dir), capture=True))
    detached = sign(release, fingerprint, armor=True)
    detached.rename(release_dir / "Release.gpg")
    sign(release, fingerprint, clear=True)


def build_pacman(packages, output, fingerprint):
    for arch, formats in packages.items():
        directory = output / "arch" / ARCHES[arch]["native"]
        directory.mkdir(parents=True)
        package = directory / formats["pacman"].name
        shutil.copy2(formats["pacman"], package)
        sign(package, fingerprint)
        run("repo-add", "--include-sigs", "--sign", "--key", fingerprint,
            str(directory / f"{REPO_NAME}.db.tar.gz"), str(package))
        # Pages artifacts cannot contain symlinks.
        for link in directory.iterdir():
            if link.is_symlink():
                data = link.read_bytes()
                link.unlink()
                link.write_bytes(data)
        run("gpg", "--batch", "--verify", str(directory / f"{REPO_NAME}.db.sig"), str(directory / f"{REPO_NAME}.db"))


def build_rpm(packages, output, fingerprint):
    for arch, formats in packages.items():
        directory = output / "rpm" / ARCHES[arch]["native"]
        directory.mkdir(parents=True)
        package = directory / formats["rpm"].name
        shutil.copy2(formats["rpm"], package)
        run("rpmsign", "--define", f"_gpg_name {fingerprint}", "--define", "_gpg_digest_algo sha256",
            "--define", "__gpg /usr/bin/gpg", "--addsign", str(package))
        run("createrepo_c", "--checksum", "sha256", "--no-database", str(directory))
        sign(directory / "repodata" / "repomd.xml", fingerprint, armor=True)


def strip_payloads(output, version, work):
    """Replace the package payloads with a redirect map.

    Only signed metadata is served from packages.vapourware-studios.net; the
    worker turns each payload path into a redirect to the release that already
    hosts those bytes. The indexes keep their normal relative paths, so every
    package manager still resolves them the usual way.

    Debian and Arch packages go out byte-for-byte as the application release
    built them, so they redirect straight to it. `rpmsign --addsign` rewrites
    the RPM to embed its signature, so the checksum createrepo_c recorded only
    matches the signed copy — those are republished on this repository's own
    release and redirect there instead.
    """
    source = f"https://github.com/{SOURCE}/releases/download/v{version}"
    ours = f"https://github.com/{PACKAGES}/releases/download/v{version}"
    assets = work / "release-assets"
    assets.mkdir(parents=True, exist_ok=True)
    redirects = {}
    for file in sorted(output.rglob("*")):
        if not file.is_file() or not file.name.endswith(PAYLOADS):
            continue
        path = file.relative_to(output).as_posix()
        if file.name.endswith(".rpm"):
            redirects[path] = f"{ours}/{file.name}"
            shutil.move(str(file), assets / file.name)
        else:
            redirects[path] = f"{source}/{file.name}"
            file.unlink()
    if not redirects:
        raise ValueError("No package payloads were found to redirect")
    (output / "manifest.json").write_text(
        json.dumps({"schema": 1, "version": version, "redirects": redirects}, indent=2, sort_keys=True) + "\n")
    return redirects, {file.name: checksum(file) for file in sorted(assets.iterdir())}


def make_archive(output, target):
    def neutral_metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info
    with tarfile.open(target, "w:gz", compresslevel=1) as archive:
        for file in sorted(output.rglob("*")):
            if file.is_file():
                archive.add(file, arcname=file.relative_to(output), filter=neutral_metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version_tuple(args.version)
    record_path = ROOT / "release.json"
    existing = json.loads(record_path.read_text()) if record_path.exists() else {}
    if existing.get("version") and version_tuple(existing["version"]) >= version_tuple(args.version):
        print("The package repository already has this release or a newer one.")
        return
    fingerprint = (ROOT / "signing-key-fingerprint.txt").read_text().strip()
    if not re.fullmatch(r"[A-F0-9]{40}", fingerprint):
        raise ValueError("Invalid pinned package-signing fingerprint")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError("Repository output must be a new directory")
    args.output.mkdir(parents=True)
    packages, source_digests = download_release(args.version, args.work_dir)
    validate_packages(packages, args.version)
    public_key = run("gpg", "--batch", "--armor", "--export", fingerprint, capture=True)
    if not public_key:
        raise ValueError("The pinned package-signing key is not available")
    (args.output / "signing-key.asc").write_bytes(public_key)
    (args.output / "signing-key-fingerprint.txt").write_text(fingerprint + "\n")
    (args.output / ".nojekyll").touch()
    build_apt(packages, args.output, fingerprint)
    build_pacman(packages, args.output, fingerprint)
    build_rpm(packages, args.output, fingerprint)
    shutil.copytree(ROOT / "config", args.output / "config")
    redirects, signed_rpms = strip_payloads(args.output, args.version, args.work_dir)
    # Metadata only: indexes, signatures and the public key. Anything close to
    # this size means a payload escaped the redirect map.
    if sum(f.stat().st_size for f in args.output.rglob("*") if f.is_file()) > 20_000_000:
        raise ValueError("Signed metadata is far larger than expected")
    archive = args.work_dir / f"linux-packages-{args.version}.tar.gz"
    make_archive(args.output, archive)
    record = {"schema": 1, "version": args.version, "source": f"https://github.com/{SOURCE}/releases/tag/v{args.version}",
              "signing_fingerprint": fingerprint, "archive": archive.name, "sha256": checksum(archive),
              "source_checksums": source_digests, "redirects": len(redirects), "signed_rpms": signed_rpms}
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    readme = ROOT / "README.md"
    readme.write_text(readme.read_text().replace(
        "The first signed package publication is pending. The setup URLs below become available after that publication succeeds.",
        "Signed packages are published below. Complete the one-time setup for your distribution, then use its normal package manager."))
    print(f"Built signed package repository for {args.version}")


if __name__ == "__main__":
    main()
