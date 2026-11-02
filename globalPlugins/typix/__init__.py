# Typix for NVDA
# Copyright (C) 2025 OlegTheSnowman

import os
import re
import time
import shutil
import logging
import weakref
from collections import deque
from types import SimpleNamespace

import globalPluginHandler
import globalVars
import api
import controlTypes
import gui
import addonHandler
import inputCore
import keyboardHandler
import ui
import winUser
import languageHandler
import core
import config as nvda_config
import speech
import tones
import textInfos
import winBindings.user32
import wx
from config.configFlags import TypingEcho

from .audio_engine import AudioEngine
from .storage import load_json, save_json
from .typing_stats import TypingStats
from .sound_loader import CATEGORIES, SoundPackLoader, char_category
from . import config as addon_config
from . import settings as addon_settings
from . import spellcheck as addon_spellcheck

try:
    addonHandler.initTranslation()
except Exception:
    pass

logger = logging.getLogger(__name__)

# Bundled packs already copied to the user folder, so a pack the user
# deletes stays deleted instead of coming back on the next start.
_BUNDLED_MARKER = ".typix_bundled.json"

# Keys that never fire event_typedCharacter, by NVDA key name.
_SPECIAL_KEYS = {
    "backspace": "BACKSPACE",
    "delete": "DELETE",
    "enter": "ENTER",
    "numpadenter": "ENTER",
    "tab": "TAB",
}

# Control characters typed through event_typedCharacter that the gesture
# hook above already plays, so they must not play twice.
_CONTROL_CHARS = {"\x08", "\x7f", "\r", "\n", "\t", "\x1b"}
# What Enter types. It isn't a character for the statistics, but it does
# finish a word.
_LINE_ENDS = ("\r", "\n")

# NVDA's "speak typed words" setting. Typix only runs this in edit fields,
# so every value but off means speak.
_TYPED_WORDS_OFF = TypingEcho.OFF.value

# Never typed: each is a key press, and Enter sends the message in a chat app.
_NOT_TYPED = "\r\n\t"
# How long, in seconds, characters Typix typed are expected back.
_OWN_CHARS_TIMEOUT = 2.0

# "i" on its own and its contractions, for the capital I rule.
_I_FORMS = {"i", "i'm", "i'll", "i've", "i'd"}

_EDITABLE_ROLES = (
    controlTypes.Role.EDITABLETEXT,
    controlTypes.Role.TERMINAL,
    # Modern Word (UIA, Office 2016+) exposes its document as DOCUMENT.
    controlTypes.Role.DOCUMENT,
)


def _nvda_word_chars():
    """NVDA's own typed-word buffer, the one "speak typed words" uses.
    Private to NVDA, so this returns None if a future version drops it."""
    return getattr(speech.speech, "_curWordChars", None)


def _input_language(obj):
    """Locale name of the keyboard layout active in obj's thread."""
    try:
        lcid = winUser.getKeyboardLayout(obj.windowThreadID) & 0xFFFF
        return languageHandler.windowsLCIDToLocaleName(lcid)
    except Exception:
        return None


def _lines_before_caret(obj):
    """Yields where to look for the word just typed: the caret's line up
    to the caret, then the whole line above, for after Enter. Lazy, so the
    line above is only read when the caret's line has no match. Yields
    nothing if the field can't be read."""
    try:
        caret = obj.makeTextInfo(textInfos.POSITION_CARET)
        line = caret.copy()
        line.expand(textInfos.UNIT_LINE)
        before = caret.copy()
        before.setEndPoint(line, "startToStart")
    except Exception:
        return
    yield before
    try:
        # From the line's start: from inside a line, moving up a line only
        # reaches the start of that same line.
        previous = line.copy()
        previous.collapse()
        if not previous.move(textInfos.UNIT_LINE, -1):
            return
        previous.expand(textInfos.UNIT_LINE)
    except Exception:
        return
    yield previous


def _token_at(text, pos):
    """The whitespace-separated chunk of text around index pos."""
    start = pos
    while start and not text[start - 1].isspace():
        start -= 1
    end = pos
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


def _same_window(focus, obj):
    """Whether focus is back in obj's window. Never raises."""
    try:
        return focus is obj or focus.windowHandle == obj.windowHandle
    except Exception:
        return False


