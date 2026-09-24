import json
from pathlib import Path

from core import debug_log

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
    # returns the saved sampler device type (eg, akai, generic, etc)
    # defaults to akai if nothing has been saved yet
    config = load_config()
    return config.get("device_type", "akai")


def save_device_type(device_type):
    config = load_config()
    config["device_type"] = device_type
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
