"""A piano keyboard that scales to its width: click or drag with the mouse, or type on the computer keyboard while it has focus. It only reports notes
(`note_on`, `note_off`); whoever owns it decides what to send, and with what velocity.

Computer keys (QWERTY, the usual DAW layout), only while this widget has the focus so typing in a text box never plays a note: A W S E D F T G Y H U J K O L P ; are the notes
from `typing_base` (C, C#, D ... E an octave up); Z and X move that base down and up an octave, C and V ask for a quieter / louder note (`velocity_step`, -1 / +1: the owner
of the value decides the size of the step), [ and ] ask for the previous / next MIDI channel (`channel_step`). The range those keys play is marked by a bar under the keyboard and a light tint on its keys. Every note that is held is released
when the widget loses focus or is disabled. (Same behaviour as JV-Studio's keyboard, minus the patch and bank keys: a sampler has no banks.)
"""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QWidget

from core.midi_notes import midi_note_to_name
from ui import theme

FIRST_NOTE = 24  # C0 in the S3000 panel's naming (C1 in Ableton's): the lowest note an Akai keygroup can cover
LAST_NOTE = 108  # C7 (C8 in Ableton's): seven octaves, 50 white keys (the sampler's own range goes up to 127; this is for auditioning)
MIN_WHITE_WIDTH = 20  # px: below this the audition keyboard scrolls sideways instead of squeezing its keys (`ScrollableKeyboard`)
TYPING_KEYS = "awsedftgyhujkolp;"  # semitones 0, 1, 2 ...
DEFAULT_TYPING_BASE = 60  # C4
BLACK = {1, 3, 6, 8, 10}
RANGE_BAR = 7  # the strip under the keys that marks the octave the computer keys play


def _white_notes(first, last):
    return [n for n in range(first, last + 1) if n % 12 not in BLACK]


