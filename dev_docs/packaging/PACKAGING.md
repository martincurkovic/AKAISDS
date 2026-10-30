# Packaging AKAISDS for Homebrew, AUR and Flathub

Updated 2026-10-12. **Homebrew is live** (tap `martincurkovic/homebrew-tap`, tested by hand against v1.2.1). AUR and Flathub are still drafts.

```
.github/workflows/publish-packages.yml   bumps the tap (and, when enabled, the AUR) when a release is published
dev_docs/packaging/
  homebrew/akaisds.rb    copy of the cask in the tap repo (the tap's copy is the real one)
  aur/PKGBUILD           draft AUR "-bin" package (downloads the AppImage)
  aur/akaisds.desktop, aur/icon_512x512.png   bundled with the AUR package
```

How it fits: `build.yml` builds all platforms on a pushed tag and creates a DRAFT release (asset URLs 404 until you publish it).
`publish-packages.yml` runs on `release: published` (never on a draft, never on a pre-release such as `-rc1`) and can also be run by hand
(`workflow_dispatch`, with a tag). It only works once it is on `master`.

## Homebrew (own tap)

Why a tap: Homebrew 5.0 dropped `--no-quarantine`, and the official homebrew-cask stops supporting casks that fail Gatekeeper (1 Sept 2026);
AKAISDS is unsigned. Don't suggest `--no-quarantine` to users.

1. Public repo named exactly `homebrew-tap` (renamed from `homebrew-akaisds` on 2026-10-13: it also holds akaiutil-GUI and JV Studio) with `Casks/akaisds.rb` (start from `homebrew/akaisds.rb`; it requires macOS 13+, expressed as `:ventura`). `Casks/akaisds-legacy.rb` (template `homebrew/akaisds-legacy.rb`) is the macOS 12-and-later build (also a fallback on newer macOS): `depends_on macos: :monterey`, `conflicts_with` the main cask, and the publish workflow bumps it too when the release has the `-legacy` DMGs and the tap has the file.
2. Test: `brew tap martincurkovic/tap && brew audit --cask --online martincurkovic/tap/akaisds && brew install --cask martincurkovic/tap/akaisds`.
3. Gatekeeper will prompt on first launch - the cask's `caveats` say so. There is deliberately no `postflight` that strips the quarantine
   attribute: overriding a macOS security prompt silently should be a conscious decision.
4. No `zap` stanza on purpose: `~/.akaisds` holds the user's sampler backups, and `brew uninstall --zap` must not delete them. The bundle
   identifier is `AKAISDS` (`CFBundleIdentifier`).
5. The workflow edits only the `version` and two `sha256` lines of the tap's cask. **Keep those three line shapes** (two-space indent,
   `sha256 arm: "..."`, `intel: "..."` on the next line) or the workflow's grep check fails the job. Everything else in the cask is edited by hand in the tap.

### Second cask: akaiutil-gui

The same tap also holds `Casks/akaiutil-gui.rb` (start from `homebrew/akaiutil-gui.rb`; needs macOS 14.4+, expressed as `:sonoma`).
Its repo (`martincurkovic/akaiutil-gui`) has its own `update-homebrew-tap.yml`, which bumps that cask on a published release; it needs a
`TAP_REPO_TOKEN` secret too (the same tap-scoped token works). Asset names there are `akaiutil-GUI-macos-{arm,intel}.dmg`.

## AUR

1. Register at aur.archlinux.org and add a dedicated SSH public key (`ssh-keygen -t ed25519 -f ~/.ssh/aur_akaisds`).
2. `git clone ssh://aur@aur.archlinux.org/akaisds-bin.git`, copy `aur/*` in, `makepkg --printsrcinfo > .SRCINFO`, commit and push.
3. Test with `makepkg -si` on a real Arch machine or container. The dependency list (`fuse2`, `qt6-base`, `alsa-lib`) is a guess; Nuitka's standalone
   build bundles most libraries, so it may be shorter.

## Automation

Add repo secret `TAP_REPO_TOKEN` (a **fine-grained** token scoped to `homebrew-tap` only, Contents: read and write). The `aur` job is OFF
until the repo variable `PUBLISH_AUR` is `true` and the secrets `AUR_SSH_PRIVATE_KEY` (the key above) and `AUR_COMMIT_EMAIL` exist. Bootstrap
by running the workflow by hand against an existing tag, since `release: published` never fired for old releases. Don't reuse the AUR key or
the tap token elsewhere. A failing job leaves the tap untouched; re-run it from the Actions tab.

## Flathub (separate, more manual)

A human-reviewed PR against `flathub/flathub`; the sandboxed build has no network, so Python dependencies must be vendored in the manifest
(`flatpak-pip-generator`); MIDI hardware access (ALSA) must be requested and justified; reviewers prefer source builds to repackaged binaries.
Treat it as its own project once Homebrew and AUR work.

## Before going live

You are vouching for these binaries (neither Homebrew nor AUR reviews them) and users will expect version bumps to keep working - that is what
the automation is for. AKAISDS is GPLv3, compatible with all three channels. Gatekeeper friction remains; mirror the README disclaimer in the
cask. Suggested order: Homebrew by hand, AUR by hand, wire the secrets and test via `workflow_dispatch`, cut a real release and confirm it
end to end, then look at Flathub.
