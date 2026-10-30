cask "akaisds-legacy" do
  arch arm: "arm", intel: "intel"

  # Placeholders until the first release with a legacy build (1.4.2): the AKAISDS repo's
  # publish workflow rewrites version and both sha256 lines on every release.
  version "1.4.2"
  sha256 arm:   "0000000000000000000000000000000000000000000000000000000000000000",
         intel: "0000000000000000000000000000000000000000000000000000000000000001"

  url "https://github.com/martincurkovic/AKAISDS/releases/download/v#{version}/AKAISDS-macos-#{arch}-legacy.dmg"
  name "AKAISDS (legacy)"
  desc "MIDI Sample Dump Standard transfer tool and editor for samplers"
  homepage "https://github.com/martincurkovic/AKAISDS"

  livecheck do
    url :url
    strategy :github_latest
  end

  # the same AKAISDS.app as the akaisds cask, built on an older Qt
  conflicts_with cask: "martincurkovic/tap/akaisds"
  depends_on macos: :monterey

  app "AKAISDS.app"

  caveats <<~EOS
    This is the AKAISDS build for macOS 12 (Monterey) and later, made with an
    older Qt. On macOS 13 or later the regular one is the better first choice:
    brew install --cask martincurkovic/tap/akaisds
    Use this one if that doesn't run on your Mac.

    AKAISDS is not code-signed or notarized by Apple, since it's a free,
    single-maintainer open-source project. macOS Gatekeeper will block it on
    first launch. To open it:

      1. Try to launch AKAISDS from Applications (it will be blocked)
      2. Open System Settings > Privacy & Security
      3. Scroll down and click "Open Anyway" next to the AKAISDS message
      4. Confirm with your password or Touch ID

    Or skip the prompt by clearing macOS's quarantine flag from Terminal:

      xattr -dr com.apple.quarantine /Applications/AKAISDS.app

    See https://github.com/martincurkovic/AKAISDS#disclaimer for details.
  EOS
end