class PianoKeyboard(QWidget):
    note_on = Signal(int)
    note_off = Signal(int)
    typing_base_changed = Signal(int)
    velocity_step = Signal(int)  # -1 (quieter) or +1 (louder): the C and V keys
    channel_step = Signal(int)  # -1 (previous MIDI channel) or +1 (next): the [ and ] keys
    held_changed = Signal(list)  # the notes held now, lowest first (for a live read-out)

    def __init__(self, parent=None, first=FIRST_NOTE, last=LAST_NOTE, typing=True):
        """`first`..`last` is the range drawn (it must start and end on a white key, or a black key would have no white key below it); `typing` False is a keyboard that
        the computer keys do not play (no octave bar)."""
        super().__init__(parent)
        self._first, self._last, self._typing = first, last, typing
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        height = 82 if typing else 66
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setMouseTracking(False)
        self.typing_base = DEFAULT_TYPING_BASE
        self._dimmed = set()  # keys drawn greyed (a rhythm key whose tone is off)
        self._whites = _white_notes(first, last)
        self._down = {}  # note -> how many sources hold it (mouse, typing key)
        self._mouse_note = None
        self._external = set()  # notes a MIDI input holds: drawn pressed, never emitted (they are already on their way to the sampler)
        self._typed = {}  # Qt key -> the note it started
        self._live = False  # the computer keyboard is routed here (the audition bar is open): the range bar is drawn solid

    # -- geometry --------------------------------------------------------------------------------------------------------

    def _white_width(self):
        return self.width() / len(self._whites)

    def _keys_height(self):
        return self.height() - RANGE_BAR - 2 if self._typing else self.height()

    def _typing_notes(self):
        """The notes the computer keys play now (clipped to the keyboard)."""
        if not self._typing:
            return []
        return [n for n in range(self.typing_base, self.typing_base + len(TYPING_KEYS)) if self._first <= n <= self._last]

    def _typing_span(self):
        """(left, right) in pixels of the keys the computer keys play, from the first key's left edge to the last white key's right edge."""
        notes = self._typing_notes()
        if not notes:
            return 0.0, 0.0
        last = notes[-1] if notes[-1] % 12 not in BLACK else notes[-1] - 1
        return self._key_rect(notes[0]).left(), self._key_rect(last).right()

    def _key_rect(self, note):
        white = self._white_width()
        if note % 12 not in BLACK:
            return QRectF(self._whites.index(note) * white, 0, white, self._keys_height())
        before = self._whites.index(note - 1)  # the white key just below
        width = white * 0.6
        return QRectF((before + 1) * white - width / 2, 0, width, self._keys_height() * 0.62)

    def note_at(self, x, y):
        """The note under a point: a black key wins where it overlaps a white one."""
        if not 0 <= x < self.width() or not 0 <= y < self._keys_height():
            return None
        for note in range(self._first, self._last + 1):
            if note % 12 in BLACK and self._key_rect(note).contains(x, y):
                return note
        index = int(x / self._white_width())
        return self._whites[min(index, len(self._whites) - 1)]

    # -- sounding --------------------------------------------------------------------------------------------------------

    def is_down(self, note):
        return note in self._down

    def _press(self, note):
        self._down[note] = self._down.get(note, 0) + 1
        if self._down[note] == 1:
            self.note_on.emit(note)
            self.held_changed.emit(sorted(self._down))
        self.update()

    def _release(self, note):
        count = self._down.get(note, 0)
        if count == 0:
            return
        if count == 1:
            del self._down[note]
            self.note_off.emit(note)
            self.held_changed.emit(sorted(self._down))
        else:
            self._down[note] = count - 1
        self.update()

    def _lit(self, note):
        return note in self._down or note in self._external

    def set_external_notes(self, notes):
        """Draw `notes` as held by something outside the widget (a MIDI input). Nothing is emitted."""
        notes = set(notes)
        if notes != self._external:
            self._external = notes
            self.update()

    def release_all(self):
        """Let go of everything (focus lost, disabled, Panic)."""
        held = bool(self._down)
        for note in list(self._down):
            del self._down[note]
            self.note_off.emit(note)
        if held:
            self.held_changed.emit([])
        self._mouse_note = None
        self._typed.clear()
        self.update()

    def set_dimmed(self, notes):
        self._dimmed = set(notes)
        self.update()

    def set_live(self, live):
        self._live = live
        self.update()

    def set_typing_base(self, note):
        note = max(self._first, min(self._last - 12, note))
        if note != self.typing_base:
            self.typing_base = note
            self.typing_base_changed.emit(note)
            self.update()  # (the range marker moves now, not at the next note)

    # -- mouse -----------------------------------------------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        self._mouse_note = self.note_at(event.position().x(), event.position().y())
        if self._mouse_note is not None:
            self._press(self._mouse_note)

    def mouseMoveEvent(self, event):
        if self._mouse_note is None:
            return
        note = self.note_at(event.position().x(), event.position().y())
        if note != self._mouse_note:  # a glissando: the old key up, the new one down
            self._release(self._mouse_note)
            self._mouse_note = note
            if note is not None:
                self._press(note)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._mouse_note is not None:
            self._release(self._mouse_note)
            self._mouse_note = None

    # -- computer keyboard -----------------------------------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        text = event.text().lower()
        if text == "z":
            self.set_typing_base(self.typing_base - 12)
        elif text == "x":
            self.set_typing_base(self.typing_base + 12)
        elif text == "c":
            self.velocity_step.emit(-1)
        elif text == "v":
            self.velocity_step.emit(1)
        elif text == "[":
            self.channel_step.emit(-1)
        elif text == "]":
            self.channel_step.emit(1)
        elif len(text) == 1 and text in TYPING_KEYS and event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            note = self.typing_base + TYPING_KEYS.index(text)
            if self._first <= note <= self._last and event.key() not in self._typed:
                self._typed[event.key()] = note
                self._press(note)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        note = self._typed.pop(event.key(), None)  # (released by key, so changing the octave while holding a key can't leave a note stuck)
        if note is not None:
            self._release(note)
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.release_all()
        super().focusOutEvent(event)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def changeEvent(self, event):
        if not self.isEnabled():
            self.release_all()
        super().changeEvent(event)

    # -- painting --------------------------------------------------------------------------------------------------------

    def paintEvent(self, _event):
        palette = theme.current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        edge = QPen(QColor(palette["key_edge"]))
        accent = QColor(palette["accent"])
        enabled = self.isEnabled()
        painter.setPen(edge)
        font = QFont(self.font())
        font.setPixelSize(9)
        painter.setFont(font)
        in_range = set(self._typing_notes())
        tint = QColor(accent)
        tint.setAlpha(46)
        for note in self._whites:
            rect = self._key_rect(note)
            colour = QColor(palette.get("key_down_white", palette["accent"])) if self._lit(note) else QColor(palette["key_white"])
            if not enabled or (note in self._dimmed and not self._lit(note)):
                colour = QColor(palette["bg_disabled"])
            painter.setBrush(colour)
            painter.drawRect(rect)
            if enabled and note in in_range and not self._lit(note):
                painter.setBrush(tint)
                painter.drawRect(rect)
            if note % 12 == 0:  # the C of each octave carries its name
                painter.setPen(QColor(palette["key_black"] if not self._lit(note) or "key_down_white" in palette else palette["text_on_accent"]))
                painter.drawText(rect.adjusted(2, 0, 0, -3), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, midi_note_to_name(note))
                painter.setPen(edge)
        for note in range(self._first, self._last + 1):
            if note % 12 in BLACK:
                dim = not enabled or (note in self._dimmed and not self._lit(note))
                painter.setBrush(QColor(palette.get("key_down_black", palette["accent"])) if self._lit(note) else QColor(palette["border_disabled"] if dim else palette["key_black"]))
                painter.drawRect(self._key_rect(note))
        if enabled and self._typing:  # the bar under the keys: which keys the computer keyboard plays (solid while the computer keyboard is routed here)
            left, right = self._typing_span()
            bar = QColor(accent)
            bar.setAlpha(255 if self._live or self.hasFocus() else 110)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar)
            painter.drawRect(QRectF(left, self.height() - RANGE_BAR, right - left, RANGE_BAR))
        if self.hasFocus():
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.end()


