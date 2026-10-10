# Packaging AKAISDS for Homebrew, AUR and Flathub

A draft, **not active**: nothing here is under `.github/workflows/`. Condensed 2026-10-10. The files were drafted against the public v1.1.1
release and carry real sha256 checksums from its assets; update the version and checksums for a newer release.

```
dev_docs/packaging/
  publish-packages.yml   GitHub Actions workflow (would go in .github/workflows/)
  homebrew/akaisds.rb    draft Cask for a tap repo
  aur/PKGBUILD           draft AUR "-bin" package (downloads the AppImage)
  aur/akaisds.desktop, aur/icon_512x512.png   bundled with the AUR package
```

How it fits: `build.yml` builds all platforms on a pushed tag and creates a DRAFT release (asset URLs 404 until you publish it).
`publish-packages.yml` runs on `release: published` (never on draft creation) and can also be run by hand (`workflow_dispatch`, with a tag).

## Homebrew (own tap)

1. Create an empty public repo named exactly `homebrew-akaisds`; add `Casks/akaisds.rb` from `homebrew/akaisds.rb`.
2. Test: `brew tap martincurkovic/akaisds && brew install --cask akaisds`.
3. Gatekeeper will prompt on first launch (the app is unsigned) - the cask's `caveats` say so. There is deliberately no `postflight` that strips the
   quarantine attribute: overriding a macOS security prompt silently should be a conscious decision. No `zap` stanza yet (no bundle identifier is set in
   `build_macos.sh`); find it with `plutil -p /Applications/AKAISDS.app/Contents/Info.plist | grep Bundle`.

## AUR

1. Register at aur.archlinux.org and add a dedicated SSH public key (`ssh-keygen -t ed25519 -f ~/.ssh/aur_akaisds`).
2. `git clone ssh://aur@aur.archlinux.org/akaisds-bin.git`, copy `aur/*` in, `makepkg --printsrcinfo > .SRCINFO`, commit and push.
3. Test with `makepkg -si` on a real Arch machine or container. The dependency list (`fuse2`, `qt6-base`, `alsa-lib`) is a guess; Nuitka's standalone
   build bundles most libraries, so it may be shorter.

## Automation

Copy `publish-packages.yml` into `.github/workflows/` and `aur/` into `packaging/aur/` (or adjust the path in the workflow). Add repo secrets:
`TAP_REPO_TOKEN` (a **fine-grained** token scoped to the tap repo only, Contents: read and write), `AUR_SSH_PRIVATE_KEY` (the key above) and
`AUR_COMMIT_EMAIL`. Bootstrap by running the workflow by hand against an existing tag (e.g. `v1.1.1`), since `release: published` never fired
for it. The Homebrew and AUR jobs are independent, so one failing doesn't stop the other. Don't reuse the AUR key or the tap token elsewhere.

## Flathub (separate, more manual)

A human-reviewed PR against `flathub/flathub`; the sandboxed build has no network, so Python dependencies must be vendored in the manifest
(`flatpak-pip-generator`); MIDI hardware access (ALSA) must be requested and justified; reviewers prefer source builds to repackaged binaries.
Treat it as its own project once Homebrew and AUR work.

## Before going live

You are vouching for these binaries (neither Homebrew nor AUR reviews them) and users will expect version bumps to keep working - that is what
the automation is for. AKAISDS is GPLv3, compatible with all three channels. Gatekeeper friction remains; mirror the README disclaimer in the
cask. Suggested order: Homebrew by hand, AUR by hand, wire the secrets and test via `workflow_dispatch`, cut a real release and confirm it
end to end, then look at Flathub.
