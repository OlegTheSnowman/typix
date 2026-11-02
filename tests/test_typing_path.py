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

    def updateSelection(self):
        self.field.selection = (self.start, self.end)

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

    def __init__(self, text, selection=None):
        self.text = text
        self.selection = selection or (len(text), len(text))
        self.queued = []

    def makeTextInfo(self, position):
        if position == "selection":
            return FakeInfo(self, *self.selection)
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
        "keyboardHandler", "ui", "winUser", "languageHandler", "core", "config", "config.configFlags",
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


class TestSentenceStart(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._expansions = {}
        self.plugin._capitalize_i = False
        self.plugin._capitalize_sentences = True
        self.plugin._spellcheck_enabled = False

    def replacement(self, text, buffered):
        return self.plugin._finished_word(FakeField(text), buffered)[2]

    def test_first_word_of_the_field(self):
        self.assertEqual(self.replacement("this ", "this"), "This")

    def test_first_word_when_the_ending_character_is_not_in_the_field_yet(self):
        self.assertEqual(self.replacement("this", "this"), "This")

    def test_word_after_a_full_stop(self):
        self.assertEqual(self.replacement("Done. this ", "this"), "This")

    def test_word_in_the_middle_of_a_sentence_stays(self):
        self.assertIsNone(self.replacement("This is ", "is"))

    def test_first_word_on_a_line_after_a_finished_sentence(self):
        self.assertEqual(self.replacement("Done.\nthis ", "this"), "This")

    def test_first_word_on_a_line_after_an_unfinished_one_stays(self):
        self.assertIsNone(self.replacement("and so\nthis ", "this"))

    def test_first_word_after_a_blank_line(self):
        self.assertEqual(self.replacement("and so\n\nthis ", "this"), "This")


class TestReplace(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._terminated = False
        self.plugin._categories_cfg = {"REPLACED": False}
        self.plugin._own_chars = plugin_module.deque()
        self.plugin._own_deadline = 0.0
        self.plugin._last_misspelling = None
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

    def replace(self, text, word="this", new="This", quiet=True):
        self.use(text)
        self.plugin._replace(self.field, word, new, 0, quiet)
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

    def test_a_rule_leaves_a_word_ended_with_enter_alone(self):
        self.replace("this\r\n")
        self.send_input.assert_not_called()
        self.assertEqual(self.field.text, "this\r\n")
        self.assertEqual(self.field.selection[0], self.field.selection[1])

    def test_a_rule_never_types_text_holding_a_line_break(self):
        self.replace("sig ", "sig", "Regards,\nOleg")
        self.send_input.assert_not_called()

    def test_a_fix_across_a_line_break_types_only_the_word_and_restores_the_caret(self):
        self.replace("teh\r\nmore", "teh", "the", quiet=False)
        self.assertEqual(self.field.text, "the\r\nmore")
        self.assertEqual(self.field.selection, (len(self.field.text),) * 2)
        self.assertEqual(self.message(), "the")

    def test_failed_typing_does_not_leave_the_word_selected(self):
        self.use("this ")
        self.send_input.side_effect = OSError
        self.plugin._replace(self.field, "this", "This", 0, False)
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


class TestWordCount(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.GlobalPlugin.__new__(plugin_module.GlobalPlugin)
        self.plugin._own_chars = plugin_module.deque()
        self.plugin._own_deadline = 0.0
        self.plugin._apostrophe_word = None
        self.plugin._expansions = {}
        self.plugin._capitalize_i = False
        self.plugin._capitalize_sentences = False
        self.plugin._spellcheck_enabled = False
        self.plugin._enabled = False
        self.plugin._categories_cfg = {"MISSPELLED": False}
        self.plugin._stats = mock.MagicMock()
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
        self.plugin._capitalize_sentences = True
        plugin_module.core.callLater.reset_mock(side_effect=True)
        self.type("\r")
        plugin_module.core.callLater.assert_not_called()

    def test_space_still_starts_a_replacement(self):
        self.plugin._capitalize_sentences = True
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


class TestLinesBeforeCaret(unittest.TestCase):
    def test_second_place_is_really_the_line_above(self):
        places = [p.text for p in plugin_module._lines_before_caret(FakeField("one\ntwo three"))]
        self.assertEqual(places, ["two three", "one\n"])

    def test_top_line_has_no_line_above(self):
        places = [p.text for p in plugin_module._lines_before_caret(FakeField("one two"))]
        self.assertEqual(places, ["one two"])


if __name__ == "__main__":
    unittest.main()
