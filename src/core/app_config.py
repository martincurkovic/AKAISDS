import json
from pathlib import Path

from core import debug_log, sampler_models

CONFIG_PATH = Path.home() / ".akaisds/config.json"


def load_config():
    # load saved app config from disk
    # if none exists, returns an empty dict
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # a corrupt/unreadable config silently falling back to defaults used
        # to leave no trace anywhere - indistinguishable from "nothing saved
        # yet" in every caller, which makes a real bug here hard to tell
        # apart from a first-ever launch when a user reports it
        debug_log.get_logger().error(
            f"app_config: couldn't read {CONFIG_PATH} - falling back to defaults",
            exc_info=True,
        )
        return {}


def save_config(config):
    # overwrite saved config with current dict
    try:
        if not CONFIG_PATH.parent.exists():
            # only interesting the first time - logged BEFORE mkdir so this
            # still fires even if the mkdir itself is what fails below
            debug_log.get_logger().info(
                f"app_config: creating {CONFIG_PATH.parent} (didn't exist yet)"
            )
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        # confirms what actually landed on disk - pairs with
        # MidiSettingsDialog's own "applied" log (settings_dialog.py) so a
        # live-vs-persisted mismatch shows up as one log with an entry and
        # no matching write nearby, instead of another guessing session
        debug_log.get_logger().info(f"app_config: saved {config!r}")
    except OSError:
        # failed pref save doesnt crash the app, but it used to also leave
        # no record at all - a missing ~/.akaisds directory used to fail
        # here silently (no mkdir), making every port/channel/device-type
        # save a permanent no-op with nothing to diagnose it by
        debug_log.get_logger().error(
            f"app_config: couldn't save config to {CONFIG_PATH}", exc_info=True
        )


def get_saved_ports():
    # returns (input_name, output_name) or none if nothing saved yet
    config = load_config()
    return config.get("midi_input_port"), config.get("midi_output_port")


def save_ports(input_name, output_name):
    config = load_config()
    config["midi_input_port"] = input_name
    config["midi_output_port"] = output_name
    save_config(config)


def get_saved_channel():
    config = load_config()
    return config.get("midi_channel", 0)


def save_channel(channel):
    config = load_config()
    config["midi_channel"] = channel
    save_config(config)


def get_saved_device_type():
    # returns the saved sampler type selection - one of
    # core/sampler_models.py's three ("akai_s2000_s3000", "akai_s1000",
    # "generic"). a legacy plain "akai" (what this file held for the
    # S2000/S3000 before the S1000 existed) is returned as its modern
    # equivalent; so is anything unrecognised (hand-edited/newer config),
    # and so is nothing having been saved yet
    config = load_config()
    return sampler_models.normalize(
        config.get("device_type", sampler_models.AKAI_S2000_S3000)
    )


def save_device_type(device_type):
    config = load_config()
    config["device_type"] = device_type
    save_config(config)


