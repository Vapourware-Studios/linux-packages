# Vapourware-Studios Linux packages

Signed packages for [SSH Client](https://github.com/Vapourware-Studios/sshclient),
maintained by `thecoolraven[bot]` through the application's release workflow.
Packages and indexes use GitHub Releases and raw repository files. No website,
GitHub Pages deployment, or shell installer is required.

<!-- publication-status -->
The first signed package publication is pending. The setup URLs below become available after that publication succeeds.

Packages are built for x86-64 and ARM64 on glibc distributions. Alpine's `apk`
is not supported by these builds. This is the project's own repository, separate
from the distributions' default repositories.

## One-time setup

Download the public signing key and compare its fingerprint before trusting it:

```bash
curl -fSLo vapourware-studios.asc https://raw.githubusercontent.com/Vapourware-Studios/linux-packages/main/signing-key.asc
gpg --show-keys --with-fingerprint vapourware-studios.asc
```

Expected fingerprint:

```
7C01CAE212F0303542C62546FC8F47D2954BDB29
```

### Arch / Manjaro

```bash
sudo pacman-key --add vapourware-studios.asc
sudo pacman-key --lsign-key 7C01CAE212F0303542C62546FC8F47D2954BDB29
curl -fSLo vapourware-studios.pacman https://raw.githubusercontent.com/Vapourware-Studios/linux-packages/main/config/vapourware-studios.pacman
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
curl -fSLo vapourware-studios.sources https://raw.githubusercontent.com/Vapourware-Studios/linux-packages/main/config/vapourware-studios.sources
sudo install -m 644 vapourware-studios.sources /etc/apt/sources.list.d/vapourware-studios.sources
sudo apt update
sudo apt install sshclient
```

### Fedora

```bash
sudo rpm --import vapourware-studios.asc
curl -fSLo vapourware-studios.repo https://raw.githubusercontent.com/Vapourware-Studios/linux-packages/main/config/vapourware-studios.repo
sudo install -m 644 vapourware-studios.repo /etc/yum.repos.d/vapourware-studios.repo
sudo dnf install sshclient
```

### openSUSE

```bash
sudo rpm --import vapourware-studios.asc
sudo zypper addrepo --refresh https://raw.githubusercontent.com/Vapourware-Studios/linux-packages/main/config/vapourware-studios.repo
sudo zypper refresh
sudo zypper install sshclient
```

After setup, normal system upgrades update SSH Client. Compare any signing-key
prompt with the fingerprint above. Keep signature checking enabled.

## Publishing

The application release workflow builds and smoke-tests both CPUs before
publishing. It then calls its `linux-repository.yml` workflow, which mints a
short-lived TheCoolRaven token scoped to this repository. The same workflow can
be dispatched with a published application version to retry a failed package
publication without rebuilding the application or updating Homebrew again.

The publisher verifies the source release's SHA-256 manifests and checks each
package's name, version and architecture. It signs the packages and indexes,
uploads and verifies release assets, then commits the public key, RPM metadata
and completion record as `thecoolraven[bot]`. A failed upload cannot advance the
completion record. Older releases cannot overwrite newer completed releases.

APT and pacman read flat repositories from fixed release tags `repo-apt` and
`repo-arch-$arch`. RPM metadata is committed under `metadata/rpm/$basearch` and
served through raw GitHub URLs; its package locations point to signed packages
in a versioned release. Packages are uploaded before indexes. Old RPM metadata
files and package assets are retained so cached indexes still resolve. If an
index refresh races publication or GitHub caching, refresh and retry.

Only public metadata belongs in Git. Package payloads belong in Releases.
The private package-signing key stays in the application repository's
`LINUX_PACKAGE_SIGNING_KEY` secret. That repository also holds
`THECOOLRAVEN_APP_ID` and `THECOOLRAVEN_PRIVATE_KEY`; this repository needs no
copies of the private keys. The public fingerprint is pinned here. Key rotation
requires explicit migration instructions for installations that trusted the old
key. Keep an offline backup of the signing key.

## Validation

```bash
python3 -m unittest discover -s test -v
```

CI installs the native package tools. Tests create disposable signing keys and
packages for both CPUs, validate APT downloads and pacman indexes, verify RPM
signatures, and make DNF download from signed metadata over HTTP with packages
at a separate release-style URL. Tests also cover first publication, version
ordering, and interrupted uploads. A passing metadata test does not establish
that every desktop, physical serial device, or Linux distribution is supported.
