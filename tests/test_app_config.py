# tests for app_config.py
# uses a fake CONFIG_PATH at a temp file
# so it never touches the real config file used by an actual user

from core import app_config


def _use_temp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "test_config.json")


def test_get_saved_ports_defaults_to_none(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_saved_ports() == (None, None)


def test_save_and_get_ports_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_ports("My Input", "My Output")
    assert app_config.get_saved_ports() == ("My Input", "My Output")


def test_get_saved_channel_defaults_to_zero(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_saved_channel() == 0


def test_save_and_get_channel_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_channel(5)
    assert app_config.get_saved_channel() == 5


def test_get_saved_device_type_defaults_to_akai(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_saved_device_type() == "akai"


def test_save_and_get_device_type_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_device_type("generic")
    assert app_config.get_saved_device_type() == "generic"


def test_get_last_update_check_defaults_to_zero(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_last_update_check() == 0


def test_save_and_get_last_update_check_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_last_update_check(1234.5)
    assert app_config.get_last_update_check() == 1234.5


def test_get_skipped_update_version_defaults_to_none(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_skipped_update_version() is None


def test_save_and_get_skipped_update_version_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_skipped_update_version("1.2.0")
    assert app_config.get_skipped_update_version() == "1.2.0"


def test_save_creates_missing_parent_directory(monkeypatch, tmp_path):
    # regression test: on a machine where ~/.akaisds has never been created
    # (e.g. by dropped_files.py or debug_log.py, which both mkdir their own
    # parent), save_config used to silently swallow the resulting
    # FileNotFoundError - the port/channel/device-type save appeared to
    # succeed (no crash, no error shown) but nothing ever actually landed on
    # disk, so get_saved_ports() kept returning (None, None) forever even
    # after the user picked real ports in MIDI Settings. Every existing test
    # above uses tmp_path directly, which pytest already creates, so none of
    # them exercise a genuinely-missing parent directory.
    missing_dir_config = tmp_path / "not_created_yet" / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_PATH", missing_dir_config)

    app_config.save_ports("My Input", "My Output")

    assert missing_dir_config.exists()
    assert app_config.get_saved_ports() == ("My Input", "My Output")


def test_get_saved_audio_output_device_defaults_to_none(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_saved_audio_output_device() is None


def test_save_and_get_audio_output_device_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_audio_output_device("deadbeef")
    assert app_config.get_saved_audio_output_device() == "deadbeef"


def test_get_saved_audio_buffer_samples_defaults_to_1024(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_saved_audio_buffer_samples() == 1024


def test_save_and_get_audio_buffer_samples_round_trip(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_audio_buffer_samples(1024)
    assert app_config.get_saved_audio_buffer_samples() == 1024


def test_get_shared_midi_transport_enabled_defaults_to_true(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    assert app_config.get_shared_midi_transport_enabled() is True


def test_ensure_defaults_saved_writes_defaults_on_fresh_config(monkeypatch, tmp_path):
    # no config file at all yet (a brand new install) - CONFIG_PATH.exists()
    # is False, load_config() returns {}
    _use_temp_config(monkeypatch, tmp_path)
    assert not app_config.CONFIG_PATH.exists()

    app_config.ensure_defaults_saved()

    assert app_config.CONFIG_PATH.exists()
    config = app_config.load_config()
    assert config["shared_midi_transport"] is True
    assert config["midi_channel"] == 0
    assert config["device_type"] == "akai"
    assert config["audio_buffer_samples"] == 1024


def test_ensure_defaults_saved_adds_missing_keys_without_touching_rest(
    monkeypatch, tmp_path
):
    # simulates a user upgrading from a version before some of these
    # settings existed - an existing config.json with OTHER settings
    # already saved, just missing the newer default-backed keys
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_ports("My Input", "My Output")
    app_config.save_channel(7)
    loaded = app_config.load_config()
    assert "shared_midi_transport" not in loaded
    assert "device_type" not in loaded

    app_config.ensure_defaults_saved()

    config = app_config.load_config()
    assert config["shared_midi_transport"] is True
    assert config["device_type"] == "akai"
    assert config["audio_buffer_samples"] == 1024
    assert config["midi_input_port"] == "My Input"
    assert config["midi_output_port"] == "My Output"
    # already-saved, non-default value must survive untouched
    assert config["midi_channel"] == 7


def test_ensure_defaults_saved_leaves_existing_values_alone(monkeypatch, tmp_path):
    # a user who already hand-edited a default-backed key (e.g. set
    # shared_midi_transport to false after hitting a problem) must not
    # have that choice silently overwritten on the next launch
    _use_temp_config(monkeypatch, tmp_path)
    config = app_config.load_config()
    config["shared_midi_transport"] = False
    config["midi_channel"] = 3
    app_config.save_config(config)

    app_config.ensure_defaults_saved()

    assert app_config.get_shared_midi_transport_enabled() is False
    assert app_config.get_saved_channel() == 3


def test_ensure_defaults_saved_is_a_noop_when_nothing_missing(monkeypatch, tmp_path):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.ensure_defaults_saved()
    first_write = app_config.load_config()

    app_config.ensure_defaults_saved()

    assert app_config.load_config() == first_write


def test_settings_saved_independently_dont_clobber_each_other(monkeypatch, tmp_path):
    # save ports/channel/device type each read, modify, write the same config file
    # confirm whether saving ONE doesnt wipe out something saved earlier
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_ports("In", "Out")
    app_config.save_channel(3)
    app_config.save_device_type("generic")

    assert app_config.get_saved_ports() == ("In", "Out")
    assert app_config.get_saved_channel() == 3
    assert app_config.get_saved_device_type() == "generic"
