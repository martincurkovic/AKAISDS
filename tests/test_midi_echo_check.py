# the pure parts of tools/midi_echo_check.py: classifying what arrives on the input, and the verdict

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import s2000_misc_probe  # noqa: F401  (the tool imports it)
import midi_echo_check as echo
import s3k.messages as m


def stat():
    return list(m.build_frame(m.Command.STAT, [0] * 10))


def ok():
    return list(m.Reply(code=0).encode())


def err():
    return list(m.Reply(code=1).encode())


def test_frames_are_classified():
    assert echo.classify_raw(stat()) == "STAT (the answer)"
    assert echo.classify_raw(ok()) == "REPLY OK (stray)"
    assert echo.classify_raw(err()) == "REPLY ERROR (stray)"
    assert echo.classify_raw([0xF8]) == "MIDI clock (0xF8)"
    assert echo.classify_raw([0xFE]) == "active sensing (0xFE)"
    assert echo.classify_raw([0x90, 60, 100]).startswith("channel message")
    assert echo.classify_raw([0xF0, 0x7F, 0x7F, 0x06, 0x02, 0xF7]) == "SysEx (not an Akai frame)"
    assert echo.classify_raw(list(m.build_frame(m.Command.MISCDATA, [0] * 8))).endswith("(stray)")


def frames(*raws):
    return [(echo.classify_raw(r), r) for r in raws]


def test_a_clean_run_is_clean():
    per_request = [frames(stat()) for _ in range(echo.REQUESTS)]
    text, clean = echo.verdict([], per_request)
    assert clean and text.startswith("clean")


def test_a_reply_after_the_stat_is_the_echo_signature():
    per_request = [frames(stat(), ok()) for _ in range(echo.REQUESTS)]
    text, clean = echo.verdict([], per_request)
    assert not clean and "ECHO" in text and "REPLY OK (stray)" in text


def test_an_error_reply_counts_as_a_stray_too():
    per_request = [frames(stat(), err())] + [frames(stat()) for _ in range(echo.REQUESTS - 1)]
    text, clean = echo.verdict([], per_request)
    assert not clean and "REPLY ERROR (stray)" in text


def test_unanswered_requests_are_reported_separately_from_echo():
    per_request = [[] for _ in range(echo.REQUESTS)]
    text, clean = echo.verdict([], per_request)
    assert not clean and "no STAT answer" in text and "ECHO" not in text


def test_idle_clock_is_reported_but_does_not_fail_a_clean_run():
    idle = frames([0xF8], [0xF8], [0xFE])
    per_request = [frames(stat()) for _ in range(echo.REQUESTS)]
    text, clean = echo.verdict(idle, per_request)
    assert clean
    assert "MIDI clock" in text and "active sensing" in text
