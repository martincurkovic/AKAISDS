import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".akaisds_config.json"


def load_config():
    # load saved app config from disk
    # if none exists, returns an empty dict
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config):
    # overwrite saved config with current dict
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass  # failed pref save doesnt crash the app


def get_saved_ports():
    # returns (input_name, output_name) or none if nothing saved yet
    config = load_config()
    return config.get("midi_input_port"), config.get("midi_output_port")


def save_ports(input_name, output_name):
    config = load_config()
    config["midi_input_port"] = input_name
    config["midi_output_port"] = output_name
    save_config(config)
