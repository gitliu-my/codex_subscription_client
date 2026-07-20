# Releasing csub for macOS

The first distribution target is Apple Silicon macOS. A version tag builds the standalone
runtime, publishes it to GitHub Releases, and updates `gitliu-my/homebrew-tap`.

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

After the release commit is on `main`, create and push the matching version tag:

```bash
git tag v0.5.0
git push origin main
git push origin v0.5.0
```

The `Release` workflow then:

1. verifies that the tag equals the package version;
2. runs the test suite on a native arm64 macOS runner;
3. uploads `csub-macos-arm64.tar.gz` and `SHA256SUMS`;
4. renders and pushes `Formula/csub.rb` to the existing Homebrew Tap.

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
