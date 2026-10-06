# Packaging AKAISDS for Homebrew, AUR, and Flathub

This is a draft/starting point, not something to run blindly. Read this whole
document before touching any of the other files in this folder. Everything
here was drafted against your **v1.1.1** release, which is already public, so
none of this requires you to cut a new version just to get started.

## What's in this folder

```
AKAISDS-packaging/
├── PACKAGING.md              this file
├── publish-packages.yml      GitHub Actions workflow (goes in AKAISDS's .github/workflows/)
├── homebrew/
│   └── akaisds.rb             draft Cask, ready to push to a new tap repo
└── aur/
    ├── PKGBUILD                draft AUR "-bin" package (goes in AKAISDS's repo too)
    ├── akaisds.desktop         Linux desktop entry bundled with the AUR package
    └── icon_512x512.png        app icon bundled with the AUR package
```

The `homebrew/akaisds.rb` and `aur/PKGBUILD` files already have **real, verified
sha256 checksums** computed from your actual v1.1.1 release assets — I
downloaded them and hashed them myself, so these two files are usable as-is
for a first manual submission.

## Glossary (since you're new to this)

- **Tap** — a Homebrew term for "a repo of package definitions." `homebrew-core`
  is Homebrew's official tap; anyone can also run their own by creating a repo
  named `homebrew-<something>`. `brew tap you/something` adds it.
- **Cask** — a Homebrew package definition specifically for prebuilt GUI apps
  (as opposed to a "Formula," which builds command-line tools from source).
  AKAISDS is a cask, not a formula.
- **PKGBUILD** — the Arch Linux equivalent of a Homebrew formula/cask: a shell
  script describing how to fetch and install a package. `makepkg` reads it.
- **AUR** — the Arch User Repository. Anyone can publish a PKGBUILD there via
  git push, no review process, no signing required. Arch users install AUR
  packages with a helper like `yay` or `paru`, or `makepkg` by hand.
- **`-bin` package** — an AUR naming convention for a package that installs a
  prebuilt binary instead of compiling from source. That's what's drafted
  here: it just downloads your existing AppImage.
- **Flathub** — the main Flatpak app store for Linux. Submission is a PR
  against `flathub/flathub` that a human reviews. No code signing involved,
  but the sandboxed build process and MIDI hardware access make it more work
  (see below).

## The big picture

You already have a `build.yml` that, on a pushed tag, builds all platforms and
creates a **draft** GitHub Release. Nothing under a draft release is publicly
downloadable — asset URLs 404 for anyone without your GitHub session. Once you
click "Publish release," the assets and API endpoints go live instantly.

`publish-packages.yml` (in this folder) is designed to run right after that:
it triggers on the `release: published` event — which only fires when you hit
Publish, never on draft creation — so there's no risk of it running early.

## Setting up Homebrew (your own tap)

1. On GitHub, create a **new, empty public repo** named exactly
   `homebrew-akaisds` under your account. The `homebrew-` prefix is required —
   that's how `brew tap` finds it.
2. Clone it locally and add `Casks/akaisds.rb` using the content from
   `homebrew/akaisds.rb` in this folder. Commit and push.
3. Test it yourself:
   ```
   brew tap martincurkovic/akaisds
   brew install --cask akaisds
   ```
   (Homebrew maps `homebrew-akaisds` → tap name `martincurkovic/akaisds`.)
4. You'll hit Gatekeeper on first launch — that's expected and is what the
   `caveats` block in the cask warns users about. I deliberately did **not**
   add a `postflight` step that silently strips the quarantine attribute;
   that's a legitimate option some unsigned-app taps use, but it means
   overriding a macOS security prompt on someone's machine without them
   clicking anything, which is worth deciding on purpose rather than by
   default. Your call — I'm happy to add it if you want that.
5. Once that works, you can wire up automation (see below) so future releases
   update the cask automatically.

**Note:** I guessed `AKAISDS.app`'s bundle behavior for the `app` stanza based
on your build script — I could not find an explicit macOS bundle identifier
set anywhere in `scripts/build_macos.sh`, so I left out a `zap` stanza (which
would clean up preference files on uninstall) rather than guess a wrong
identifier. You can add one later by checking:
```
plutil -p /Applications/AKAISDS.app/Contents/Info.plist | grep Bundle
```

## Setting up the AUR

