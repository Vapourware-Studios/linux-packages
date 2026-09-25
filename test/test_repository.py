import gzip
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_repository as build
from unpack_site import unpack


class ReleaseValidation(unittest.TestCase):
    def test_architecture_names_match_release_packages(self):
        files = build.expected_assets("1.2.3")
        self.assertEqual(files["x64"]["deb"], "sshclient-1.2.3-linux-amd64.deb")
        self.assertEqual(files["arm64"]["rpm"], "sshclient-1.2.3-linux-aarch64.rpm")
        self.assertEqual(files["x64"]["pacman"], "sshclient-1.2.3-linux-x64.pkg.tar.zst")
        self.assertEqual(files["arm64"]["pacman"], "sshclient-1.2.3-linux-aarch64.pkg.tar.zst")

    def test_versions_reject_paths_and_shell_fragments(self):
        for version in ("../1.2.3", "v1.2.3", "1.2.3;false", "1.2", "1.2.3-beta"):
            with self.assertRaises(ValueError):
                build.expected_assets(version)
        self.assertLess(build.version_tuple("1.2.9"), build.version_tuple("1.2.10"))

    def test_checksum_manifest_rejects_ambiguity(self):
        digest = "a" * 64
        self.assertEqual(build.parse_checksums(f"{digest}  demo.deb\n"), {"demo.deb": digest})
        for text in (f"{digest}  ../demo.deb", f"{digest}  demo.deb\n{digest}  demo.deb", "bad  demo.deb"):
            with self.assertRaises(ValueError):
                build.parse_checksums(text)

    def test_downloads_are_restricted_to_the_application_release(self):
        with self.assertRaises(ValueError):
            build.fetch("https://example.com/demo.deb", Path("unused"))


class ArchiveValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    MANIFEST = json.dumps({"schema": 1, "version": "1.2.3", "redirects": {
        "apt/pool/main/sshclient-1.2.3-linux-amd64.deb":
            "https://github.com/Vapourware-Studios/sshclient/releases/download/v1.2.3/sshclient-1.2.3-linux-amd64.deb",
        "rpm/x86_64/sshclient-1.2.3-linux-x86_64.rpm":
            "https://github.com/Vapourware-Studios/linux-packages/releases/download/v1.2.3/sshclient-1.2.3-linux-x86_64.rpm",
    }}).encode()

    def archive(self, extra=None, manifest=None):
        file = self.root / "linux-packages-1.2.3.tar.gz"
        with tarfile.open(file, "w:gz") as archive:
            for name, content in {"signing-key.asc": b"demo-public-key",
                                  "signing-key-fingerprint.txt": b"A" * 40,
                                  "apt/dists/stable/InRelease": b"demo-index",
                                  "manifest.json": manifest or self.MANIFEST}.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            if extra:
                archive.addfile(extra)
        record = {"version": "1.2.3", "archive": file.name, "sha256": build.checksum(file), "signing_fingerprint": "A" * 40}
        return file, record

    def test_archive_extraction_and_fingerprint(self):
        archive, record = self.archive()
        unpack(archive, self.root / "site", record)
        self.assertTrue((self.root / "site/apt/dists/stable/InRelease").is_file())

    def test_corrupt_archive_is_rejected_before_extraction(self):
        archive, record = self.archive()
        archive.write_bytes(archive.read_bytes() + b"corrupted")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            unpack(archive, self.root / "site", record)
        self.assertFalse((self.root / "site").exists())

    def test_links_and_path_escapes_are_rejected(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "/demo"
                archive, record = self.archive(info)
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    unpack(archive, self.root / "site", record)

    def test_package_payloads_may_not_ship_in_the_bundle(self):
        info = tarfile.TarInfo("apt/pool/main/sshclient-1.2.3-linux-amd64.deb")
        info.size = 0
        archive, record = self.archive(info)
        with self.assertRaisesRegex(ValueError, "must not contain package payloads"):
            unpack(archive, self.root / "site", record)

    def test_redirects_may_not_leave_the_projects_releases(self):
        for index, target in enumerate((
                "https://example.com/evil.deb",
                "https://github.com/someone-else/sshclient/releases/download/v1.2.3/x.deb",
                "https://packages.vapourware-studios.net.evil.test/x.deb")):
            with self.subTest(target=target):
                manifest = json.dumps({"schema": 1, "version": "1.2.3",
                                       "redirects": {"apt/pool/main/x.deb": target}}).encode()
                archive, record = self.archive(manifest=manifest)
                site = self.root / f"site-{index}"
                with self.assertRaisesRegex(ValueError, "Redirect leaves"):
                    unpack(archive, site, record)
                self.assertFalse(site.exists(), "nothing may be written before the map is checked")


class RedirectMap(unittest.TestCase):
    def build_site(self, root):
        site = root / "site"
        layout = {
            "apt/pool/main/sshclient-1.2.3-linux-amd64.deb": b"deb",
            "apt/pool/main/sshclient-1.2.3-linux-arm64.deb": b"deb",
            "arch/x86_64/sshclient-1.2.3-linux-x64.pkg.tar.zst": b"pkg",
            "arch/x86_64/sshclient-1.2.3-linux-x64.pkg.tar.zst.sig": b"sig",
            "arch/x86_64/vapourware-studios.db": b"db",
            "arch/x86_64/vapourware-studios.db.sig": b"dbsig",
            "rpm/x86_64/sshclient-1.2.3-linux-x86_64.rpm": b"rpm",
            "rpm/x86_64/repodata/repomd.xml": b"xml",
            "apt/dists/stable/InRelease": b"index",
        }
        for name, content in layout.items():
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return site

    def test_payloads_become_release_redirects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = self.build_site(root)
            redirects, signed = build.strip_payloads(site, "1.2.3", root / "work")

            self.assertFalse([f for f in site.rglob("*") if f.name.endswith(build.PAYLOADS)],
                             "no package payload may remain in the served bundle")
            # Signatures and indexes are metadata: the worker serves them, and
            # pacman does fetch <pkg>.sig over the network even with
            # repo-add --include-sigs, so it must survive the strip.
            for kept in ("arch/x86_64/sshclient-1.2.3-linux-x64.pkg.tar.zst.sig",
                         "arch/x86_64/vapourware-studios.db.sig",
                         "rpm/x86_64/repodata/repomd.xml"):
                self.assertTrue((site / kept).is_file(), kept)

            self.assertEqual(redirects["apt/pool/main/sshclient-1.2.3-linux-amd64.deb"],
                             "https://github.com/Vapourware-Studios/sshclient/releases/download/"
                             "v1.2.3/sshclient-1.2.3-linux-amd64.deb")
            self.assertEqual(redirects["rpm/x86_64/sshclient-1.2.3-linux-x86_64.rpm"],
                             "https://github.com/Vapourware-Studios/linux-packages/releases/download/"
                             "v1.2.3/sshclient-1.2.3-linux-x86_64.rpm")
            self.assertEqual(sorted(signed), ["sshclient-1.2.3-linux-x86_64.rpm"])
            self.assertTrue((root / "work/release-assets/sshclient-1.2.3-linux-x86_64.rpm").is_file())
            self.assertEqual(json.loads((site / "manifest.json").read_text())["version"], "1.2.3")

    def test_a_repository_with_no_payloads_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            (site / "apt/dists/stable").mkdir(parents=True)
            (site / "apt/dists/stable/InRelease").write_bytes(b"index")
            with self.assertRaisesRegex(ValueError, "No package payloads"):
                build.strip_payloads(site, "1.2.3", root / "work")


NATIVE_TOOLS = ("dpkg-deb", "dpkg-scanpackages", "apt-ftparchive", "apt-get", "apt-cache",
                "rpm", "rpmbuild", "rpmsign", "rpmkeys", "createrepo_c", "repo-add", "pacman", "gpg", "bsdtar")


@unittest.skipUnless(all(shutil.which(tool) for tool in NATIVE_TOOLS), "Native packaging toolchain is required")
class NativeRepositories(unittest.TestCase):
    def test_signed_indexes_are_readable_by_package_managers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = os.environ.get("GNUPGHOME")
            os.environ["GNUPGHOME"] = str(root / "gnupg")
            Path(os.environ["GNUPGHOME"]).mkdir(mode=0o700)
            try:
                build.run("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "", "--quick-generate-key",
                          "Vapourware-Studios", "rsa2048", "sign", "1d")
                keys = build.run("gpg", "--batch", "--with-colons", "--list-secret-keys", capture=True).decode()
                fingerprint = next(line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:"))
                packages = self.create_packages(root)
                build.validate_packages(packages, "1.2.3")
                site = root / "site"
                site.mkdir()
                public_key = site / "signing-key.asc"
                public_key.write_bytes(build.run("gpg", "--batch", "--armor", "--export", fingerprint, capture=True))
                build.build_apt(packages, site, fingerprint)
                build.build_pacman(packages, site, fingerprint)
                build.build_rpm(packages, site, fingerprint)
                self.check_apt(root, site, public_key)
                self.check_pacman(root, site)
                self.check_rpm(root, site, public_key)
                self.assertFalse(any(file.is_symlink() for file in site.rglob("*")))
                self.check_redirects(root, site, packages, public_key)
            finally:
                subprocess.run(["gpgconf", "--kill", "gpg-agent"], check=False)
                if previous is None:
                    os.environ.pop("GNUPGHOME", None)
                else:
                    os.environ["GNUPGHOME"] = previous

    def create_packages(self, root):
        packages = {}
        for arch, assets in build.expected_assets("1.2.3").items():
            names = build.ARCHES[arch]
            work = root / arch
            work.mkdir()
            deb = work / "deb"
            (deb / "DEBIAN").mkdir(parents=True)
            (deb / "DEBIAN/control").write_text(
                f"Package: sshclient\nVersion: 1.2.3\nArchitecture: {names['deb']}\n"
                "Maintainer: Vapourware-Studios <noreply@vapourware-studios.net>\nDescription: demo package\n")
            payload = deb / "usr/share/sshclient/demo.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("demo\n")
            build.run("dpkg-deb", "--build", "--root-owner-group", str(deb), str(work / assets["deb"]))
            pkg = work / "pkg"
            pkg.mkdir()
            (pkg / ".PKGINFO").write_text(f"pkgname = sshclient\npkgver = 1.2.3-1\npkgdesc = demo\narch = {names['native']}\nsize = 5\n")
            (pkg / "demo.txt").write_text("demo\n")
            build.run("bsdtar", "--zstd", "-cf", str(work / assets["pacman"]), "-C", str(pkg), ".PKGINFO", "demo.txt")
            top = work / "rpmbuild"
            top.mkdir()
            spec = top / "demo.spec"
            spec.write_text(f"""Name: sshclient
Version: 1.2.3
Release: 1
Summary: demo package
License: GPL-3.0-only
BuildArch: {names['native']}
%description
demo package
%install
mkdir -p %{{buildroot}}/usr/share/sshclient
echo demo > %{{buildroot}}/usr/share/sshclient/demo.txt
%files
/usr/share/sshclient/demo.txt
""")
            build.run("rpmbuild", "--define", f"_topdir {top}", "--target", names["native"], "-bb", str(spec))
            shutil.copy2(next((top / "RPMS").rglob("*.rpm")), work / assets["rpm"])
            packages[arch] = {kind: work / name for kind, name in assets.items()}
        return packages

    def check_apt(self, root, site, key):
        lists = root / "apt-lists"
        (lists / "partial").mkdir(parents=True)
        source = root / "demo.list"
        source.write_text(f"deb [signed-by={key}] file:{site}/apt stable main\n")
        settings = {"Dir::Etc::sourcelist": str(source), "Dir::Etc::sourceparts": "-",
                    "Dir::State::lists": str(lists), "Dir::State::status": "/dev/null",
                    "Dir::Cache": str(root / "apt-cache"), "Debug::NoLocking": "1", "APT::Architecture": "amd64"}
        options = [part for key, value in settings.items() for part in ("-o", f"{key}={value}")]
        build.run("apt-get", *options, "update")
        policy = build.run("apt-cache", *options, "policy", "sshclient", capture=True).decode()
        self.assertIn("Candidate: 1.2.3", policy)

    def check_pacman(self, root, site):
        for names in build.ARCHES.values():
            db = root / f"pacman-{names['native']}"
            (db / "sync").mkdir(parents=True)
            shutil.copy2(site / "arch" / names["native"] / "vapourware-studios.db", db / "sync/vapourware-studios.db")
            config = db / "pacman.conf"
            config.write_text(f"[options]\nArchitecture = {names['native']}\n[vapourware-studios]\nServer = file://{site}/arch/{names['native']}\n")
            result = build.run("pacman", "--config", str(config), "--dbpath", str(db), "-Si", "sshclient", capture=True).decode()
            self.assertIn("1.2.3-1", result)

    def check_redirects(self, root, site, packages, key):
        """The shipped bundle is metadata only, and apt still resolves a package.

        Serving is split: the worker answers for signed indexes and 302s every
        payload to the release hosting it. This drives a real apt through that
        split over HTTP rather than trusting that it follows redirects.
        """
        indexed = build.checksum(next((site / "rpm").rglob("*.rpm")))
        original = build.checksum(packages["x64"]["rpm"])
        # rpmsign --addsign rewrites the package, so the checksum createrepo_c
        # recorded belongs to the signed copy, not to the application release.
        # That is why RPMs are republished instead of redirected upstream.
        self.assertNotEqual(indexed, original, "expected rpmsign to rewrite the RPM")

        redirects, signed = build.strip_payloads(site, "1.2.3", root / "work")
        self.assertFalse([f for f in site.rglob("*") if f.name.endswith(build.PAYLOADS)])
        self.assertTrue(signed, "signed RPMs must be republished")
        for path, target in redirects.items():
            owner = "linux-packages" if path.endswith(".rpm") else "sshclient"
            self.assertIn(f"Vapourware-Studios/{owner}/releases/download/v1.2.3/", target)
        self.assertLess(sum(f.stat().st_size for f in site.rglob("*") if f.is_file()), 20_000_000)

        # Payloads now live only in the release-asset staging directory; point
        # the redirect target at it and let apt walk the whole chain.
        blobs = root / "work" / "release-assets"
        for arch, formats in packages.items():
            for kind, file in formats.items():
                if not (blobs / file.name).exists():
                    shutil.copy2(file, blobs / file.name)

        blob_srv = http.server.HTTPServer(("127.0.0.1", 0), self.handler(blobs))
        blob_url = f"http://127.0.0.1:{blob_srv.server_port}"
        meta_srv = http.server.HTTPServer(("127.0.0.1", 0), self.handler(site, blob_url))
        meta_url = f"http://127.0.0.1:{meta_srv.server_port}"
        for srv in (blob_srv, meta_srv):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.shutdown)

        lists = root / "http-lists"
        (lists / "partial").mkdir(parents=True)
        (root / "http-cache" / "archives" / "partial").mkdir(parents=True)
        source = root / "http.list"
        source.write_text(f"deb [signed-by={key}] {meta_url}/apt stable main\n")
        settings = {"Dir::Etc::sourcelist": str(source), "Dir::Etc::sourceparts": "-",
                    "Dir::State::lists": str(lists), "Dir::State::status": "/dev/null",
                    "Dir::Cache": str(root / "http-cache"), "Debug::NoLocking": "1",
                    "APT::Architecture": "amd64"}
        options = [part for k, v in settings.items() for part in ("-o", f"{k}={v}")]
        build.run("apt-get", *options, "update")
        build.run("apt-get", *options, "download", "sshclient", cwd=root)
        downloaded = list(root.glob("sshclient*.deb"))
        self.assertTrue(downloaded, "apt did not retrieve the package through the redirect")
        self.assertEqual(build.checksum(downloaded[0]), build.checksum(packages["x64"]["deb"]))

    def handler(self, directory, redirect_to=None):
        # Only payloads redirect. Index signatures such as Release.gpg and
        # <repo>.db.sig are metadata and must be served from here.
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(directory), **kw)

            def do_GET(self):
                name = self.path.lstrip("/")
                if redirect_to and name.endswith(build.PAYLOADS):
                    self.send_response(302)
                    self.send_header("Location", f"{redirect_to}/{Path(name).name}")
                    self.end_headers()
                    return
                super().do_GET()

            def log_message(self, *a):
                pass
        return Handler

    def check_rpm(self, root, site, key):
        db = root / "rpmdb"
        db.mkdir()
        build.run("rpm", "--dbpath", str(db), "--import", str(key))
        for package in (site / "rpm").rglob("*.rpm"):
            result = build.run("rpmkeys", "--dbpath", str(db), "--checksig", str(package), capture=True).decode()
            self.assertIn("signatures OK", result)


if __name__ == "__main__":
    unittest.main()
