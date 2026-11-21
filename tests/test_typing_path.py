"""Runs the real word-reading code from __init__.py against a stand-in for
NVDA's OffsetsTextInfo, whose move() copies NVDA's behaviour: moving by a line
from inside a line lands on the start of that same line."""
import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import _addon

UNIT_LINE, UNIT_CHARACTER = "line", "character"


class FakeInfo:
    def __init__(self, field, start, end=None):
        self.field = field
        self.start = start
        self.end = start if end is None else end

    @property
    def full(self):
        return self.field.text

    def copy(self):
        return FakeInfo(self.field, self.start, self.end)

    @property
    def isCollapsed(self):
        return self.start == self.end

    def updateSelection(self):
        if self.field.selects == "at once":
            self.field.selection = (self.start, self.end)
        elif self.field.selects == "late":
            self.field.pending = (self.start, self.end)

    def updateCaret(self):
        self.field.selection = (self.start, self.start)

    @property
    def text(self):
        return self.full[self.start:self.end]

    def _line_at(self, offset):
        start = self.full.rfind("\n", 0, offset) + 1
        stop = self.full.find("\n", offset)
        return start, len(self.full) if stop < 0 else stop + 1

    def _unit(self, unit, offset):
        return self._line_at(offset) if unit == UNIT_LINE else (offset, offset + 1)

    def expand(self, unit):
        self.start, self.end = self._unit(unit, self.start)

    def collapse(self, end=False):
        if end:
            self.start = self.end
        else:
            self.end = self.start

    def setEndPoint(self, other, which):
        if which == "startToStart":
            self.start = other.start
        elif which == "endToEnd":
            self.end = other.end

    def compareEndPoints(self, other, which):
        return self.start - other.start

    def move(self, unit, direction, endPoint=None):
        if direction == 0:
            return 0
        if endPoint == "end":
            offset = self.end
        elif endPoint == "start":
            offset = self.start
        else:
            self.collapse()
            offset = self.start
        high = len(self.full) + (1 if unit == UNIT_CHARACTER else 0)
        count = 0
        last = None
        while count != direction and (last is None or (direction > 0 and offset > last) or (direction < 0 and offset < last)) \
                and (offset < high or direction < 0) and (offset > 0 or direction > 0):
            last = offset
            if direction < 0 and offset > 0:
                offset -= 1
            start, stop = self._unit(unit, offset)
            offset = start if direction < 0 else stop
            count += 1 if direction > 0 else -1
        if endPoint == "start":
            if (direction > 0 and offset <= self.start) or (direction < 0 and offset >= self.start) or offset < 0:
                return 0
            self.start = offset
            return count
        if (direction > 0 and offset <= self.start) or (direction < 0 and offset >= self.start) \
                or offset < 0 or offset >= high:
            return 0
        self.start = self.end = offset
        return count


class FakeField:
    role = "edit"
    windowHandle = 1
    detectPossibleSelectionChange = None  # present on fields NVDA watches for selection

    def __init__(self, text, selection=None):
        self.text = text
        self.selection = selection or (len(text), len(text))
        self.queued = []
        self.selects = "at once"  # or "late", as in a browser, or "never"
        self.pending = None

    def makeTextInfo(self, position):
        if position == "selection":
            info = FakeInfo(self, *self.selection)
            if self.pending:
                self.selection, self.pending = self.pending, None
            return info
        return FakeInfo(self, self.selection[1])

    def send_input(self, inputs):
        """Typed input waits in a queue, as it does in Windows."""
        self.queued.append("".join(chr(item.ii.ki.wScan) for item in inputs[::2]))

    def process_queue(self):
        for typed in self.queued:
            start, end = self.selection
            self.text = self.text[:start] + typed + self.text[end:]
            self.selection = (start + len(typed), start + len(typed))
        self.queued = []


