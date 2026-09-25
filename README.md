# Vapourware-Studios Linux packages

Signed packages for [SSH Client](https://github.com/Vapourware-Studios/sshclient),
maintained by `thecoolraven[bot]` through the application's release workflow.

<!-- publication-status -->
The first signed package publication is pending. The setup URLs below become available after that publication succeeds.

The repositories cover Arch/Manjaro, Ubuntu/Debian, and Fedora/openSUSE on x86-64
and ARM64. These packages use glibc. Alpine's `apk` is not supported by these
builds. This is the project's own package repository; it is separate from the
distributions' default repositories.

## One-time setup

Download the public signing key and check its fingerprint before installing it:

```bash
curl -fSLo vapourware-studios.asc https://vapourware-studios.github.io/linux-packages/signing-key.asc
gpg --show-keys --with-fingerprint vapourware-studios.asc
```

Expected fingerprint:

```
30CA22DD01ECC0BE82FC9C4B31EBC3525661FEA1
```

### Arch / Manjaro

```bash
sudo pacman-key --add vapourware-studios.asc
sudo pacman-key --lsign-key 30CA22DD01ECC0BE82FC9C4B31EBC3525661FEA1
curl -fSLo vapourware-studios.pacman https://vapourware-studios.github.io/linux-packages/config/vapourware-studios.pacman
sudo install -m 644 vapourware-studios.pacman /etc/pacman.d/vapourware-studios
```

Add this line once at the end of `/etc/pacman.conf`:

```ini
Include = /etc/pacman.d/vapourware-studios
```

Then install:

```bash
sudo pacman -Syu sshclient
```

### Ubuntu / Debian

```bash
sudo install -d -m 755 /usr/share/keyrings
sudo install -m 644 vapourware-studios.asc /usr/share/keyrings/vapourware-studios.asc
curl -fSLo vapourware-studios.sources https://vapourware-studios.github.io/linux-packages/config/vapourware-studios.sources
sudo install -m 644 vapourware-studios.sources /etc/apt/sources.list.d/vapourware-studios.sources
sudo apt update
sudo apt install sshclient
```

### Fedora

```bash
sudo rpm --import vapourware-studios.asc
curl -fSLo vapourware-studios.repo https://vapourware-studios.github.io/linux-packages/config/vapourware-studios.repo
sudo install -m 644 vapourware-studios.repo /etc/yum.repos.d/vapourware-studios.repo
sudo dnf install sshclient
```

### openSUSE

```bash
sudo rpm --import vapourware-studios.asc
sudo zypper addrepo --refresh https://vapourware-studios.github.io/linux-packages/config/vapourware-studios.repo
sudo zypper refresh
sudo zypper install sshclient
```

After setup, normal system upgrades update SSH Client. Package managers may ask
you to accept the signing key; compare its fingerprint with the value above.
No shell installer is used. The app's functionality and Linux validation
coverage are documented in the [application repository](https://github.com/Vapourware-Studios/sshclient/blob/main/docs/linux.md).

## How updates are published

1. SSH Client's release workflow builds and tests Linux packages for both CPUs.
2. The Linux update job mints a short-lived TheCoolRaven app token, scoped to
   this repository, using the same app secrets as the Homebrew tap.
3. It verifies each package against the source release's SHA-256 manifest,
   checks package identity/version/CPU, and signs the native repositories.
4. `thecoolraven[bot]` commits `release.json`, pushes it here, and publishes the
   signed repository archive as a GitHub Release. An older job cannot overwrite
   a newer version.
5. The release event deploys the hosted metadata after verifying the checksum
   pinned in that commit. Deployment always uses the current main record so
   delayed events cannot roll the repository back.

## What is actually hosted

Almost nothing. apt and pacman both support a *flat* repository — one directory,
no `dists/` or `pool/` hierarchy — so their indexes, signatures and packages are
published as GitHub release assets under fixed tags (`repo-apt`,
`repo-arch-$arch`) and read straight from there. An installed configuration
keeps working because those tags never change name.

dnf is the exception: it always looks for `<baseurl>/repodata/repomd.xml`, a
directory path that a release asset name cannot express. So its repodata alone
is served from Pages, while `createrepo_c --baseurl` writes an `xml:base` that
sends dnf to the release for the packages themselves.

The result is a few hundred kilobytes of hosted metadata. No package byte is
ever served from Pages, which keeps the repository clear of its size and
bandwidth limits. The build refuses to publish if a package reaches the hosted
site or the metadata grows unexpectedly large.

Only the source code, configuration, public signing-key fingerprint and release
record belong in Git. If a package download races a repository update, refresh
your package-manager indexes and retry.

The package signing secret is stored as `LINUX_PACKAGE_SIGNING_KEY` in the
**sshclient** repository. The existing `THECOOLRAVEN_APP_ID` and
`THECOOLRAVEN_PRIVATE_KEY` secrets are reused there. This repository needs no copy
of those private keys. Its publishing workflow uses GitHub's Pages deployment
permissions and makes no Git commits.

The signing key does not expire. Rotating a package signing key is disruptive —
every machine that completed the setup above must explicitly trust the
replacement — so treat the private key as long-lived and keep an offline backup.
If it must be rotated, update the pinned fingerprint, republish, and publish
clear migration instructions alongside the change.

## Testing

```bash
python3 -m unittest discover -s test -v
```

CI installs the native Debian, RPM and pacman tools. Its integration test builds
small temporary packages for both CPUs, signs them, and checks the resulting
indexes with native package-manager tools. Signing keys used by tests are
created in temporary directories and never committed.
