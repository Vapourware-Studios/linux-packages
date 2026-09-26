import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_repository as build
import publish_repository as publish


class ReleaseValidation(unittest.TestCase):
    def test_first_publication_retries_and_rollback(self):
        self.assertTrue(build.should_publish(None, "1.2.3"))
        self.assertTrue(build.should_publish("1.2.2", "1.2.3"))
        self.assertFalse(build.should_publish("1.2.3", "1.2.3"))
        self.assertFalse(build.should_publish("1.2.4", "1.2.3"))

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


class PublicationValidation(unittest.TestCase):
    def test_upload_failure_is_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            directory = assets / "repo-apt"
            directory.mkdir()
            (directory / "demo.deb").write_bytes(b"demo")
            (directory / "InRelease").write_bytes(b"signed index")
            calls = []
            def invoke(*args, **kwargs):
                calls.append(args)
                if "upload" in args:
                    raise RuntimeError("interrupted upload")
            with mock.patch.object(publish.subprocess, "run", return_value=mock.Mock(returncode=0)):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    publish.publish_assets(assets, invoke)
            self.assertEqual(len(calls), 1)
            self.assertIn("demo.deb", calls[0][4])
            self.assertFalse(any("edit" in call or "commit" in call for call in calls))

    def test_indexes_are_uploaded_after_packages_and_inrelease_last(self):
        names = ["InRelease", "Packages.gz", "demo.deb", "Release.gpg"]
        self.assertEqual([p.name for p in sorted(map(Path, names), key=publish.upload_order)],
                         ["demo.deb", "Packages.gz", "Release.gpg", "InRelease"])


NATIVE_TOOLS = ("dpkg-deb", "dpkg-scanpackages", "apt-ftparchive", "apt-get", "apt-cache",
                "dnf", "rpm", "rpmbuild", "rpmsign", "rpmkeys", "createrepo_c", "repo-add", "pacman", "gpg", "bsdtar")


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
                site, assets = root / "site", root / "assets"
                site.mkdir()
                assets.mkdir()
                public_key = site / "signing-key.asc"
                public_key.write_bytes(build.run("gpg", "--batch", "--armor", "--export", fingerprint, capture=True))
                build.build_apt(packages, assets, fingerprint)
                build.build_pacman(packages, assets, fingerprint)
                server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.addCleanup(server.server_close)
                self.addCleanup(server.shutdown)
                base = f"http://127.0.0.1:{server.server_port}"
                with mock.patch.object(build, "DOWNLOAD", base + "/assets"):
                    build.build_rpm(packages, site, assets, fingerprint, "1.2.3")
                self.check_dnf(root, base, public_key)

                self.check_apt(root, assets, public_key)
                self.check_pacman(root, assets)
                self.check_rpm(root, assets, public_key)
                # Release assets are plain files, and only dnf's repodata plus
                # the public key may ever be hosted.
                self.assertFalse(any(f.is_symlink() for f in assets.rglob("*")))
                self.assertFalse([f for f in site.rglob("*") if f.name.endswith((".deb", ".rpm", ".pkg.tar.zst"))],
                                 "no package may be served from the hosted site")
                self.assertTrue((site / "rpm/x86_64/repodata/repomd.xml.asc").is_file())
                self.assertLess(sum(f.stat().st_size for f in site.rglob("*") if f.is_file()), 20_000_000)
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

    def check_apt(self, root, assets, key):
        lists = root / "apt-lists"
        (lists / "partial").mkdir(parents=True)
        source = root / "demo.list"
        # Flat repository: a trailing-slash URI and "./" instead of a suite and
        # components. This is what lets apt read a repo out of release assets.
        source.write_text(f"deb [signed-by={key}] file:{assets}/{build.APT_TAG}/ ./\n")
        settings = {"Dir::Etc::sourcelist": str(source), "Dir::Etc::sourceparts": "-",
                    "Dir::State::lists": str(lists), "Dir::State::status": "/dev/null",
                    "Dir::Cache": str(root / "apt-cache"), "Debug::NoLocking": "1", "APT::Architecture": "amd64"}
        options = [part for key, value in settings.items() for part in ("-o", f"{key}={value}")]
        build.run("apt-get", *options, "update")
        policy = build.run("apt-cache", *options, "policy", "sshclient", capture=True).decode()
        self.assertIn("Candidate: 1.2.3", policy)
        # Prove the bare Filename: resolves — this is the rewrite that makes a
        # flat repo work against a release download URL with no directories.
        build.run("apt-get", *options, "download", "sshclient", cwd=root)
        self.assertTrue(list(root.glob("sshclient*.deb")), "apt could not fetch from the flat repository")

    def check_pacman(self, root, assets):
        for names in build.ARCHES.values():
            native = names["native"]
            directory = assets / f"repo-arch-{native}"
            db = root / f"pacman-{native}"
            (db / "sync").mkdir(parents=True)
            shutil.copy2(directory / "vapourware-studios.db", db / "sync/vapourware-studios.db")
            config = db / "pacman.conf"
            config.write_text(f"[options]\nArchitecture = {native}\n"
                              f"[vapourware-studios]\nServer = file://{directory}\n")
            result = build.run("pacman", "--config", str(config), "--dbpath", str(db), "-Si", "sshclient", capture=True).decode()
            self.assertIn("1.2.3-1", result)

    def check_dnf(self, root, base, key):
        config = root / "dnf.conf"
        config.write_text(f"[main]\nreposdir={root}/no-repos\ncachedir={root}/dnf-cache\n"
                          f"persistdir={root}/dnf-state\nlogdir={root}/logs\n"
                          f"[demo]\nname=demo\nbaseurl={base}/site/rpm/x86_64/\n"
                          f"gpgkey=file://{key}\ngpgcheck=1\nrepo_gpgcheck=1\n")
        destination = root / "downloaded"
        destination.mkdir()
        build.run("dnf", "-y", "--config", str(config), "--releasever=1", "--forcearch=x86_64",
                  "download", "--destdir", str(destination), "sshclient")
        files = list(destination.glob("*.rpm"))
        self.assertEqual(len(files), 1)
        original = root / "assets/v1.2.3" / files[0].name
        self.assertEqual(build.checksum(files[0]), build.checksum(original))

    def check_rpm(self, root, assets, key):
        db = root / "rpmdb"
        db.mkdir()
        build.run("rpm", "--dbpath", str(db), "--import", str(key))
        found = list(assets.rglob("*.rpm"))
        self.assertEqual(len(found), len(build.ARCHES), "each CPU publishes one signed RPM")
        for package in found:
            result = build.run("rpmkeys", "--dbpath", str(db), "--checksig", str(package), capture=True).decode()
            self.assertIn("signatures OK", result)


if __name__ == "__main__":
    unittest.main()
