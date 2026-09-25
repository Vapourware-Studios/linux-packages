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
3C89AADAA804B642D3CD7408075B14EB2795823A
```

### Arch / Manjaro

```bash
sudo pacman-key --add vapourware-studios.asc
sudo pacman-key --lsign-key 3C89AADAA804B642D3CD7408075B14EB2795823A
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
5. The release event deploys the archive to GitHub Pages after verifying the
   checksum pinned in that commit. Deployment always uses the current main
   record so delayed events cannot roll the repository back.

Only the source code, configuration, public signing-key fingerprint and release
record belong in Git. Binaries and generated metadata are deployment artifacts;
versioned archives stay in GitHub Releases. GitHub Pages serves the current
release, with a size check before publishing. If a package download races a
repository update, refresh your package-manager indexes and retry.

The package signing secret is stored as `LINUX_PACKAGE_SIGNING_KEY` in the
**sshclient** repository. The existing `THECOOLRAVEN_APP_ID` and
`THECOOLRAVEN_PRIVATE_KEY` secrets are reused there. This repository needs no copy
of those private keys. Its publishing workflow uses GitHub's Pages deployment
permissions and makes no Git commits.

The signing key expires after two years. Renew the same key before expiry,
replace its Actions secret, and republish so clients can refresh the public key.
If rotating to a different key, update the pinned fingerprint and publish clear
migration instructions; clients must explicitly trust the replacement.

## Testing

```bash
python3 -m unittest discover -s test -v
```

CI installs the native Debian, RPM and pacman tools. Its integration test builds
small temporary packages for both CPUs, signs them, and checks the resulting
indexes with native package-manager tools. Signing keys used by tests are
created in temporary directories and never committed.
