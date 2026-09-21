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