class ScrollableKeyboard(QScrollArea):
    """A `PianoKeyboard` that keeps its keys at least `MIN_WHITE_WIDTH` px wide: in a window too narrow for all of them it scrolls sideways (a trackpad swipe, the scroll bar, or the octave
    keys, which bring the octave the computer keys play into view) instead of squeezing the keys until they are hard to hit. Wider than that, it is just the keyboard, scaled to the width."""

    def __init__(self, keyboard, parent=None):
        super().__init__(parent)
        self.keyboard = keyboard
        keyboard.setMinimumWidth(len(keyboard._whites) * MIN_WHITE_WIDTH)
        self.setWidget(keyboard)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.viewport().setAutoFillBackground(False)
        self._sized_for_bar = None
        self._fit_height()
        keyboard.typing_base_changed.connect(lambda _n: self.show_typing_range())

    def _fit_height(self):
        """Room for the keyboard and, when it scrolls, the scroll bar under it (a bar that squeezed the keyboard would push the octave marker out of sight)."""
        needed = self.keyboard.minimumWidth() > self.width()
        height = self.keyboard.maximumHeight() + (self.horizontalScrollBar().sizeHint().height() + 2 if needed else 0)
        if height != self.maximumHeight():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()
        if self._sized_for_bar is None and self.width() > 1:
            self._sized_for_bar = True
            self.show_typing_range()

    def show_typing_range(self):
        """Scroll so that the octave the computer keys play is in the middle (nothing to do when everything fits)."""
        bar = self.horizontalScrollBar()
        if bar.maximum() <= 0:
            return
        left, right = self.keyboard._typing_span()
        bar.setValue(int((left + right) / 2 - self.viewport().width() / 2))