def get_yamaha_device_number():
    # the Device Number set on a Yamaha A4000/A5000 (0-15) - what its SysEx is addressed to, separate from the
    # SDS "exclusive channel" above. Manual-edit-only for now (no Settings UI): config.json's
    # "yamaha_device_number"; anything missing or out of range reads as 0, the unit's usual setting
    value = load_config().get("yamaha_device_number", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 15 else 0


def get_yamaha_write_warning_acknowledged():
    # the one-time "editing a Yamaha A4000/A5000 is experimental" warning, shown before the first
    # parameter write (ui/yamaha_writer.py)
    return bool(load_config().get("yamaha_write_warning_acknowledged", False))


def save_yamaha_write_warning_acknowledged(acknowledged=True):
    config = load_config()
    config["yamaha_write_warning_acknowledged"] = bool(acknowledged)
    save_config(config)


def get_s1000_editor_warning_acknowledged():
    # whether the user has already clicked through the one-time "S1000
    # editing is experimental" warning (shown by the Dashboard before the
    # Program Editor first opens in S1000 mode)
    return bool(load_config().get("s1000_editor_warning_acknowledged", False))


def save_s1000_editor_warning_acknowledged(acknowledged=True):
    config = load_config()
    config["s1000_editor_warning_acknowledged"] = bool(acknowledged)
    save_config(config)


def get_s950_transfer_warning_acknowledged():
    # whether the user has already clicked through the one-time "S900/S950
    # transfers are experimental" warning (shown by the Dashboard before the
    # first Send in S900/S950 mode)
    return bool(load_config().get("s950_transfer_warning_acknowledged", False))


def save_s950_transfer_warning_acknowledged(acknowledged=True):
    config = load_config()
    config["s950_transfer_warning_acknowledged"] = bool(acknowledged)
    save_config(config)


def get_s950_program_write_warning_acknowledged():
    # the one-time "writing S900/S950 programs is experimental" warning, shown
    # before the first program write (ui/s950_program_editor.py)
    return bool(load_config().get("s950_program_write_warning_acknowledged", False))


def save_s950_program_write_warning_acknowledged(acknowledged=True):
    config = load_config()
    config["s950_program_write_warning_acknowledged"] = bool(acknowledged)
    save_config(config)


# default as of the shared MIDI transport (core/midi_transport.py) becoming
# the default connection mode - see core/midi_manager.py's own
# shared_transport_enabled(). Manual-edit-only by design: there's
# deliberately no Settings UI for this (an end user having a problem with
# it edits config.json by hand and sets this to false), so this key needs
# to actually land on disk (see _DEFAULTS/ensure_defaults_saved() below)
# rather than only implicitly defaulting in the getter - a hand-editing
# user needs to find the key there to know it exists
_DEFAULT_SHARED_MIDI_TRANSPORT = True


def get_shared_midi_transport_enabled():
    config = load_config()
    return config.get("shared_midi_transport", _DEFAULT_SHARED_MIDI_TRANSPORT)


# samples, not ms/bytes - same unit an Ableton-style buffer-size combobox
# shows (see ui/settings_dialog.py's _BUFFER_SIZE_OPTIONS), converted to
# milliseconds at playback time (see core/audio_preview.py, which passes
# it to miniaudio.PlaybackDevice's buffersize_msec) since that conversion
# depends on the sample rate actually in use
_DEFAULT_AUDIO_BUFFER_SAMPLES = 1024


def get_saved_audio_output_device():
    # hex-encoded miniaudio device id (see core.audio_preview.device_id_string),
    # or None for "system default" - the same meaning an unset/missing key
    # already has, so there's no separate sentinel to keep in sync
    config = load_config()
    return config.get("audio_output_device")


def save_audio_output_device(device_id):
    config = load_config()
    config["audio_output_device"] = device_id
    save_config(config)


def get_saved_audio_buffer_samples():
    config = load_config()
    return config.get("audio_buffer_samples", _DEFAULT_AUDIO_BUFFER_SAMPLES)


def save_audio_buffer_samples(buffer_samples):
    config = load_config()
    config["audio_buffer_samples"] = buffer_samples
    save_config(config)


def get_last_update_check():
    # unix timestamp of the last successful update check, or 0 if never
    config = load_config()
    return config.get("last_update_check", 0)


def save_last_update_check(timestamp):
    config = load_config()
    config["last_update_check"] = timestamp
    save_config(config)


def get_skipped_update_version():
    # version string the user chose "skip this version" for, or none
    config = load_config()
    return config.get("skipped_update_version")


def save_skipped_update_version(version):
    config = load_config()
    config["skipped_update_version"] = version
    save_config(config)


# the Settings > Appearance theme choice: "system" follows the OS light/dark
# setting (the original behaviour, and the default), "light"/"dark" pin it.
# The valid set lives here rather than in ui/theme.py so this module stays
# free of any ui import (ui.theme imports THESE).
THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_VALUES = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)


def get_saved_theme():
    # anything unrecognised (a hand-edited or newer config.json) is treated
    # as "system" rather than crashing or pinning an unknown theme
    theme = load_config().get("theme", THEME_SYSTEM)
    return theme if theme in THEME_VALUES else THEME_SYSTEM


def save_theme(theme):
    config = load_config()
    config["theme"] = theme if theme in THEME_VALUES else THEME_SYSTEM
    save_config(config)


# keys with a real default value worth persisting to disk, so a hand-edited
# or version-upgraded config.json that's missing one of them gets it filled
# back in rather than only ever defaulting implicitly in the getter above
# (every getter already does that too, so a missing key never crashes or
# misbehaves even before ensure_defaults_saved() below runs - this is about
# the key being discoverable/visible in the file, same reasoning
# shared_midi_transport originally needed). Deliberately excludes keys whose
# "unset" meaning (ports, audio_output_device, skipped_update_version,
# last_update_check=0/never) is itself the correct default - writing those
# out would just be noise, not a real default being filled in.
_DEFAULTS = {
    "midi_channel": 0,
    "device_type": sampler_models.AKAI_S2000_S3000,
    "theme": THEME_SYSTEM,
    "shared_midi_transport": _DEFAULT_SHARED_MIDI_TRANSPORT,
    "audio_buffer_samples": _DEFAULT_AUDIO_BUFFER_SAMPLES,
}


def ensure_defaults_saved():
    # writes any key in _DEFAULTS into config.json that isn't already there
    # - covers both a brand new install (load_config() returns {}, nothing
    # on disk yet) and a user upgrading from a version before a given key
    # existed. Adding a new entry to _DEFAULTS is all a future setting
    # needs to get this same treatment. Call once at startup, before
    # anything reads the live config - see ui/main_window.py's own call
    # site.
    config = load_config()
    changed = False
    missing = {key: value for key, value in _DEFAULTS.items() if key not in config}
    if missing:
        config.update(missing)
        changed = True
    # a config.json from before the S1000 existed saved the S2000/S3000 as
    # plain "akai" - rewrite it so someone reading the file sees the
    # hardware it actually means (get_saved_device_type already accepts
    # either spelling, so this is purely about the file being accurate)
    if config.get("device_type") == sampler_models.LEGACY_AKAI:
        config["device_type"] = sampler_models.AKAI_S2000_S3000
        changed = True
    if changed:
        save_config(config)
