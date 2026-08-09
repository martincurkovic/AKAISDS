# Tests for ui/theme.py's render_stylesheet (the QSS template renderer)
# forgetting to add a colour to one of the 2 palettes should raise a KeyError at startup,
# instead of just silently rendering the wrong colour in the UI
# _write_colored_chevron writes tiny SVG files to a per-user directory
# moneypatched to a tmp_path for the test only, same as test_app_config.py
# which redirects CONFIG_PATH to temp, so these tests never touch the real configs

from ui import theme


def _use_temp_icons_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        theme, "_GENERATED_ICONS_DIR", str(tmp_path / "generated_icons")
    )


def test_dark_palette_renders_succesfully(monkeypatch, tmp_path):
    _use_temp_icons_dir(monkeypatch, tmp_path)
    result = theme.render_stylesheet(theme.DARK_PALETTE)
    assert isinstance(result, str)
    assert len(result) > 0


def test_light_palette_renders_succesfully(monkeypatch, tmp_path):
    _use_temp_icons_dir(monkeypatch, tmp_path)
    result = theme.render_stylesheet(theme.LIGHT_PALETTE)
    assert isinstance(result, str)
    assert len(result) > 0


def test_missing_palette_key_raises_clear_error(monkeypatch, tmp_path):
    _use_temp_icons_dir(monkeypatch, tmp_path)
    incomplete_palette = dict(theme.DARK_PALETTE)
    del incomplete_palette["accent"]  # remove one key to induce an error

    try:
        theme.render_stylesheet(incomplete_palette)
        assert False, "expected a KeyError for the missing 'accent' key"
    except KeyError as e:
        assert "accent" in str(e)


def test_no_unresolved_placeholders_remain_in_output(monkeypatch, tmp_path):
    _use_temp_icons_dir(monkeypatch, tmp_path)
    result = theme.render_stylesheet(theme.DARK_PALETTE)
    assert "${" not in result
