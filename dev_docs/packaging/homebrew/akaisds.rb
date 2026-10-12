cask "akaisds" do
  arch arm: "arm", intel: "intel"

  version "1.1.1"
  sha256 arm:   "3acf173283145aedab62674b4cc323ac1ef292637e3745b9a83fb12c80d07f70",
         intel: "3c7055f4e0cb134207c0f0bea04876690e20bcbd15384e47ae4203e192b0e387"

  url "https://github.com/martincurkovic/AKAISDS/releases/download/v#{version}/AKAISDS-macos-#{arch}.dmg"
  name "AKAISDS"
  desc "MIDI Sample Dump Standard transfer tool for Akai and generic SDS-compatible samplers"
  homepage "https://github.com/martincurkovic/AKAISDS"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "AKAISDS.app"

  caveats <<~EOS
    AKAISDS is not code-signed or notarized by Apple, since it's a free,
    single-maintainer open-source project. macOS Gatekeeper will block it on
    first launch. To open it:

      1. Try to launch AKAISDS from Applications (it will be blocked)
      2. Open System Settings > Privacy & Security
      3. Scroll down and click "Open Anyway" next to the AKAISDS message
      4. Confirm with your password or Touch ID

    See https://github.com/martincurkovic/AKAISDS#disclaimer for details.
  EOS
end