def load_plugin():
    """Imports __init__.py with NVDA's modules stubbed out."""
    nvda = {name: mock.MagicMock() for name in (
        "globalPluginHandler", "globalVars", "api", "controlTypes", "gui", "addonHandler", "inputCore",
        "keyboardHandler", "ui", "winUser", "languageHandler", "core", "config", "config.configFlags", "eventHandler",
        "speech", "tones", "textInfos", "wx", "characterProcessing", "nvwave", "gui.guiHelper",
        "gui.nvdaControls", "winBindings", "winBindings.user32",
    )}
    nvda["winBindings"].user32 = nvda["winBindings.user32"]
    nvda["winBindings.user32"].INPUT.side_effect = mock.MagicMock
    nvda["winBindings.user32"].KEYBDINPUT.side_effect = mock.MagicMock
    nvda["keyboardHandler"].shouldUseToUnicodeEx.return_value = False
    nvda["globalPluginHandler"].GlobalPlugin = object
    nvda["gui"].settingsDialogs.SettingsPanel = object
    nvda["wx"].Dialog = object
    nvda["textInfos"].UNIT_LINE = UNIT_LINE
    nvda["textInfos"].UNIT_CHARACTER = UNIT_CHARACTER
    nvda["textInfos"].POSITION_SELECTION = "selection"
    path = os.path.join(_addon.ADDON, "__init__.py")
    spec = importlib.util.spec_from_file_location("typix.plugin", path)
    spec.submodule_search_locations = None  # so relative imports resolve in typix
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, nvda):
        spec.loader.exec_module(module)
    return module


plugin_module = load_plugin()


