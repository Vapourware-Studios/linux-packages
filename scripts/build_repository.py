#!/usr/bin/env python3
"""Build signed repositories: release assets for apt/pacman, raw Git for RPM metadata."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "Vapourware-Studios/sshclient"
PACKAGES = "Vapourware-Studios/linux-packages"
DOWNLOAD = f"https://github.com/{PACKAGES}/releases/download"
REPO_NAME = "vapourware-studios"
# Fixed tags, clobbered on every release, so the URLs in an installed config
# never change. Versioned releases carry history separately.
APT_TAG = "repo-apt"
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


def local_release(directory, version):
    """Use packages already on disk, for testing the pipeline without a release."""
    packages = {}
    digests = {}
    for arch, assets in expected_assets(version).items():
        packages[arch] = {}
        for kind, name in assets.items():
            file = Path(directory) / name
            if not file.is_file():
                raise ValueError(f"Missing local package: {name}")
            packages[arch][kind] = file
            digests[name] = checksum(file)
    return packages, digests


def sign(file, fingerprint, *, armor=False, clear=False, output=None):
    suffix = ".asc" if armor else ".sig"
    target = output or Path(str(file) + suffix)
    args = ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--local-user", fingerprint,
            "--digest-algo", "SHA256", "--output", str(target)]
    if armor:
        args.append("--armor")
    args.extend(["--clearsign" if clear else "--detach-sign", str(file)])
    run(*args)
    run("gpg", "--batch", "--verify", str(target), *([] if clear else [str(file)]))
    return target


def validate_public_key(key, fingerprint):
    details = run("gpg", "--batch", "--with-colons", "--import-options", "show-only", "--import",
                  input=key, capture=True).decode()
    fields = [line.split(":") for line in details.splitlines()]
    if any(row[0] in ("sec", "ssb") for row in fields):
        raise ValueError("A private key must never be published")
    fingerprints = [row[9] for row in fields if row[0] == "fpr"]
    identities = [row[9] for row in fields if row[0] == "uid"]
    if not fingerprints or fingerprints[0] != fingerprint or not identities:
        raise ValueError("Public signing key does not match the pinned fingerprint")
    for identity in identities:
        if not re.fullmatch(r"Vapourware-Studios(?: Linux Packages)?(?: <(?:noreply@vapourware-studios\.net|306463211\+thecoolraven\[bot\]@users\.noreply\.github\.com)>)?", identity):
            raise ValueError("Public signing key identity must use the organization and its noreply address")


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


def build_apt(packages, assets, fingerprint):
    """Flat apt repository: one directory, both CPUs, no dists/ or pool/.

    `Filename:` is rewritten to a bare name because a flat repository resolves
    it against the base URI, and a release download URL has no directory to
    hold the `./` that dpkg-scanpackages emits.
    """
    flat = assets / APT_TAG
    staging = flat.parent / f"{APT_TAG}-staging"
    for directory in (flat, staging):
        directory.mkdir(parents=True)
    for formats in packages.values():
        shutil.copy2(formats["deb"], staging)
        shutil.copy2(formats["deb"], flat)
    index = run("dpkg-scanpackages", "--multiversion", ".", cwd=staging, capture=True)
    index = re.sub(rb"^Filename: \./", b"Filename: ", index, flags=re.MULTILINE)
    (flat / "Packages").write_bytes(index)
    (flat / "Packages.gz").write_bytes(gzip.compress(index, mtime=0))

    # Generate Release over the indexes alone. Running apt-ftparchive in a
    # directory holding the .deb files would list those as index files too.
    meta = flat.parent / f"{APT_TAG}-meta"
    meta.mkdir()
    for name in ("Packages", "Packages.gz"):
        shutil.copy2(flat / name, meta)
    options = {"Origin": "Vapourware-Studios", "Label": "Vapourware-Studios",
               "Suite": "stable", "Codename": "stable", "Architectures": "amd64 arm64"}
    args = [part for key, value in options.items() for part in ("-o", f"APT::FTPArchive::Release::{key}={value}")]
    release = meta / "Release"
    release.write_bytes(run("apt-ftparchive", *args, "release", ".", cwd=meta, capture=True))
    shutil.copy2(release, flat / "Release")
    sign(flat / "Release", fingerprint, armor=True, output=flat / "Release.gpg")
    sign(flat / "Release", fingerprint, clear=True, output=flat / "InRelease")
    shutil.rmtree(staging)
    shutil.rmtree(meta)


def build_pacman(packages, assets, fingerprint):
    """Flat pacman repository per CPU; Server uses $arch to pick the tag."""
    for arch, formats in packages.items():
        native = ARCHES[arch]["native"]
        directory = assets / f"repo-arch-{native}"
        directory.mkdir(parents=True)
        package = directory / formats["pacman"].name
        shutil.copy2(formats["pacman"], package)
        sign(package, fingerprint)
        run("repo-add", "--include-sigs", "--sign", "--key", fingerprint,
            str(directory / f"{REPO_NAME}.db.tar.gz"), str(package))
        # Release assets are plain files; resolve repo-add's symlinks.
        for link in directory.iterdir():
            if link.is_symlink():
                data = link.resolve().read_bytes()
                link.unlink()
                link.write_bytes(data)
        run("gpg", "--batch", "--verify", str(directory / f"{REPO_NAME}.db.sig"), str(directory / f"{REPO_NAME}.db"))


def build_rpm(packages, site, assets, fingerprint, version):
    """Keep RPM metadata in Git and signed package bytes in a versioned release."""
    for arch, formats in packages.items():
        native = ARCHES[arch]["native"]
        directory = assets / f"v{version}"
        directory.mkdir(parents=True, exist_ok=True)
        package = directory / formats["rpm"].name
        shutil.copy2(formats["rpm"], package)
        run("rpmsign", "--define", f"_gpg_name {fingerprint}", "--define", "_gpg_digest_algo sha256",
            "--define", "__gpg /usr/bin/gpg", "--addsign", str(package))
        staging = site / f"staging-{native}"
        staging.mkdir()
        shutil.copy2(package, staging)
        run("createrepo_c", "--checksum", "sha256", "--no-database",
            "--baseurl", f"{DOWNLOAD}/v{version}/", str(staging))
        served = site / "rpm" / native
        served.mkdir(parents=True)
        shutil.move(str(staging / "repodata"), str(served / "repodata"))
        shutil.rmtree(staging)
        sign(served / "repodata" / "repomd.xml", fingerprint, armor=True)


def should_publish(current, requested):
    """A completed record is committed only after every upload succeeds."""
    return not current or version_tuple(current) < version_tuple(requested)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-dir", type=Path,
                        help="build from packages already on disk instead of a published release")
    args = parser.parse_args()
    version_tuple(args.version)
    fingerprint = (ROOT / "signing-key-fingerprint.txt").read_text().strip()
    if not re.fullmatch(r"[A-F0-9]{40}", fingerprint):
        raise ValueError("Invalid pinned package-signing fingerprint")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError("Repository output must be a new directory")
    args.output.mkdir(parents=True)
    assets = args.work_dir / "release-assets"
    assets.mkdir(parents=True, exist_ok=True)

    packages, source_digests = (local_release(args.local_dir, args.version) if args.local_dir
                                else download_release(args.version, args.work_dir))
    validate_packages(packages, args.version)
    public_key = run("gpg", "--batch", "--armor", "--export", fingerprint, capture=True)
    if not public_key:
        raise ValueError("The pinned package-signing key is not available")
    validate_public_key(public_key, fingerprint)
    (args.output / "signing-key.asc").write_bytes(public_key)
    (args.output / "signing-key-fingerprint.txt").write_text(fingerprint + "\n")
    build_apt(packages, assets, fingerprint)
    build_pacman(packages, assets, fingerprint)
    build_rpm(packages, args.output, assets, fingerprint, args.version)
    versioned = assets / f"v{args.version}"
    for directory in list(assets.iterdir()):
        if directory == versioned:
            continue
        for file in directory.iterdir():
            if file.name.endswith((".deb", ".pkg.tar.zst", ".pkg.tar.zst.sig")):
                shutil.copy2(file, versioned)
    shutil.copy2(args.output / "signing-key.asc", versioned)

    # Only repodata, the public key and the setup files are hosted. Anything
    # bigger means a package leaked into the metadata directory.
    hosted = sum(f.stat().st_size for f in args.output.rglob("*") if f.is_file())
    if hosted > 20_000_000:
        raise ValueError(f"Hosted metadata is far larger than expected: {hosted} bytes")
    if any(f.name.endswith((".deb", ".rpm", ".pkg.tar.zst")) for f in args.output.rglob("*")):
        raise ValueError("Package payloads must never be hosted")

    published = {tag.name: {f.name: checksum(f) for f in sorted(tag.iterdir()) if f.is_file()}
                 for tag in sorted(assets.iterdir()) if tag.is_dir()}
    record = {"schema": 2, "version": args.version,
              "source": f"https://github.com/{SOURCE}/releases/tag/v{args.version}",
              "signing_fingerprint": fingerprint, "source_checksums": source_digests,
              "metadata_bytes": hosted, "published": published}
    (args.work_dir / "release.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Built signed package repository for {args.version}: {hosted} bytes of metadata")


if __name__ == "__main__":
    main()
