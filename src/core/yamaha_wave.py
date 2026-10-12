"""The Yamaha A4000/A5000 WAVE DATA bulk dump ("WD") - a wave object's audio - as pure functions, no Qt/MIDI.

A sample's audio lives in WAVE objects: the sample links a left one (SP payload @64) and, if stereo, a right one
(@80). Besides Sample Dump Standard the unit serves a wave object by its OWN name as a Yamaha bulk dump (owner's
manual "MIDI Data Format", "1.1.4 Wave Data Bulk Dump"). MEASURED on a real A4000 (2026-10-06, `tools/a4000_wave_probe.py`,
verified byte for byte against SDS dumps of the same samples):

 - a long wave arrives as SEVERAL COMPLETE bulk messages (each its own F0..F7 with the 26-byte header, ~4 KB; 4070
   data bytes in the first, 4068 in the rest) - NOT one message with several blocks like the object list;
 - each message's `data` starts with a 2-byte BLOCK NUMBER (00 00, 00 01, ...);
 - the first message then holds the [Common] block, whose UL at data[22:26] is the WORD COUNT = the sample's frames
   + 4 guard words (4000 for a sample whose `wave_length` is 3996; 132 for the 128-frame sine);
 - the audio words are 16-bit big-endian signed: frame k is the word at data[75 + 2k] of the first message, the
   stream continuing at data[2:] of each later message; the last 4 words are a loop guard (a copy of the wave's first
   frames), not audio. (The manual puts the words at payload offset 72, i.e. data[74] after the block number;
   measured is data[75]. Trust the measurement.)

`WaveAssembler` joins the messages as they arrive and reports the frames each one added (so a UI can draw the
wave progressively); `build_wave_messages` makes the same messages for the fake unit and the tests.
"""

import struct

from core import yamaha_sysex as ysx

BLOCK_NUMBER_BYTES = 2
SIZE_OFFSET = 22  # UL, in the first message's data: words in the wave including the guard
AUDIO_OFFSET = 75  # in the first message's data
GUARD_WORDS = 4
#: real data bytes in the first message / each later message (measured; a 4096-byte MIDI span minus the 26-byte header, nibbled)
FIRST_MESSAGE_DATA = 2035
LATER_MESSAGE_DATA = 2034


class WaveAssembler:
    """Collects one wave object's messages. `feed(dump)` -> the NEW frames that message completed (a list of ints),
    or raises `ysx.YamahaSysexError` for a message that isn't the next block of this wave."""

    def __init__(self, name):
        self.name = name.rstrip()
        self.blocks = 0
        self.total_words = None  # known after the first message
        self._words = []
        self._carry = b""  # a lone byte left over when a message ends mid-word

    @property
    def total_frames(self):
        """Frames the wave will hold once complete (None before the first message)."""
        return None if self.total_words is None else max(self.total_words - GUARD_WORDS, 0)

    @property
    def frames(self):
        """The frames received so far (never the guard words)."""
        return self._words[: self.total_frames] if self.total_words is not None else []

    @property
    def done(self):
        return self.total_words is not None and len(self._words) >= self.total_words

    @property
    def started(self):
        return self.blocks > 0

    def feed(self, dump):
        if dump.fmt != "WD" or dump.name != self.name:
            raise ysx.YamahaSysexError(f"not a message of wave {self.name!r}")
        data = bytes(dump.data)
        if len(data) < BLOCK_NUMBER_BYTES or int.from_bytes(data[:BLOCK_NUMBER_BYTES], "big") != self.blocks:
            raise ysx.YamahaSysexError(f"wave {self.name!r}: expected block {self.blocks}")
        if self.blocks == 0:
            if len(data) < AUDIO_OFFSET:
                raise ysx.YamahaSysexError(f"wave {self.name!r}: first message too short")
            self.total_words = int.from_bytes(data[SIZE_OFFSET : SIZE_OFFSET + 4], "big")
            audio = data[AUDIO_OFFSET:]
        else:
            audio = data[BLOCK_NUMBER_BYTES:]
        self.blocks += 1
        before = len(self._words)
        audio = self._carry + audio
        usable = len(audio) - len(audio) % 2
        self._carry = audio[usable:]
        self._words.extend(struct.unpack(f">{usable // 2}h", audio[:usable]))
        # never hand out more than the wave holds, and never the guard
        limit = self.total_frames
        return self._words[before : min(len(self._words), limit)] if before < limit else []


def build_wave_messages(device, name, frames, *, header=ysx.HEADER_A4000):
    """The messages a unit sends for the wave object `name` holding `frames` (a list of int16): see the module
    docstring. The [Common] block is the minimum the format needs (type, name, word count) - the rest is zeros."""
    name_raw = ysx.pad_name(name)
    words = list(frames) + (list(frames[:GUARD_WORDS]) + [0] * GUARD_WORDS)[:GUARD_WORDS]
    audio = struct.pack(f">{len(words)}h", *words)
    head = bytearray(AUDIO_OFFSET)  # data[0:2] is the block number, 0
    head[2] = ysx.OBJECT_TYPES["wave"]
    head[4:20] = name_raw
    head[SIZE_OFFSET : SIZE_OFFSET + 4] = len(words).to_bytes(4, "big")
    first_audio = FIRST_MESSAGE_DATA - AUDIO_OFFSET
    later_audio = LATER_MESSAGE_DATA - BLOCK_NUMBER_BYTES
    datas = [bytes(head) + audio[:first_audio]]
    for block, i in enumerate(range(first_audio, len(audio), later_audio), start=1):
        datas.append(block.to_bytes(BLOCK_NUMBER_BYTES, "big") + audio[i : i + later_audio])
    return [ysx.build_bulk_dump(device, "WD", name_raw, data, header=header) for data in datas]