def _distance(start, target, limit):
    """Characters from start to target, counted in the field's own units,
    so it doesn't matter how an app counts a line break. Stops at limit."""
    position = start.copy()
    count = 0
    while count < limit and position.compareEndPoints(target, "startToStart") < 0:
        if not position.move(textInfos.UNIT_CHARACTER, 1):
            break
        count += 1
    return count


def _is_editable(obj):
    """True for a writable text field. Never raises: UIA elements can die
    mid-check with a COMError, and letting that out stalls NVDA's events."""
    if not obj:
        return False
    try:
        states = obj.states
        if controlTypes.State.READONLY in states:
            return False
        return obj.role in _EDITABLE_ROLES or controlTypes.State.EDITABLE in states
    except Exception:
        return False


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    # Translators: Category name of Typix commands in NVDA's Input gestures dialog.
    scriptCategory = _("Typix")

    def __init__(self):
        super().__init__()
        addon_config.init_config()
        self.audio_engine = AudioEngine()
        # Packs live in NVDA's config folder, which survives addon updates.
        config_path = globalVars.appArgs.configPath
        user_packs = os.path.join(config_path, "typix_packs")
        if not os.path.isdir(user_packs):
            # Packs from before the rename to Typix.
            old_packs = os.path.join(config_path, "typing_soundscape_packs")
            if os.path.isdir(old_packs):
                shutil.copytree(old_packs, user_packs)
        os.makedirs(user_packs, exist_ok=True)
        marker = os.path.join(user_packs, _BUNDLED_MARKER)
        installed = set(load_json(marker, []))
        bundled = os.path.join(os.path.dirname(__file__), "soundpacks")
        new = [n for n in os.listdir(bundled)
               if n not in installed and os.path.isdir(os.path.join(bundled, n))]
        for name in new:
            if not os.path.isdir(os.path.join(user_packs, name)):
                shutil.copytree(os.path.join(bundled, name), os.path.join(user_packs, name))
        if new:
            save_json(marker, sorted(installed.union(new)))
        self.sound_loader = SoundPackLoader(user_packs)
        self.sound_loader.scan_packs()
        addon_spellcheck.set_enchant_dictionaries_directory(config_path)

        self._terminated = False
        self._last_play = {}             # debounce key -> time in ms
        self._typed_obj_ref = None       # weakref to the last field a character was typed in
        self._selection_check = None     # state of a running selection spellcheck
        self._last_misspelling = None    # word and language for the fix command
        self._apostrophe_word = None     # NVDA's word held back at an apostrophe
        self._stats_open = False         # the statistics dialog is showing
        self._own_chars = deque()        # characters Typix typed, on their way back
        self._own_deadline = 0.0         # when to stop expecting them
        # Typing statistics, kept across sessions; counts only, never text.
        self._stats = TypingStats(os.path.join(config_path, "typix_stats.json"))
        self.update_settings()

        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(addon_settings.TypixSettingsPanel)
        inputCore.decide_executeGesture.register(self._on_gesture)
        # Each NVDA profile can have its own Typix settings.
        nvda_config.post_configProfileSwitch.register(self.update_settings)
        self._typix_menu_item = None
        try:
            tray = gui.mainFrame.sysTrayIcon
            menu = wx.Menu()
            # Translators: Item in NVDA menu, Tools, Typix that opens the custom spelling dictionary.
            item = menu.Append(wx.ID_ANY, _("Manage &Dictionary..."))
            tray.Bind(wx.EVT_MENU, self._on_manage_dictionary, item)
            # Translators: Name of the Typix submenu in NVDA menu, Tools.
            self._typix_menu_item = tray.toolsMenu.AppendSubMenu(menu, _("Typix"))
        except Exception:
            logger.exception("Typix: failed to install Tools menu entry")

    def _on_manage_dictionary(self, event):
        gui.runScriptModalDialog(addon_settings.ManageDictionaryDialog(gui.mainFrame))

    def terminate(self):
        self._terminated = True
        try:
            inputCore.decide_executeGesture.unregister(self._on_gesture)
            nvda_config.post_configProfileSwitch.unregister(self.update_settings)
        except Exception:
            pass
        try:
            gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(addon_settings.TypixSettingsPanel)
        except (ValueError, AttributeError):
            pass
        try:
            if self._typix_menu_item is not None:
                gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._typix_menu_item)
        except Exception:
            pass
        self._stats.save()
        self.audio_engine.close()
        super().terminate()

    def update_settings(self):
        """Caches everything the keystroke path reads. Called on settings save."""
        get = addon_config.get_config
        self.audio_engine.configure(get("master_volume"), get("panning_enabled"), get("panning_width"))
        self._enabled = bool(get("enabled"))
        self._current_pack = get("current_pack") or "default"
        self._debounce_ms = get("debounce_ms") or 0
        self._global_rules = addon_config.get_json("global_char_rules_json")
        self._expansions = addon_config.get_json("expansions_json")
        self._capitalize_sentences = bool(get("capitalize_sentences"))
        self._capitalize_i = bool(get("capitalize_i"))
        cats = get("categories")
        self._categories_cfg = {cat: bool(cats[cat]) for cat in CATEGORIES}
        self._spellcheck_enabled = bool(get("spellcheck_enabled"))

    def reload_packs(self):
        self.sound_loader.scan_packs()
        self.audio_engine.clear_cache()

    @staticmethod
    def _echo_words(obj):
        """Whether NVDA speaks typed words here. Terminals hold typed echo
        until the screen updates, so passwords stay hidden; they are left
        to NVDA."""
        try:
            if obj.role == controlTypes.Role.TERMINAL:
                return False
        except Exception:
            return False
        return nvda_config.conf["keyboard"]["speakTypedWords"] != _TYPED_WORDS_OFF

    def _alert(self, category, hz):
        """Plays an alert category, or a beep when the pack has no sound for it."""
        if self._category_on(category) and not self._play(category):
            tones.beep(hz, 60)

    def _category_on(self, category):
        return self._categories_cfg[category]

    # --- Scripts ---

    def script_toggleTypingSounds(self, gesture):
        new_state = not addon_config.get_config("enabled")
        addon_config.set_config("enabled", new_state)
        self.update_settings()
        if new_state:
            # Translators: Spoken when the toggle command turns typing sounds on.
            ui.message(_("Typix sounds on"))
        else:
            # Translators: Spoken when the toggle command turns typing sounds off.
            ui.message(_("Typix sounds off"))
    # Translators: Description of the toggle command in NVDA's Input gestures dialog.
    script_toggleTypingSounds.__doc__ = _("Toggles Typix typing sounds on and off")

    def script_checkSelectionSpelling(self, gesture):
        if self._selection_check is not None:
            # Translators: Spoken when the selection spelling check is started while one is already running.
            ui.message(_("A selection spelling check is already in progress"))
            return
        obj = api.getFocusObject()
        try:
            text = obj.makeTextInfo(textInfos.POSITION_SELECTION).text
        except Exception:
            text = ""
        if not text or not text.strip():
            # Nothing selected: fix the last misspelled word if it's still
            # just before the caret; otherwise, including in windows with
            # no text at all, report typing statistics.
            last = self._last_misspelling
            if last is not None and self._find_word(obj, last.word) is not None:
                self._suggest(
                    last.word, last.lang,
                    lambda choice, cancelled: choice and self._replace(obj, last.word, choice),
                )
                return
            self._last_misspelling = None
            self._show_stats()
            return
        lang = _input_language(obj)
        checker = addon_spellcheck.start_checker(text, lang)
        if checker is None:
            # Translators: Spoken when no spelling dictionary is ready yet for the current keyboard language.
            ui.message(_("Spellcheck dictionary is not available yet, try again in a moment"))
            return
        try:
            readonly = controlTypes.State.READONLY in obj.states
        except Exception:
            readonly = False
        self._selection_check = SimpleNamespace(
            obj=obj, checker=checker, errors=iter(checker), lang=lang, readonly=readonly,
            original=text, found=False, fixes=[],
        )
        self._next_selection_misspelling()
    # Translators: Description of the spelling command in NVDA's Input gestures dialog.
    script_checkSelectionSpelling.__doc__ = _(
        "Checks the spelling of selected text; with nothing selected, fixes the last misspelled word "
        "you typed, or reports your typing statistics when there is none"
    )

    def _show_stats(self):
        """Opens the statistics report, once, through runScriptModalDialog
        so the calling script returns at once."""
        if self._stats_open:
            return
        dlg = addon_settings.StatsDialog(gui.mainFrame, addon_settings.stats_text(self._stats.details()))
        self._stats_open = True

        def closed(result):
            self._stats_open = False
        gui.runScriptModalDialog(dlg, closed)

    def _next_selection_misspelling(self):
        state = self._selection_check
        try:
            # check_word adds the custom dictionary and the ignore rules
            # to what enchant flagged.
            err = next(
                e for e in state.errors
                if addon_spellcheck.check_word(e.word, state.lang) is False
                and not addon_spellcheck.looks_like_code(_token_at(state.checker.get_text(), e.wordpos))
            )
        except StopIteration:
            self._finish_selection_check()
            return
        except Exception:
            logger.exception("Typix: selection spellcheck iteration failed")
            self._finish_selection_check()
            return
        state.found = True

        def chosen(choice, cancelled):
            if choice:
                state.fixes.append((err.wordpos, err.word, choice))
            if cancelled:
                # Escape ends the whole check; corrections already chosen
                # are still applied.
                state.errors = iter(())
            self._next_selection_misspelling()
        self._suggest(err.word, state.lang, chosen)

    def _finish_selection_check(self):
        state, self._selection_check = self._selection_check, None
        if not state.found:
            # Translators: Spoken when the selection spelling check finds no misspelled words.
            ui.message(_("No spelling errors found"))
        elif not state.fixes:
            # Translators: Spoken when the selection spelling check ends without any word replaced.
            ui.message(_("No changes made"))
        elif state.readonly:
            # Translators: Spoken when corrected text can't be written because the field is read-only.
            ui.message(_("Cannot replace text, the field is read-only"))
        else:
            self._apply_selection_fixes(state)

    def _suggest(self, word, lang, on_done):
        """Shows suggestions for word and calls on_done(choice, cancelled):
        choice is the replacement or None, cancelled is True for Escape.
        Each suggestion says what's wrong, like "hello, missing l". Opened
        through runScriptModalDialog, the way NVDA opens its own dialogs,
        so it always comes to the front and the calling script returns at
        once (NVDA's watchdog times scripts). Add to dictionary is handled
        here."""
        options = addon_spellcheck.suggestions(word, lang)
        labels = [f"{sug}, {what}" if what else sug for sug, what in options]
        # Translators: Choice in the spelling suggestions list. {word} is the misspelled word.
        labels.append(_("Add {word} to dictionary").format(word=word))
        # Translators: Choice in the spelling suggestions list that leaves the word as it is.
        labels.append(_("Skip"))
        if options:
            # Translators: Message of the spelling suggestions dialog. {word} is the misspelled word, {count} the number of suggestions.
            message = ngettext(
                "{word} is misspelled. {count} suggestion.",
                "{word} is misspelled. {count} suggestions.",
                len(options),
            ).format(word=word, count=len(options))
        else:
            # Translators: Message of the spelling suggestions dialog when there are no suggestions. {word} is the misspelled word.
            message = _("{word} is misspelled. No suggestions.").format(word=word)
        # Translators: Title of the spelling suggestions dialog.
        dlg = wx.SingleChoiceDialog(gui.mainFrame, message, _("Spelling suggestions"), labels)
        dlg.SetSelection(0)

        def closed(result):
            if result != wx.ID_OK:
                on_done(None, True)
                return
            idx = dlg.GetSelection()
            if idx == len(options):
                addon_spellcheck.add_custom_word(word, lang)
            on_done(options[idx][0] if 0 <= idx < len(options) else None, False)
        gui.runScriptModalDialog(dlg, closed)

    @staticmethod
    def _find_word(obj, word):
        """A text range over the last whole occurrence of word before the
        caret, on the caret's line or the line above, or None."""
        pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)")
        try:
            for place in _lines_before_caret(obj):
                matches = list(pattern.finditer(place.text or ""))
                if matches:
                    found = place.copy()
                    found.collapse()
                    found.move(textInfos.UNIT_CHARACTER, matches[-1].start())
                    end = found.copy()
                    end.move(textInfos.UNIT_CHARACTER, len(word))
                    found.setEndPoint(end, "endToEnd")
                    return found
        except Exception:
            logger.debug("Typix: could not search the field for a word", exc_info=True)
        return None

    def _replace(self, obj, word, text, tries=40, quiet=False):
        """Types text over word once focus is back in obj's window. The word
        is selected through NVDA's text API and the selection is read back
        before typing, so nothing but that exact word can be overwritten.
        What follows the word up to the caret is typed again with it, in one
        SendInput, so the caret stays where it was and a fast typist's next
        key lands in the right place.

        A line break or Tab can't be typed: Enter sends the message in a
        chat app. With one in that stretch, text replacement rules (quiet)
        leave the word alone, and a fix asked for by command types only the
        word and moves the caret back afterwards. The clipboard is never
        used. Speech is muted for the swap, so NVDA doesn't read out the
        selection. Afterwards it says the new text, or with quiet (already
        spoken) plays the replaced sound."""
        if self._terminated:
            return
        if not _same_window(api.getFocusObject(), obj):
            if tries:
                core.callLater(50, self._replace, obj, word, text, tries - 1, quiet)
            elif not quiet:
                ui.message(self._no_way_back())
            return
        mode = speech.getState().speechMode
        speech.setSpeechMode(speech.SpeechMode.off)
        caret = None
        try:
            found = self._find_word(obj, word)
            caret = obj.makeTextInfo(textInfos.POSITION_CARET)
            span = found.copy()
            span.setEndPoint(caret, "endToEnd")
            expected = span.text or ""
            tail = expected[len(word):]
            separated = any(c in tail + text for c in _NOT_TYPED)
            if separated and quiet:
                self._unmute(mode)
                return
            if not expected.startswith(word):
                raise LookupError("the word is not where it was found")
            if separated:
                after = found.copy()
                after.collapse(end=True)
                back_by = _distance(after, caret, len(tail) + 1)
                found.updateSelection()
                if obj.makeTextInfo(textInfos.POSITION_SELECTION).text != word:
                    raise LookupError("selection read back differently")
                self._type(text)

                def landed(ok):
                    try:
                        if ok and back_by:
                            info = obj.makeTextInfo(textInfos.POSITION_CARET)
                            info.move(textInfos.UNIT_CHARACTER, back_by)
                            info.updateCaret()
                    except Exception:
                        logger.debug("Typix: could not put the caret back", exc_info=True)
                    self._last_misspelling = None
                    self._unmute(mode, text)
                self._when_landed(obj, text, landed)
                return
            span.updateSelection()
            if obj.makeTextInfo(textInfos.POSITION_SELECTION).text != expected:
                raise LookupError("selection read back differently")
            self._type(text + tail)
        except Exception:
            logger.debug("Typix: replacing a word failed", exc_info=True)
            # Left selected, the word would go with the next key.
            try:
                caret.updateCaret()
            except Exception:
                pass
            self._unmute(mode, None if quiet else self._unsafe(word))
            return
        if quiet:
            self._alert("REPLACED", 660)
            self._unmute(mode)
        else:
            self._last_misspelling = None
            self._unmute(mode, text)

    @staticmethod
    def _no_way_back():
        # Translators: Spoken when focus doesn't return to the text field after the suggestions dialog.
        return _("Could not get back to the text, nothing replaced")

    @staticmethod
    def _unsafe(word):
        # Translators: Spoken when the misspelled word can't be selected safely for replacing. {word} is that word.
        return _("Could not select {word} safely, nothing replaced").format(word=word)

    @staticmethod
    def _unmute(mode, message=None):
        """Brings speech back and says message, after a moment for the app
        to report its change."""
        def unmute():
            if speech.getState().speechMode == speech.SpeechMode.off:
                speech.setSpeechMode(mode)
            if message:
                ui.message(message)
        core.callLater(150, unmute)

    def _apply_selection_fixes(self, state, tries=40):
        """Corrects the selection check's words one at a time, each selected
        and read back like any other fix, and leaves the caret at the end
        of the corrected text. First to last, so the caret can finish there:
        each fix moves the words after it by a known number of characters."""
        if self._terminated:
            return
        focus = api.getFocusObject()
        if not _same_window(focus, state.obj):
            if tries:
                core.callLater(50, self._apply_selection_fixes, state, tries - 1)
            else:
                ui.message(self._no_way_back())
            return
        try:
            selection = focus.makeTextInfo(textInfos.POSITION_SELECTION)
            current = selection.text
        except Exception:
            current = None
        if current != state.original:
            # Translators: Spoken when the selection changed during the spelling check, so nothing is corrected.
            ui.message(_("The selection changed, nothing replaced"))
            return
        mode = speech.getState().speechMode
        speech.setSpeechMode(speech.SpeechMode.off)
        start = selection.copy()
        start.collapse()
        pos, word, _choice = state.fixes[-1]
        try:
            end = selection.copy()
            end.collapse(end=True)
            mark = self._range_at(start, state.original, pos, 0, word)
            mark.collapse(end=True)
            tail = _distance(mark, end, len(state.original) + 1)
        except Exception:
            logger.debug("Typix: could not measure the selection", exc_info=True)
            self._unmute(mode, self._unsafe(word))
            return
        self._next_fix(focus, state, start, 0, 0, tail, mode)

    def _next_fix(self, obj, state, start, index, shift, tail, mode):
        """Applies fix number index. shift is how far earlier fixes moved
        it, and tail the characters from the last word to the selection's
        end, for the caret at the finish."""
        if index == len(state.fixes):
            try:
                caret = obj.makeTextInfo(textInfos.POSITION_CARET)
                if tail:
                    caret.move(textInfos.UNIT_CHARACTER, tail)
                caret.updateCaret()
            except Exception:
                logger.debug("Typix: could not put the caret back", exc_info=True)
            # Translators: Spoken after the selection check corrected the words.
            self._unmute(mode, _("Corrected"))
            return
        pos, word, choice = state.fixes[index]
        try:
            found = self._range_at(start, state.original, pos, shift, word)
            found.updateSelection()
            if obj.makeTextInfo(textInfos.POSITION_SELECTION).text != word:
                raise LookupError("selection read back differently")
            self._type(choice)
        except Exception:
            logger.debug("Typix: replacing a selected word failed", exc_info=True)
            try:
                start.updateCaret()
            except Exception:
                pass
            self._unmute(mode, self._unsafe(word))
            return

        def landed(ok):
            if ok:
                self._next_fix(obj, state, start, index + 1, shift + len(choice) - len(word), tail, mode)
            else:
                self._unmute(mode, self._unsafe(word))
        self._when_landed(obj, choice, landed)

    @staticmethod
    def _range_at(start, original, pos, shift, word):
        """A range over word at index pos of original, moved by shift, or
        None. A line break is two characters in the text and one or two in
        the field, depending on the app, so both counts are tried."""
        for offset in (pos, pos - original[:pos].count("\r\n")):
            found = start.copy()
            found.move(textInfos.UNIT_CHARACTER, offset + shift)
            end = found.copy()
            end.move(textInfos.UNIT_CHARACTER, len(word))
            found.setEndPoint(end, "endToEnd")
            if found.text == word:
                return found
        return None

    def _when_landed(self, obj, text, then, tries=50):
        """Calls then(True) once text, just typed, is in the field before the
        caret, or then(False) after about a second. Typed input is queued
        while selecting takes effect at once, so moving on too soon would
        put the text in the wrong place."""
        try:
            info = obj.makeTextInfo(textInfos.POSITION_CARET)
            info.move(textInfos.UNIT_CHARACTER, -len(text), endPoint="start")
            landed = info.text == text
        except Exception:
            landed = False
        if landed or not tries:
            then(landed)
        else:
            core.callLater(20, self._when_landed, obj, text, then, tries - 1)

    def _type(self, text):
        """Types text over the selection as Unicode input, the way NVDA's
        braille input does, so no keyboard layout is involved. It all goes
        in one SendInput call, so the user's own keys can't land in the
        middle of it."""
        data = text.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        user32 = winBindings.user32
        inputs = []
        for unit in units:
            for direction in (0, user32.KEYEVENTF.KEYUP):
                item = user32.INPUT()
                item.type = user32.INPUT_TYPE.KEYBOARD
                item.ii.ki = user32.KEYBDINPUT()
                item.ii.ki.wScan = unit
                item.ii.ki.dwFlags = user32.KEYEVENTF.UNICODE | direction
                inputs.append(item)
        # These come back through event_typedCharacter, except where NVDA
        # works out typed characters itself and never sees injected ones.
        if not keyboardHandler.shouldUseToUnicodeEx(api.getFocusObject()):
            self._own_chars.extend(chr(unit) for unit in units)
            self._own_deadline = time.monotonic() + _OWN_CHARS_TIMEOUT
        winUser.SendInput(inputs)

    def _is_own_char(self, ch):
        """Whether ch is a character Typix just typed itself, which must not
        be echoed, counted or run through the rules again. Anything else
        passes: a key the user pressed earlier can arrive first."""
        if self._own_chars and time.monotonic() > self._own_deadline:
            self._own_chars.clear()
        if self._own_chars and self._own_chars[0] == ch:
            self._own_chars.popleft()
            return True
        return False

    # --- Keystrokes ---

    def _debounce_ok(self, key):
        """Per-key repeat filter; always True when the filter is 0."""
        if self._debounce_ms <= 0:
            return True
        now = time.monotonic() * 1000
        if now - self._last_play.get(key, -1e9) < self._debounce_ms:
            return False
        self._last_play[key] = now
        return True

    def _on_gesture(self, gesture):
        """Plays Backspace, Delete, Enter and Tab. Must always return True,
        or NVDA drops the keystroke."""
        try:
            if not isinstance(gesture, keyboardHandler.KeyboardInputGesture):
                return True
            category = _SPECIAL_KEYS.get((gesture.mainKeyName or "").lower())
            if category == "BACKSPACE" and _is_editable(api.getFocusObject()):
                self._stats.backspace()
            if not self._enabled or not category or not self._category_on(category):
                return True
            obj = api.getFocusObject()
            if not _is_editable(obj):
                # Tab and Enter can move focus before this runs, so the field
                # the user was just typing in still counts. Compared by live
                # identity through a weakref: id() is reused once a
                # short-lived NVDA object is collected, which used to let
                # these sounds play after focus had left the editor.
                typed = self._typed_obj_ref() if self._typed_obj_ref else None
                if obj is not typed:
                    return True
            if self._debounce_ok(category):
                self._play(category)
        except Exception:
            logger.debug("Typix: gesture hook failed", exc_info=True)
        return True

    def _finished_word(self, obj, buffered, rules=True):
        """The word that just ended, read from the field: (word, the
        whitespace chunk around it, its replacement or None, always None
        without rules). Speaks the word if NVDA's own echo would have got
        it wrong."""
        word = buffered
        token = ""
        replacement = None
        speak = self._echo_words(obj)
        try:
            # Terminals: NVDA delays echo there; leave the field alone.
            in_terminal = obj.role == controlTypes.Role.TERMINAL
        except Exception:
            in_terminal = True
        rules_on = self._expansions or self._capitalize_i or self._capitalize_sentences
        if not in_terminal and (speak or self._spellcheck_enabled or rules_on):
            lines = _lines_before_caret(obj)
            for place in lines:
                text = place.text or ""
                found = addon_spellcheck.trailing_word(text)
                if found:
                    if found.endswith(buffered):
                        word = found
                        end = len(text.rstrip())
                        # The whole space-separated chunk, so links and
                        # code can be told apart from words.
                        token = _token_at(text, end - 1)
                        # What comes before the word, for sentence starts.
                        before = text[:text.rfind(found, 0, end + 1)]
                        if not before.strip():
                            above = next(lines, None)
                            before = above.text if above is not None else ""
                        # Text replacement rules: expansions first, then
                        # a lone i, then the first word of a sentence.
                        if not rules:
                            pass
                        elif word in self._expansions:
                            replacement = self._expansions[word]
                        elif self._capitalize_i and word[0] == "i" and word.replace("’", "'") in _I_FORMS:
                            replacement = "I" + word[1:]
                        elif (
                            self._capitalize_sentences and word[0].islower()
                            and not addon_spellcheck.looks_like_code(token)
                        ):
                            ending = before.rstrip().rstrip("\"'“‘([{")
                            last = ending.split()[-1] if ending.split() else ""
                            # A dot inside the last chunk (e.g., i.e., a file
                            # name) isn't the end of a sentence.
                            if not ending or (
                                ending[-1] in ".!?…" and not addon_spellcheck.looks_like_code(last)
                            ):
                                replacement = word[0].upper() + word[1:]
                    break
        if speak and len(word) <= 100 and (replacement or word != buffered):
            speech.clearTypedWordBuffer()
            speech.speakText(replacement or word)
        return word, token, replacement

    def event_typedCharacter(self, obj, nextHandler, ch):
        if self._is_own_char(ch):
            return
        # A word just ended, by NVDA's own test, and NVDA buffered it. Read
        # the whole word from the field before nextHandler(), which speaks
        # and clears the buffer. NVDA also clears it on every caret move,
        # Backspace included, so after a correction it holds only what was
        # typed since, while the field holds the whole word. The field's
        # word is trusted only when it ends with the buffer, so
        # autocorrect, a moved caret or an unreadable field fall back to
        # NVDA's own behaviour. Enter ends a word too.
        buffered = word = replacement = None
        token = ""
        try:
            protected = api.isTypingProtected()
        except Exception:
            protected = True  # a dying object: don't risk reading a password
        chars = _nvda_word_chars()
        held = self._apostrophe_word
        self._apostrophe_word = None
        if held and chars is not None and not addon_spellcheck.is_word_char(ch):
            # The apostrophe held back below was a closing quote after all:
            # give NVDA its word back so it ends here.
            chars[:0] = held
        if (
            ch in addon_spellcheck.APOSTROPHES and chars and not protected
            and (self._spellcheck_enabled or self._echo_words(obj))
        ):
            # NVDA ends a word at an apostrophe and would say "can" for
            # "can't". Hold the word back: if a letter follows, the whole
            # word is read from the field when it really ends; if not, the
            # next key gives it back.
            self._apostrophe_word = list(chars)
            chars.clear()
        elif not addon_spellcheck.is_word_char(ch):
            buffered = "".join(chars) if chars else None
        if buffered and not protected and _is_editable(obj):
            word = buffered
            try:
                # Enter may have sent the text already, and replacing mutes
                # speech just as a game or chat answers, so its words stay.
                word, token, replacement = self._finished_word(obj, buffered, rules=ch not in _NOT_TYPED)
            except Exception:
                # A field can die while it's read. NVDA must still get the
                # character, so this falls back to NVDA's own word.
                logger.debug("Typix: could not read the finished word", exc_info=True)
        try:
            nextHandler()
        except Exception:
            # COMError from UIA elements that vanish in Electron/web apps.
            # Letting it out stalls NVDA's event pipeline.
            pass

        if replacement:
            # After nextHandler, so the character that ended the word has
            # reached the field first.
            core.callLater(0, self._replace, obj, word, replacement, 0, True)
        # Spelling works whether or not typing sounds are on. An expanded
        # shortcut isn't checked; a capitalised word still is.
        if (
            word and word not in self._expansions and self._spellcheck_enabled and self._category_on("MISSPELLED")
            and not addon_spellcheck.looks_like_code(token)
        ):
            lang = _input_language(obj)
            if addon_spellcheck.check_word(word, lang) is False:
                self._stats.misspelling()
                self._last_misspelling = SimpleNamespace(word=word, lang=lang)
                self._alert("MISSPELLED", 880)

        if not _is_editable(obj):
            return
        if ch not in _CONTROL_CHARS:
            self._stats.key(time.monotonic(), ch, word_ended=bool(buffered))
        elif buffered and ch in _LINE_ENDS:
            self._stats.word()
        if not self._enabled:
            return
        try:
            self._typed_obj_ref = weakref.ref(obj)
        except TypeError:
            self._typed_obj_ref = None
        if ch in _CONTROL_CHARS:
            return

        if protected:
            # In a password field every key gets the same plain letter
            # sound: per-key pan, pitch, sounds and categories would tell a
            # listener, or a recording, which keys were pressed.
            if self._category_on("LETTER"):
                self._play("LETTER")
            return
        category = char_category(ch)
        if category == "CAPS_LETTER" and not self._category_on(category):
            category = "LETTER"
        if self._category_on(category) and self._debounce_ok(ch):
            self._play(category, ch)

    def _play(self, category, char=None):
        """Plays a category sound. A character's own sound replaces the
        category sound, and its pan and pitch come from the global rules
        first, then the pack. CAPS_LETTER plays the caps sound on top of
        the letter sound. Returns whether anything played."""
        pack = self._current_pack
        loader = self.sound_loader
        pan = pitch = 0.0
        if char:
            pan, pitch = loader.get_char_effects(pack, char)
            rule = self._global_rules.get(char) or {}
            pan = float(rule.get("pan", pan))
            pitch = float(rule.get("pitch", pitch))
        paths = []
        if category == "CAPS_LETTER":
            paths.append(loader.get_sound_path(pack, "CAPS_LETTER"))
            category = "LETTER"
        paths.append((char and loader.get_char_sound_path(pack, char)) or loader.get_sound_path(pack, category))
        paths = [p for p in paths if p]
        for path in paths:
            self.audio_engine.play_sound(path, pan=pan, pitch=pitch)
        return bool(paths)
