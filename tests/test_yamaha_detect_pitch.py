# tests for the Yamaha Samples tab's "Detect Pitch" button (Pitch card, beside Original key): it analyses the loaded left channel
# with core/root_note_detection and, on a yes, writes Original key + Fine tune through the ordinary guarded edit path.
# Real controller + session + window against core/demo_a4000.FakeA4000.

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core import yamaha_params as yp

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_markers_ui import put
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401  (autouse: no modal one-time warning in a test)
from test_yamaha_waveform import load, window  # noqa: F401

RATE = 44100


def tone(hz, frames=RATE // 2):
    return [round(12000 * math.sin(2 * math.pi * hz * i / RATE)) for i in range(frames)]


def loaded(window, audio, name="TONE"):
    window.fake.add_sample(name, audio=audio)
    put(window.fake, name, sampling_frequency_l=RATE, sampling_frequency_r=RATE, original_key_l=66, original_key_r=66,
        fine_tune_l=0, fine_tune_r=0, loop_mode=4)
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = load(window, name)
    assert wait_until(lambda: tab._audio_name == name)
    return tab


def answer(monkeypatch, button):
    asked = []

    def question(_parent, title, text, *a, **k):
        asked.append(text)
        return button

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def stored(window, key, name="TONE"):
    return yp.extract(yp.get("sample", key), window.fake.samples[name])


def test_the_button_sits_in_the_pitch_card_next_to_original_key(window):
    tab = loaded(window, tone(440))
    button = tab.panel.detect_pitch_button
    key_row = tab.panel.widgets["original_key_l"].parentWidget().layout()
    assert button.text() == "Detect Pitch"
    assert button.parentWidget() is tab.panel.widgets["original_key_l"].parentWidget()
    assert button.geometry().top() >= 0 and key_row is not None
    assert button.isEnabled()


def test_the_button_needs_the_audio_in_memory(window):
    window.fake.add_sample("QUIET", audio=tone(440))
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = window.samples_tab
    tab.select_sample("QUIET")
    assert wait_until(lambda: tab._selected == "QUIET" and "QUIET" in tab._cache and not tab.cards_scroll.isHidden())
    assert not tab.panel.detect_pitch_button.isEnabled()


def test_a_detected_pitch_sets_original_key_and_cancels_the_cents_with_fine_tune(window, monkeypatch):
    tab = loaded(window, tone(440 * 2 ** (20 / 1200)))  # A, 20 cents sharp
    asked = answer(monkeypatch, QMessageBox.StandardButton.Yes)
    tab.panel.detect_pitch_button.click()
    assert len(asked) == 1 and "-20" in asked[0]
    assert wait_until(lambda: stored(window, "original_key_l") == 69 and stored(window, "fine_tune_l") == -20, timeout=10)


def test_saying_no_writes_nothing(window, monkeypatch):
    tab = loaded(window, tone(440))
    answer(monkeypatch, QMessageBox.StandardButton.No)
    tab.panel.detect_pitch_button.click()
    window.samples_tab._writer.flush()
    assert stored(window, "original_key_l") == 66 and stored(window, "fine_tune_l") == 0


def test_noise_gets_a_plain_acknowledgement_and_no_question(window, monkeypatch):
    import random

    rng = random.Random(1)
    tab = loaded(window, [rng.randint(-12000, 12000) for _ in range(RATE // 2)])
    asked = answer(monkeypatch, QMessageBox.StandardButton.Yes)
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, title, text, *a, **k: told.append(text))
    tab.panel.detect_pitch_button.click()
    assert told and not asked
    assert stored(window, "original_key_l") == 66


def test_a_stereo_sample_is_analysed_from_its_left_channel(window, monkeypatch):
    window.fake.add_sample("ST", audio=tone(440), audio_right=tone(220))
    put(window.fake, "ST", sampling_frequency_l=RATE, original_key_l=66, fine_tune_l=0, loop_mode=4)
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = load(window, "ST")
    assert wait_until(lambda: tab._audio_name == "ST" and tab._audio_samples_right is not None)
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    tab.panel.detect_pitch_button.click()
    assert wait_until(lambda: stored(window, "original_key_l", "ST") == 69, timeout=10)
