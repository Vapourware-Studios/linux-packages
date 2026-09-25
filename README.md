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
curl -fSLo vapourware-studios.asc https://packages.vapourware-studios.net/signing-key.asc
gpg --show-keys --with-fingerprint vapourware-studios.asc
```

Expected fingerprint:

```
3C89AADAA804B642D3CD7408075B14EB2795823A
```

### Arch / Manjaro

```bash
sudo pacman-key --add vapourware-studios.asc
sudo pacman-key --lsign-key 3C89AADAA804B642D3CD7408075B14EB2795823A
curl -fSLo vapourware-studios.pacman https://packages.vapourware-studios.net/config/vapourware-studios.pacman
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
curl -fSLo vapourware-studios.sources https://packages.vapourware-studios.net/config/vapourware-studios.sources
sudo install -m 644 vapourware-studios.sources /etc/apt/sources.list.d/vapourware-studios.sources
sudo apt update
sudo apt install sshclient
```

### Fedora

```bash
sudo rpm --import vapourware-studios.asc
curl -fSLo vapourware-studios.repo https://packages.vapourware-studios.net/config/vapourware-studios.repo
sudo install -m 644 vapourware-studios.repo /etc/yum.repos.d/vapourware-studios.repo
sudo dnf install sshclient
```

### openSUSE

```bash
sudo rpm --import vapourware-studios.asc
sudo zypper addrepo --refresh https://packages.vapourware-studios.net/config/vapourware-studios.repo
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
5. The release event deploys the signed metadata to
   `packages.vapourware-studios.net` after verifying the checksum pinned in that
   commit. Deployment always uses the current main record so delayed events
   cannot roll the repository back.

## What is actually served

Only signed metadata is hosted: the package indexes, their signatures and the
public key, well under a megabyte per release. Package files are not copied
anywhere. The repository ships a `manifest.json` mapping each payload path to
the release that already holds those bytes, and the worker answers requests for
them with a redirect. Every package manager follows redirects, and the
signatures cover the content rather than its location, so this changes where
bytes come from and nothing about what is verified.

Debian and Arch packages redirect to the application release, byte-for-byte as
it built them. `rpmsign --addsign` rewrites an RPM to embed its signature, so
the checksum recorded in the RPM index only matches the signed copy; those are
republished on this repository's own release and redirect there instead.

Only source, configuration, the pinned fingerprint and the release record belong
in Git. If a package download races a repository update, refresh your
package-manager indexes and retry.

The package signing secret is stored as `LINUX_PACKAGE_SIGNING_KEY` in the
**sshclient** repository. The existing `THECOOLRAVEN_APP_ID` and
`THECOOLRAVEN_PRIVATE_KEY` secrets are reused there. This repository needs no copy
of those private keys.

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
