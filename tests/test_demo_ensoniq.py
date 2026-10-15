# tests for core/demo_ensoniq.py - the fake Ensoniq EPS/ASR. It is built from the same reading of the spec as the codec, so these only
# pin down what the engine tests rely on: the GET conversation of the spec's section 8, the status codes, one conversation at a time.

from core import ensoniq_sysex as e
from core.demo_ensoniq import EOX, SOX, FakeEnsoniq, make_wavesample


def talk(fake, message):
    fake.out.send_message([SOX, *message, EOX])


def drain(fake):
    out = []
    while (got := fake.inp.get_message()) is not None:
        out.append(got[0][1:-1])
    return out


def ack(fake):
    talk(fake, e.build_response(e.STATUS_ACK))


def test_get_instrument_conversation_follows_section_8():
    fake = FakeEnsoniq()
    talk(fake, e.build_get_instrument(0))
    # RESPONSE(ACK), then the PUT's first message
    first_two = drain(fake)
    assert e.parse_response(e.parse_message(first_two[0])) == e.STATUS_ACK
    put = e.parse_message(first_two[1])
    assert put.command == e.CMD_PUT_INSTRUMENT
    assert e.decode_context(put.body)[0] == 0
    assert drain(fake) == []  # nothing more until the client ACKs the first part
    ack(fake)
    (data,) = drain(fake)
    block = e.parse_block_message(data, e.INSTRUMENT_BLOCK_WORDS)
    assert e.instrument_name(block) == "DRUMS"
    ack(fake)
    assert drain(fake) == [] and fake.data_messages_delivered == 1


def test_an_absent_instrument_layer_and_wavesample_answer_with_their_status():
    fake = FakeEnsoniq()
    for message, status in [
        (e.build_get_instrument(1), e.STATUS_INVALID_INSTRUMENT),
        (e.build_get_layer(0, 3), e.STATUS_INVALID_LAYER),
        (e.build_get_wavesample_parameters(0, 0, 99), e.STATUS_INVALID_WAVESAMPLE),
    ]:
        talk(fake, message)
        (reply,) = drain(fake)
        assert e.parse_response(e.parse_message(reply)) == status


def test_layer_block_key_map_names_the_wavesamples():
    fake = FakeEnsoniq()
    block = fake.layer_block(fake.instruments[0]["layers"][0])
    assert e.decode_name(block[:12]) == "KIT"
    assert {v for v in block[19:107] if v} == {1, 2}


def test_wavesample_data_range_is_exclusive_by_default():
    fake = FakeEnsoniq()
    talk(fake, e.build_get_wavesample_data(0, 0, 1, 10, 20))
    drain(fake)
    ack(fake)
    (data,) = drain(fake)
    assert e.parse_data_message(data) == fake.instruments[0]["layers"][0]["wavesamples"][1]["samples"][10:20]


def test_an_inclusive_unit_returns_one_word_more():
    fake = FakeEnsoniq(inclusive_end=True)
    talk(fake, e.build_get_wavesample_data(0, 0, 1, 10, 20))
    drain(fake)
    ack(fake)
    (data,) = drain(fake)
    assert len(e.parse_data_message(data)) == 11


def test_a_range_past_the_data_is_status_0a():
    fake = FakeEnsoniq()
    talk(fake, e.build_get_wavesample_data(0, 0, 1, 2990, 3001))
    (reply,) = drain(fake)
    assert e.parse_response(e.parse_message(reply)) == e.STATUS_INVALID_DATA_RANGE


def test_a_copy_answers_status_11():
    fake = FakeEnsoniq(copies={(0, 0, 1)})
    talk(fake, e.build_get_wavesample_data(0, 0, 1, 0, 10))
    (reply,) = drain(fake)
    assert e.parse_response(e.parse_message(reply)) == e.STATUS_WAVESAMPLE_IS_A_COPY


def test_a_command_mid_conversation_gets_nak_and_the_conversation_survives():
    fake = FakeEnsoniq()
    talk(fake, e.build_get_instrument(0))
    drain(fake)
    talk(fake, e.build_get_instrument(2))  # not allowed: one in flight
    (reply,) = drain(fake)
    assert e.parse_response(e.parse_message(reply)) == e.STATUS_NAK and fake.naks_sent == 1
    ack(fake)
    assert len(drain(fake)) == 1  # the data message of the FIRST conversation


def test_a_nak_from_the_client_ends_the_conversation_and_nothing_is_retransmitted():
    fake = FakeEnsoniq()
    talk(fake, e.build_get_instrument(0))
    drain(fake)
    ack(fake)
    drain(fake)
    talk(fake, e.build_response(e.STATUS_NAK))
    assert drain(fake) == []
    talk(fake, e.build_get_instrument(0))  # free again
    assert drain(fake)


def test_a_message_for_another_channel_is_ignored():
    fake = FakeEnsoniq(channel=3)
    talk(fake, e.build_get_instrument(0, channel=0))
    assert drain(fake) == []
    talk(fake, e.build_get_instrument(0, channel=3))
    assert drain(fake)


def test_other_manufacturers_and_unknown_commands_are_not_answered():
    fake = FakeEnsoniq()
    talk(fake, [0x47, 0x00, 0x05])
    talk(fake, e.build_message(e.CMD_CREATE_INSTRUMENT, e.encode_context(5, 0, 1)))
    assert drain(fake) == []
    assert fake.ignored_ops == [e.CMD_CREATE_INSTRUMENT] and len(fake.rejected) == 1


def test_get_parameter_free_blocks():
    fake = FakeEnsoniq(free_blocks=1234)
    talk(fake, e.build_get_parameter(0, 0, 1, e.PAGE_SYSTEM, e.SYSTEM_ITEM_FREE_BLOCKS))
    ack_msg, put = drain(fake)
    assert e.parse_response(e.parse_message(ack_msg)) == e.STATUS_ACK
    message = e.parse_message(put)
    assert message.command == e.CMD_PUT_PARAMETER
    assert e.decode_param_value(message.body[e.CONTEXT_SIZE + 2 :]) == 1234


def test_get_parameter_unknown_number_is_status_03():
    fake = FakeEnsoniq()
    talk(fake, e.build_get_parameter(0, 0, 1, e.PAGE_MIDI, 5))
    (reply,) = drain(fake)
    assert e.parse_response(e.parse_message(reply)) == e.STATUS_INVALID_PARAMETER_NUMBER


def test_wait_option_makes_the_unit_wait_for_an_ack_before_answering():
    fake = FakeEnsoniq(wait_on_first_get=True)
    talk(fake, e.build_get_instrument(0))
    (reply,) = drain(fake)
    assert e.parse_response(e.parse_message(reply)) == e.STATUS_WAIT
    ack(fake)
    assert len(drain(fake)) == 2  # now the RESPONSE(ACK) and the PUT


def test_make_wavesample_describes_its_own_audio():
    ws = make_wavesample("TEST", frames=500, rate_code=14, key_range=(10, 20), root_key=33)
    p = ws["params"]
    assert (p.name, p.root_key, p.sample_rate_code, p.key_range) == ("TEST", 33, 14, (10, 20))
    assert (p.start, p.end, len(ws["samples"])) == (0, 500, 500)
    assert any(ws["samples"])
