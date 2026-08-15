# Releasing csub for macOS and Linux

A version tag builds Apple Silicon macOS and Linux x86_64 standalone runtimes, publishes them
to GitHub Releases, and updates the macOS Formula in `gitliu-my/homebrew-tap`.

## One-time setup

1. Create a fine-grained GitHub personal access token that can write repository contents only
   in `gitliu-my/homebrew-tap`.
2. Add it to `gitliu-my/codex_subscription_client` as the Actions secret
   `HOMEBREW_TAP_TOKEN`.
3. Keep the version in `pyproject.toml`, `src/codex_subscription/__init__.py`, and
   `CHANGELOG.md` synchronized.

When the token is absent, the release job skips the Homebrew update and emits a notice. GitHub
Release creation still succeeds, and the Formula can then be updated manually with the checksum
from `SHA256SUMS`.

## Create a release

Run the test and packaging checks locally:

```bash
python3 -m unittest discover -s tests -v
./scripts/build_macos.sh
./scripts/package_macos_release.sh
```

Linux packaging is verified on an Ubuntu 22.04 x86_64 host or by the release workflow:

```bash
./scripts/build_linux.sh
./scripts/package_linux_release.sh
./scripts/install_linux.sh release/csub-linux-x86_64.tar.gz
```

After the release commit is on `main`, create and push the matching version tag:

```bash
git tag v0.5.0
git push origin main
git push origin v0.5.0
```

The `Release` workflow then:

1. verifies that the tag equals the package version;
2. runs the test suite and builds on native arm64 macOS and Ubuntu 22.04 x86_64 runners;
3. uploads both platform archives and one combined `SHA256SUMS`;
4. renders and pushes `Formula/csub.rb` to the existing Homebrew Tap.

## Linux verification

On a clean x86_64 Linux account without sudo access:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/gitliu-my/codex_subscription_client/main/scripts/install_linux.sh \
  | sh
export PATH="$HOME/.local/bin:$PATH"
csub --help
csub login
```

## Homebrew verification

Homebrew 6 requires explicit trust for third-party Tap content. Install and test with:

```bash
brew tap gitliu-my/tap
brew trust --formula gitliu-my/tap/csub
brew install gitliu-my/tap/csub
csub --help
brew test gitliu-my/tap/csub
```

Upgrade with:

```bash
brew update
brew upgrade csub
csub restart
```

The restart moves any running background API process onto the newly installed runtime.