1. Register an account at [aur.archlinux.org](https://aur.archlinux.org) if
   you don't have one.
2. Generate an SSH keypair dedicated to this (don't reuse a personal one):
   ```
   ssh-keygen -t ed25519 -f ~/.ssh/aur_akaisds -C "akaisds-aur"
   ```
   Add the **public** key to your AUR account (Account → My Account → SSH
   Public Key).
3. Locally, clone the (currently empty) AUR repo for the new package and push
   the files from `aur/` in this folder:
   ```
   git clone ssh://aur@aur.archlinux.org/akaisds-bin.git
   cp /path/to/AKAISDS-packaging/aur/* akaisds-bin/
   cd akaisds-bin
   makepkg --printsrcinfo > .SRCINFO
   git add PKGBUILD .SRCINFO akaisds.desktop icon_512x512.png
   git commit -m "Initial import: akaisds-bin 1.1.1"
   git push
   ```
4. **Test the PKGBUILD before pushing**, ideally on an actual Arch machine or
   container:
   ```
   makepkg -si
   ```
   I've listed `fuse2`, `qt6-base`, and `alsa-lib` as dependencies as a
   best guess based on what AppImages typically need at runtime — Nuitka's
   standalone build bundles most shared libraries itself, so the real
   dependency list might be shorter. Adjust based on what `makepkg -si`
   actually needs on a clean system.
5. For automation later, save the **private** key from step 2 as the
   `AUR_SSH_PRIVATE_KEY` secret in your AKAISDS GitHub repo, and your AUR
   account's email as `AUR_COMMIT_EMAIL`.

## Setting up the automation workflow

1. Copy `publish-packages.yml` into `AKAISDS/.github/workflows/`.
2. Copy the `aur/` folder into `AKAISDS/packaging/aur/` (the workflow reads
   `packaging/aur/PKGBUILD` etc. from the repo — adjust the path in the
   workflow if you put it somewhere else).
3. In the AKAISDS repo's Settings → Secrets and variables → Actions, add:
   - `TAP_REPO_TOKEN` — a **fine-grained** GitHub personal access token scoped
     to only the `homebrew-akaisds` repo, with Contents: Read & write. (Avoid
     a classic token with broad `repo` scope if you can — fine-grained tokens
     let you limit blast radius to just that one repo.)
   - `AUR_SSH_PRIVATE_KEY` — the private half of the SSH key from the AUR
     setup above.
   - `AUR_COMMIT_EMAIL` — the email tied to your AUR account.
4. **Test it against your existing v1.1.1 release** without waiting for a new
   version: go to Actions → Publish Packages → Run workflow, and type `v1.1.1`
   in the tag field. This is exactly why the workflow has a manual
   `workflow_dispatch` trigger — v1.1.1 predates this automation, so the
   `release: published` event never fired for it, but you don't need a new
   release just to bootstrap packaging.
5. Watch the run. If either job fails, nothing goes out — a failed Homebrew
   push doesn't affect the AUR job or vice versa, since they run as
   independent jobs off the same `checksums` job.
6. From then on, every time you click "Publish release" on a new tag, both
   the tap and AUR package update automatically.

## Flathub — separate and more manual

Flathub is not included as automation here because the setup is meaningfully
different and heavier:

- Submission is a **human-reviewed PR** against `flathub/flathub` — there's no
  way to automate your way past that first review, regardless of tooling.
- Flathub's sandboxed build has **no network access**, so `uv sync` /
  installing Python deps at build time the way your current CI does won't
  work directly. Dependencies need to be vendored/pinned in the manifest
  (tools like `flatpak-pip-generator` help with this).
- AKAISDS needs real MIDI hardware access (via `python-rtmidi`/ALSA), which
  the Flatpak sandbox blocks by default. You'll need to request device
  permissions in the manifest and be ready to justify that in review.
- Reviewers generally prefer builds from source over repackaging a prebuilt
  binary, unlike the Homebrew/AUR routes here.

Given that, I'd treat Flathub as its own follow-up project once the simpler
two are live and working, rather than something to rush into now. Happy to
draft the manifest + metainfo file in a separate pass when you're ready.

## Things to know before going live

- **You are vouching for these binaries.** Nothing about Homebrew or AUR
  reviews the actual binary content — you're personally asserting that the
  DMG/AppImage at that URL is safe. That's already implicitly true of your
  GitHub Releases today; packaging just adds two more places pointing at the
  same trust.
- **Upkeep expectation.** Once people install via `brew` or an AUR helper,
  they'll expect version bumps to keep working. A version that only exists on
  GitHub but never got promoted to the tap/AUR will look "stale" to those
  users. The automation above is what keeps this from becoming manual toil.
- **License.** AKAISDS is GPLv3 — that's compatible with all three of these
  distribution channels without any extra action on your part.
- **Un-signed macOS Gatekeeper friction persists regardless of Homebrew.**
  Packaging through a tap doesn't remove the Gatekeeper prompt; it just
  automates *fetching* the same DMG a user would otherwise download by hand.
  The disclaimer in your README still applies and is worth mirroring in the
  cask's `caveats` block (already done in the draft).
- **Don't reuse your AUR SSH key or the tap token elsewhere.** Both are
  scoped narrowly on purpose (to one repo, and to AUR pushes respectively) so
  a leak has limited blast radius.

## Suggested order of operations

1. Manually push the Homebrew tap and test `brew install --cask akaisds`
   locally.
2. Manually push the AUR package and test `makepkg -si` (ideally in an Arch
   VM/container, since you're likely not on Arch day-to-day).
3. Once both work by hand, wire up the secrets and land
   `publish-packages.yml`, and test it via `workflow_dispatch` against
   `v1.1.1`.
4. Cut a real new release (e.g. v1.1.2) normally and confirm the automation
   picks it up on publish, end to end.
5. Revisit Flathub as a separate effort.
