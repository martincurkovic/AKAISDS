cask "akaisds" do
  arch arm: "arm", intel: "intel"

  version "1.2.1"
  sha256 arm:   "2016c60798bbaedb2e755e4bbfcb577fa5b10d1f208f74f1cbda42ec6346eafd",
         intel: "ab5918d04b5eae06a7fb81f7099585a8c9a91fa7d3e845c356357ac8bbdd8b32"

  url "https://github.com/martincurkovic/AKAISDS/releases/download/v#{version}/AKAISDS-macos-#{arch}.dmg"
  name "AKAISDS"
  desc "MIDI Sample Dump Standard transfer tool for Akai and generic SDS-compatible samplers"
  homepage "https://github.com/martincurkovic/AKAISDS"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: ">= :sequoia"

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