class TestReplacementRules(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._expansions = {"brb": "be right back"}
        self.plugin._spellcheck_enabled = False

    def finished(self, text, buffered):
        return self.plugin._finished_word(FakeField(text), buffered)

    def test_a_shortcut_is_replaced(self):
        self.assertEqual(self.finished("ok brb ", "brb")[2], "be right back")

    def test_the_text_before_the_word_is_returned_for_finding_it_again(self):
        self.assertEqual(self.finished("ok brb ", "brb")[3], "ok ")

    def test_words_are_no_longer_capitalised(self):
        # Automatic capitals were removed: unreliable in browser editors.
        self.assertIsNone(self.finished("Done. this ", "this")[2])
        self.assertIsNone(self.finished("so i ", "i")[2])


class TestReplace(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._terminated = False
        self.plugin._categories_cfg = {"REPLACED": False}
        self.plugin._own_chars = plugin_module.deque()
        self.plugin._own_deadline = 0.0
        self.plugin._last_misspelling = None
        self.plugin._hush = None
        self.plugin._hush_serial = 0
        plugin_module.speech.setSpeechMode.reset_mock()
        plugin_module.eventHandler.queueEvent.reset_mock()
        plugin_module.eventHandler.isPendingEvents.return_value = False
        self.send_input = plugin_module.winUser.SendInput
        self.send_input.reset_mock(side_effect=True)
        plugin_module.keyboardHandler.shouldUseToUnicodeEx.return_value = False
        plugin_module.ui.message.reset_mock()
        # Any use of the clipboard fails the test.
        plugin_module.api.copyToClip.side_effect = AssertionError("clipboard used")
        plugin_module.api.getClipData.side_effect = AssertionError("clipboard used")

        def call_later(delay, function, *args):
            self.field.process_queue()
            function(*args)
        plugin_module.core.callLater.side_effect = call_later

    def use(self, text, selection=None):
        self.field = FakeField(text, selection)
        self.send_input.side_effect = self.field.send_input
        plugin_module.api.getFocusObject.return_value = self.field
        return self.field

    def replace(self, text, word="this", new="This"):
        self.use(text)
        self.plugin._replace(self.field, word, new, 0)
        self.field.process_queue()

    def check_selection(self, text, fixes, selection=None):
        self.use(text, selection or (0, len(text)))
        state = SimpleNamespace(obj=self.field, original=self.field.makeTextInfo("selection").text, fixes=fixes)
        self.plugin._apply_selection_fixes(state)
        self.field.process_queue()

    def message(self):
        return plugin_module.ui.message.call_args[0][0]

    def test_a_word_is_typed_with_the_text_after_it(self):
        self.replace("this ")
        self.assertEqual(self.field.text, "This ")
        self.assertEqual(self.field.selection, (5, 5))
        self.send_input.assert_called_once()

    def test_text_outside_the_basic_plane_is_typed_as_surrogates(self):
        self.use("x")
        self.plugin._type("a\U0001f600")
        self.assertEqual([ord(c) for c in self.field.queued[0]], [0x61, 0xd83d, 0xde00])

    def test_a_fix_across_a_line_break_types_only_the_word_and_restores_the_caret(self):
        self.replace("teh\r\nmore", "teh", "the")
        self.assertEqual(self.field.text, "the\r\nmore")
        self.assertEqual(self.field.selection, (len(self.field.text),) * 2)
        self.assertEqual(self.message(), "the")

    def test_failed_typing_does_not_leave_the_word_selected(self):
        self.use("this ")
        self.send_input.side_effect = OSError
        self.plugin._replace(self.field, "this", "This", 0)
        start, end = self.field.selection
        self.assertEqual(start, end)
        self.assertIn("safely", self.message())

    def test_selection_check_fixes_each_word_and_ends_after_the_text(self):
        self.check_selection("helo wrld ok", [(0, "helo", "hello"), (5, "wrld", "world")])
        self.assertEqual(self.field.text, "hello world ok")
        self.assertEqual(self.field.selection, (14, 14))
        self.assertEqual(self.send_input.call_count, 2)
        self.assertEqual(self.message(), "Corrected")

    def test_selection_check_across_lines_and_inside_the_text(self):
        self.check_selection("before helo\r\nwrld ok after", [(0, "helo", "hello"), (6, "wrld", "world")], (7, 21))
        self.assertEqual(self.field.text, "before hello\r\nworld ok after")
        self.assertEqual(self.field.selection, (23, 23))

    def test_selection_check_stops_if_the_selection_changed(self):
        self.use("helo", (0, 4))
        state = SimpleNamespace(obj=self.field, original="wrld", fixes=[(0, "wrld", "world")])
        self.plugin._apply_selection_fixes(state)
        self.send_input.assert_not_called()
        self.assertIn("changed", self.message())

    def test_selection_check_types_nothing_if_the_last_word_is_not_there(self):
        self.check_selection("helo ok", [(0, "helo", "hello"), (5, "wrld", "world")])
        self.send_input.assert_not_called()
        self.assertIn("safely", self.message())

    def test_selection_check_stops_at_a_word_that_is_not_there(self):
        self.check_selection("helo ok", [(0, "helo", "hello"), (2, "wrld", "world"), (5, "ok", "OK")])
        self.assertEqual(self.field.text, "hello ok")
        self.assertIn("safely", self.message())

    def test_replacing_never_mutes_nvda(self):
        self.replace("this ")
        self.replace("teh\r\nmore", "teh", "the")
        self.check_selection("helo ok", [(0, "helo", "hello")])
        plugin_module.speech.setSpeechMode.assert_not_called()

    def test_nvda_is_told_the_selection_before_the_hold_ends(self):
        self.replace("this ")
        self.assertIsNone(self.plugin._hush)
        self.assertEqual(self.field._lastSelectionPos.start, 5)
        self.assertFalse(self.field.hasContentChangedSinceLastSelection)
        plugin_module.eventHandler.queueEvent.assert_called_with("caret", self.field)

    def test_a_failed_replace_ends_the_hold(self):
        self.use("this ")
        self.send_input.side_effect = OSError
        self.plugin._replace(self.field, "this", "This", 0)
        self.assertIsNone(self.plugin._hush)

    def test_caret_events_held_for_the_field_only_and_not_for_long(self):
        self.use("this ")
        hold = plugin_module.time.monotonic() + 1
        self.plugin._hush = (self.field, hold)
        held = mock.MagicMock()
        self.plugin.event_caret(self.field, held)
        held.assert_not_called()
        other = FakeField("x")
        other.windowHandle = 2
        passed = mock.MagicMock()
        self.plugin.event_caret(other, passed)
        passed.assert_called_once()
        self.plugin._hush = (self.field, 0.0)
        late = mock.MagicMock()
        self.plugin.event_caret(self.field, late)
        late.assert_called_once()
        self.assertIsNone(self.plugin._hush)

    def test_a_selection_that_shows_up_late_still_gets_replaced(self):
        self.use("this ")
        self.field.selects = "late"
        self.plugin._replace(self.field, "this", "This", 0)
        self.field.process_queue()
        self.assertEqual(self.field.text, "This ")
        self.assertEqual(self.field.selection, (5, 5))

    def test_a_selection_that_never_shows_up_types_nothing(self):
        self.use("this ")
        self.field.selects = "never"
        self.plugin._replace(self.field, "this", "This", 0)
        self.send_input.assert_not_called()
        self.assertEqual(self.field.text, "this ")
        self.assertIn("safely", self.message())

    def test_a_late_caret_event_on_a_new_focus_object_finds_the_selection_current(self):
        self.use("this ")
        newer = FakeField("This ", (0, 4))
        newer._lastSelectionPos = newer.makeTextInfo("selection")
        plugin_module.api.getFocusObject.return_value = newer
        plugin_module.core.callLater.side_effect = None
        self.plugin._hold_selection_speech(self.field)
        self.plugin._finish_swap(self.field)
        newer.selection = (5, 5)
        held = mock.MagicMock()
        self.plugin.event_caret(newer, held)
        held.assert_not_called()
        self.assertTrue(newer._lastSelectionPos.isCollapsed)
        release = plugin_module.core.callLater.call_args[0]
        release[1](*release[2:])
        self.assertIsNone(self.plugin._hush)
        plugin_module.eventHandler.queueEvent.assert_called_with("caret", self.field)

    def test_an_old_release_leaves_a_newer_hold_alone(self):
        self.use("this ")
        plugin_module.core.callLater.side_effect = None
        self.plugin._hold_selection_speech(self.field)
        self.plugin._finish_swap(self.field)
        release = plugin_module.core.callLater.call_args[0]
        self.plugin._hold_selection_speech(self.field)
        release[1](*release[2:])
        self.assertIsNotNone(self.plugin._hush)

    def test_the_hold_waits_for_pending_caret_events(self):
        self.use("this ")
        plugin_module.core.callLater.side_effect = None
        plugin_module.eventHandler.isPendingEvents.return_value = True
        self.plugin._hold_selection_speech(self.field)
        self.plugin._release_hold(self.field, self.plugin._hush_serial)
        self.assertIsNotNone(self.plugin._hush)
        self.plugin._release_hold(self.field, self.plugin._hush_serial, 0)
        self.assertIsNone(self.plugin._hush)

    def test_a_slow_swap_keeps_the_hold(self):
        self.use("this ", (0, 4))
        plugin_module.core.callLater.side_effect = None
        self.plugin._hush = (self.field, 0.0)
        self.plugin._when_landed(self.field, None, mock.MagicMock())
        self.assertGreater(self.plugin._hush[1], plugin_module.time.monotonic())

    def test_own_characters_are_swallowed_once_each(self):
        self.replace("this ")
        for ch in "This ":
            self.assertTrue(self.plugin._is_own_char(ch), ch)
        self.assertFalse(self.plugin._is_own_char("T"))

    def test_a_users_key_arriving_first_passes_through(self):
        self.replace("this ")
        self.assertFalse(self.plugin._is_own_char("x"))
        self.assertTrue(self.plugin._is_own_char("T"))

    def test_own_characters_expire(self):
        self.replace("this ")
        self.plugin._own_deadline = 0.0
        self.assertFalse(self.plugin._is_own_char("T"))
        self.assertFalse(self.plugin._own_chars)

    def test_nothing_is_expected_back_where_nvda_works_out_characters_itself(self):
        plugin_module.keyboardHandler.shouldUseToUnicodeEx.return_value = True
        self.replace("this ")
        self.assertEqual(self.field.text, "This ")
        self.assertFalse(self.plugin._own_chars)


class TestSpellingCommand(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._selection_check = None
        self.plugin._last_misspelling = SimpleNamespace(word="teh", lang="en_US")
        self.plugin._suggest = mock.MagicMock()
        self.plugin._show_stats = mock.MagicMock()
        self.field = FakeField("teh ")
        plugin_module.api.getFocusObject.return_value = self.field

    def press(self):
        self.plugin.script_checkSelectionSpelling(None)

    def finish_dialog(self, choice, cancelled):
        self.plugin._suggest.call_args[0][2](choice, cancelled)

    def test_add_to_dictionary_or_skip_does_not_offer_the_word_again(self):
        self.press()
        self.finish_dialog(None, False)
        self.press()
        self.plugin._suggest.assert_called_once()
        self.plugin._show_stats.assert_called_once()

    def test_escape_keeps_the_word_for_another_try(self):
        self.press()
        self.finish_dialog(None, True)
        self.press()
        self.assertEqual(self.plugin._suggest.call_count, 2)
        self.plugin._show_stats.assert_not_called()


class TestWordCount(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._own_chars = plugin_module.deque()
        self.plugin._own_deadline = 0.0
        self.plugin._apostrophe_word = None
        self.plugin._expansions = {}
        self.plugin._spellcheck_enabled = False
        self.plugin._enabled = False
        self.plugin._categories_cfg = {"MISSPELLED": False}
        self.plugin._stats = mock.MagicMock()
        self.plugin._init_auto_replace()
        plugin_module.api.isTypingProtected.return_value = False
        plugin_module.speech.speech._curWordChars = list("test")
        self.field = FakeField("test")
        self.field.states = {plugin_module.controlTypes.State.EDITABLE}

    def type(self, ch):
        self.plugin.event_typedCharacter(self.field, mock.MagicMock(), ch)

    def test_enter_finishes_a_word_without_counting_as_a_character(self):
        self.type("\r")
        self.plugin._stats.word.assert_called_once()
        self.plugin._stats.key.assert_not_called()

    def test_enter_never_starts_a_replacement(self):
        self.plugin._expansions = {"test": "a test"}
        plugin_module.core.callLater.reset_mock(side_effect=True)
        self.type("\r")
        plugin_module.core.callLater.assert_not_called()

    def test_space_still_starts_a_replacement(self):
        self.plugin._expansions = {"test": "a test"}
        plugin_module.core.callLater.reset_mock(side_effect=True)
        self.type(" ")
        plugin_module.core.callLater.assert_called_once()

    def test_enter_on_an_empty_line_finishes_nothing(self):
        plugin_module.speech.speech._curWordChars = []
        self.type("\r")
        self.plugin._stats.word.assert_not_called()

    def test_backspace_does_not_finish_a_word(self):
        self.type("\x08")
        self.plugin._stats.word.assert_not_called()
        self.plugin._stats.key.assert_not_called()

    def test_space_still_counts_as_a_character_and_a_word(self):
        self.type(" ")
        self.plugin._stats.key.assert_called_once()
        self.assertTrue(self.plugin._stats.key.call_args[1]["word_ended"])
        self.plugin._stats.word.assert_not_called()


class TestAutoReplace(unittest.TestCase):
    """Automatic replacements select a word and type over it. A key typed
    in between used to land on the selection and wipe out text on
    claude.ai ("this is a test" became "s a test"), so they wait for a
    pause, hold keys, type only the words and put the caret back."""

    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._terminated = False
        self.plugin._init_auto_replace()
        self.plugin._own_chars = plugin_module.deque()
        self.plugin._own_deadline = 0.0
        self.plugin._hush = None
        self.plugin._hush_serial = 0
        self.plugin._alert = mock.MagicMock()
        plugin_module.eventHandler.isPendingEvents.return_value = False
        plugin_module.winUser.getAsyncKeyState.return_value = 0
        self.later = []
        plugin_module.core.callLater.reset_mock()
        plugin_module.core.callLater.side_effect = lambda delay, f, *a: self.later.append((delay, f, a))
        self.typed = []
        self.replayed = []
        self.send_input = plugin_module.winUser.SendInput
        self.send_input.reset_mock(side_effect=True)

        def send_input(inputs):
            if isinstance(inputs[0].ii.ki.wVk, int):
                self.replayed.append([(i.ii.ki.wVk, i.ii.ki.dwFlags) for i in inputs])
            else:
                self.typed.append("".join(chr(i.ii.ki.wScan) for i in inputs[::2]))
                self.field.send_input(inputs)
        self.send_input.side_effect = send_input

    def use(self, text, caret=None):
        caret = len(text) if caret is None else caret
        self.field = FakeField(text, (caret, caret))
        plugin_module.api.getFocusObject.return_value = self.field
        return self.field

    def step(self):
        """Runs what is due now; the hold's 2 second timer is left alone."""
        self.field.process_queue()
        due = [item for item in self.later if item[0] < 1000]
        self.later = [item for item in self.later if item[0] >= 1000]
        for _delay, f, args in due:
            f(*args)
        return bool(due)

    def run_all(self):
        for _ in range(200):
            if not self.step():
                break
        self.field.process_queue()

    def queue(self, word, replacement, prefix):
        self.plugin._queue_auto_replace(self.field, word, replacement, prefix)

    def test_waits_while_keys_are_still_coming(self):
        self.use("this ")
        self.plugin._last_key_time = plugin_module.time.monotonic()
        self.queue("this", "This", "")
        self.step()
        self.assertEqual(self.field.text, "this ")
        self.assertTrue(self.later)

    def test_waits_while_a_modifier_is_down(self):
        self.use("this ")
        plugin_module.winUser.getAsyncKeyState.return_value = 0x8000
        self.queue("this", "This", "")
        self.step()
        self.step()
        self.assertEqual(self.field.text, "this ")
        self.assertFalse(self.plugin._holding)

    def test_the_stretch_to_the_caret_is_typed_once(self):
        # Typing only the word and moving the caret back lost the rest of
        # the line on claude.ai in Firefox.
        self.use("this is a test ")
        self.queue("this", "This", "")
        self.run_all()
        self.assertEqual(self.field.text, "This is a test ")
        self.assertEqual(self.field.selection, (15, 15))
        self.assertEqual(self.typed, ["This is a test "])

    def test_the_caret_distance_is_read_not_stepped(self):
        # Stepping character by character froze NVDA for seconds in Firefox.
        self.use("this is a test ")
        self.queue("this", "This", "")
        with mock.patch.object(plugin_module, "_distance", side_effect=AssertionError("stepped")):
            self.run_all()
        self.assertEqual(self.field.text, "This is a test ")
        self.assertEqual(self.field.selection, (15, 15))

    def test_two_words_waiting_are_both_replaced(self):
        self.use("i think i am ")
        self.queue("i", "I", "")
        self.queue("i", "I", "i think ")
        self.run_all()
        self.assertEqual(self.field.text, "I think I am ")
        self.assertEqual(self.field.selection, (13, 13))
        self.assertEqual(self.typed, ["I think I am "])

    def test_replacements_of_another_length_keep_the_caret_right(self):
        self.use("brb so i went ")
        self.queue("brb", "be right back", "")
        self.queue("i", "I", "brb so ")
        self.run_all()
        self.assertEqual(self.field.text, "be right back so I went ")
        self.assertEqual(self.field.selection, (24, 24))

    def test_the_caret_goes_back_inside_the_text(self):
        self.use("this is more", caret=8)
        self.queue("this", "This", "")
        self.run_all()
        self.assertEqual(self.field.text, "This is more")
        self.assertEqual(self.field.selection, (8, 8))

    def test_keys_during_the_swap_are_held_and_replayed_in_order(self):
        self.use("this is ")
        self.field.selects = "late"
        self.queue("this", "This", "")
        self.step()
        self.assertTrue(self.plugin._holding)
        self.assertFalse(self.plugin._on_raw_key(vkCode=0x41, scanCode=30, extended=False, pressed=True))
        self.assertFalse(self.plugin._on_raw_key(vkCode=0x41, scanCode=30, extended=False, pressed=False))
        self.assertEqual(self.replayed, [])
        self.run_all()
        self.assertEqual(self.field.text, "This is ")
        self.assertEqual(len(self.replayed), 1)
        self.assertEqual([vk for vk, _flags in self.replayed[0]], [0x41, 0x41])
        # Once replayed, keys pass straight through again.
        self.assertTrue(self.plugin._on_raw_key(vkCode=0x41, scanCode=30, extended=False, pressed=True))

    def test_a_shortcut_is_not_a_key_on_its_way(self):
        # Ctrl+S never arrives as a character; counting it made every later
        # replacement wait for the stale count to expire.
        self.plugin._on_raw_key(vkCode=0xA2, scanCode=29, pressed=True)
        self.plugin._on_raw_key(vkCode=0x53, scanCode=31, pressed=True)
        self.plugin._on_raw_key(vkCode=0x53, scanCode=31, pressed=False)
        self.plugin._on_raw_key(vkCode=0xA2, scanCode=29, pressed=False)
        self.assertEqual(self.plugin._keys_in_flight(), 0)

    def test_waits_for_keys_already_let_through(self):
        self.use("this ")
        self.plugin._on_raw_key(vkCode=0x41, scanCode=30, pressed=True)
        self.plugin._last_key_time -= 0.4  # pressed a while ago, still not arrived
        self.queue("this", "This", "")
        self.step()
        self.step()
        self.assertEqual(self.field.text, "this ")
        self.assertFalse(self.plugin._holding)  # waiting holds no keys
        with self.plugin._key_lock:
            self.plugin._keys_arrived += 1
        self.run_all()
        self.assertEqual(self.field.text, "This ")

    def test_typixs_own_unicode_input_is_never_held(self):
        self.use("")
        self.plugin._hold_keys()
        self.assertTrue(self.plugin._on_raw_key(vkCode=plugin_module._VK_PACKET, scanCode=0, pressed=True))

    def test_keys_pass_when_nothing_is_being_replaced(self):
        self.use("")
        self.assertTrue(self.plugin._on_raw_key(vkCode=0x41, scanCode=30, pressed=True))

    def test_a_hold_that_outlives_its_timer_gives_the_keys_back(self):
        self.use("")
        self.plugin._auto_busy = True
        serial = self.plugin._hold_keys()
        self.plugin._on_raw_key(vkCode=0x41, scanCode=30, pressed=True)
        self.plugin._hold_deadline = 0.0
        self.plugin._hold_expired(serial)
        self.assertFalse(self.plugin._holding)
        self.assertFalse(self.plugin._auto_busy)
        self.assertEqual(len(self.replayed), 1)

    def test_a_word_that_cannot_be_selected_is_left_alone_and_the_keys_come_back(self):
        self.use("this is ")
        self.field.selects = "never"
        real_await = self.plugin._await_selection

        def key_while_selecting(obj, expected):
            self.plugin._on_raw_key(vkCode=0x41, scanCode=30, pressed=True)
            real_await(obj, expected)
        self.plugin._await_selection = key_while_selecting
        self.queue("this", "This", "")
        self.run_all()
        self.assertEqual(self.field.text, "this is ")
        self.assertEqual(self.field.selection, (8, 8))
        self.assertFalse(self.plugin._auto_busy)
        self.assertEqual(len(self.replayed), 1)

    def test_a_word_with_a_line_break_after_it_is_left_alone(self):
        # Enter may have sent it already, in a chat app.
        self.use("this\r\nmore ")
        self.queue("this", "This", "")
        self.run_all()
        self.assertEqual(self.field.text, "this\r\nmore ")
        self.assertEqual(self.typed, [])

    def test_enter_drops_the_replacements_waiting(self):
        self.use("this ")
        self.queue("this", "This", "")
        self.plugin._is_own_char = lambda ch: False
        plugin_module.api.isTypingProtected.return_value = False
        plugin_module.speech.speech._curWordChars = []
        self.plugin._apostrophe_word = None
        self.plugin._spellcheck_enabled = False
        self.plugin._enabled = False
        self.plugin._stats = mock.MagicMock()
        self.plugin.event_typedCharacter(self.field, mock.MagicMock(), "\r")
        self.run_all()
        self.assertEqual(self.field.text, "this ")

    def test_a_replacement_holding_a_line_break_is_never_typed(self):
        self.use("sig ")
        self.queue("sig", "Regards,\nOleg", "")
        self.run_all()
        self.assertEqual(self.field.text, "sig ")

    def test_keys_come_back_only_once_nothing_is_selected(self):
        self.use("this is ")
        self.field.selection = (0, 4)
        states = []
        send = self.send_input.side_effect

        def record(inputs):
            states.append(self.field.selection[0] == self.field.selection[1])
            send(inputs)
        self.send_input.side_effect = record
        self.plugin._auto_busy = True
        self.plugin._hold_keys()
        self.plugin._on_raw_key(vkCode=0x41, scanCode=30, pressed=True)
        self.plugin._end_auto()
        self.run_all()
        self.assertEqual(states, [True])

    def test_a_slow_swap_renews_the_hold(self):
        self.use("")
        self.plugin._auto_busy = True
        serial = self.plugin._hold_keys()
        self.plugin._hold_expired(serial)  # its deadline hasn't passed
        self.assertTrue(self.plugin._holding)

    def test_a_word_no_longer_there_is_skipped(self):
        self.use("so this ")
        self.queue("this", "This", "")
        self.run_all()
        self.assertEqual(self.field.text, "so this ")
        self.assertFalse(self.plugin._holding)


class TestFindWordPrefix(unittest.TestCase):
    def test_the_occurrence_with_the_right_text_before_it(self):
        field = FakeField("this is this")
        found = plugin_module.GlobalPlugin._find_word(field, "this", "")
        self.assertEqual((found.start, found.end), (0, 4))

    def test_no_occurrence_with_that_prefix_finds_nothing(self):
        field = FakeField("so this")
        self.assertIsNone(plugin_module.GlobalPlugin._find_word(field, "this", ""))


class TestLinesBeforeCaret(unittest.TestCase):
    def test_second_place_is_really_the_line_above(self):
        places = [p.text for p in plugin_module._lines_before_caret(FakeField("one\ntwo three"))]
        self.assertEqual(places, ["two three", "one\n"])

    def test_top_line_has_no_line_above(self):
        places = [p.text for p in plugin_module._lines_before_caret(FakeField("one two"))]
        self.assertEqual(places, ["one two"])


if __name__ == "__main__":
    unittest.main()
