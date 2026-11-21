# Typix for NVDA
# Copyright (C) 2025 OlegTheSnowman

import os
import re
import time
import threading
import functools
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
import eventHandler
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
# Longest a field's caret events stay held with nothing renewing the hold, in seconds.
_HUSH_TIMEOUT = 1.5
# An automatic replacement waits for this long without a key, in seconds, so
# no key the user already pressed is still on its way to the field.
_IDLE_BEFORE_REPLACE = 0.35
# Keys pressed during an automatic replacement are held at most this long.
_KEY_HOLD_TIMEOUT = 2.0
# After this long without a key, keys that never arrived are forgotten.
# Shortcuts and NVDA commands never arrive as characters, so a stale count
# made every replacement wait this long after the pause.
_IN_FLIGHT_FORGET = 0.5
# Words further than this from the caret are left alone.
_CARET_LIMIT = 2000
# Unicode input, as Typix types it; never held.
_VK_PACKET = 0xE7
_ALT_KEYS = {0x12, 0xA4, 0xA5}
# Ctrl and Windows: a letter pressed with them is a shortcut, not a character.
_CHORD_KEYS = {0x11, 0xA2, 0xA3, 0x5B, 0x5C}
_MODIFIER_KEYS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, both Windows keys
# Keys that normally produce a typed character: Backspace, Enter, space,
# digits, letters, numpad and punctuation.
_CHARACTER_KEYS = (
    {0x08, 0x0D, 0x20} | set(range(0x30, 0x3A)) | set(range(0x41, 0x5B))
    | set(range(0x60, 0x70)) | set(range(0xBA, 0xC1)) | set(range(0xDB, 0xE0)) | {0xE2}
)


def _modifier_down():
    """Whether Shift, Ctrl, Alt or a Windows key is down. Never raises."""
    try:
        return any(winUser.getAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_KEYS)
    except Exception:
        return False
# How long a selection gets to show up in the field, in seconds.
_SELECT_TIMEOUT = 0.1
# How long a swap gets to reach the field, in seconds.
_LAND_TIMEOUT = 1.0
# How long, in milliseconds, the hold outlasts a swap. Browsers send their
# caret events late, and one let through would read the old selection.
_HUSH_GRACE = 300

# Developer tracing. On only when tools/dev_install.py has put its reloader
# next to the add-on, so a packaged Typix never logs what is typed.
_TRACE = os.path.exists(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "typixDevReload.py")
)


def _trace(message, *args, exc_info=False):
    """Writes a line to the NVDA log in a developer install only."""
    if _TRACE:
        from logHandler import log
        log.info("Typix trace: " + message, *args, exc_info=exc_info)


def _trace_field(obj, label):
    """Traces the caret's line and the selection in obj."""
    if not _TRACE:
        return
    try:
        line = obj.makeTextInfo(textInfos.POSITION_CARET)
        line.expand(textInfos.UNIT_LINE)
        selected = obj.makeTextInfo(textInfos.POSITION_SELECTION).text
        _trace("%s: line %r, selection %r", label, line.text, selected)
    except Exception:
        _trace("%s: field unreadable", label, exc_info=True)


def _describe(obj):
    """Which control obj is, for traces."""
    try:
        return "%s role=%s window=%s app=%s" % (
            type(obj).__name__, obj.role.name, obj.windowClassName, obj.appModule.appName
        )
    except Exception:
        return repr(obj)



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


def _keyboard_thread(obj):
    """The thread that really has keyboard focus. In WebView2 apps such as
    WhatsApp, the window NVDA reports for the field can belong to another
    thread than the one the keys go to, and that thread keeps the default
    layout while the user types in another language. Falls back to obj's
    own thread."""
    try:
        info = winUser.getGUIThreadInfo(0)
        if info.hwndFocus:
            thread = winUser.getWindowThreadProcessID(info.hwndFocus)[1]
            if thread:
                return thread
    except Exception:
        logger.debug("Typix: could not find the keyboard focus thread", exc_info=True)
    return obj.windowThreadID


def _input_language(obj):
    """Locale name of the keyboard layout the user is typing with."""
    try:
        lcid = winUser.getKeyboardLayout(_keyboard_thread(obj)) & 0xFFFF
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


def _in_terminal(obj):
    """True in a terminal, and for an object that can't say what it is."""
    try:
        return obj.role == controlTypes.Role.TERMINAL
    except Exception:
        return True


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
        self._hush = None                # field whose selection changes NVDA must not read, and until when
        self._hush_serial = 0            # tells a swap's release from a newer swap's hold
        self._own_chars = deque()        # characters Typix typed, on their way back
        self._own_deadline = 0.0         # when to stop expecting them
        self._init_auto_replace()
        # Typing statistics, kept across sessions; counts only, never text.
        self._stats = TypingStats(os.path.join(config_path, "typix_stats.json"))
        self.update_settings()

        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(addon_settings.TypixSettingsPanel)
        inputCore.decide_executeGesture.register(self._on_gesture)
        inputCore.decide_handleRawKey.register(self._on_raw_key)
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
        self._hush = None
        try:
            inputCore.decide_handleRawKey.unregister(self._on_raw_key)
        except Exception:
            pass
        self._release_keys()
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
                def chosen(choice, cancelled):
                    # Added or skipped, the word is dealt with: without this
                    # the command would offer it again. Escape can try again.
                    if not cancelled:
                        self._last_misspelling = None
                    if choice:
                        self._replace(obj, last.word, choice)
                self._suggest(last.word, last.lang, chosen)
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
    def _find_word(obj, word, prefix=None):
        """A text range over the last whole occurrence of word before the
        caret, on the caret's line or the line above, or None. With prefix,
        only an occurrence with exactly that text before it on its line."""
        pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)")
        try:
            for place in _lines_before_caret(obj):
                text = place.text or ""
                matches = [
                    m for m in pattern.finditer(text)
                    if prefix is None or text[:m.start()] == prefix
                ]
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

    def _replace(self, obj, word, text, tries=40):
        """Types text over word once focus is back in obj's window, for a
        fix chosen by command. Automatic replacements use _auto_swap. The
        word is selected through NVDA's text API and the selection is read
        back before typing, so nothing but that exact word can be
        overwritten. What follows the word up to the caret is typed again
        with it, in one SendInput, so the caret stays where it was.

        A line break or Tab can't be typed: Enter sends the message in a
        chat app. With one in that stretch, only the word is typed and the
        caret is moved back afterwards. The clipboard is never used. NVDA
        isn't muted: only the field's caret events are held back for the
        swap, so nothing else stops being spoken. Afterwards it says the new
        text."""
        if self._terminated:
            return
        if not _same_window(api.getFocusObject(), obj):
            if tries:
                core.callLater(50, self._replace, obj, word, text, tries - 1)
            else:
                ui.message(self._no_way_back())
            return
        # A browser may have made a new object for the field in the meantime.
        old = obj
        obj = api.getFocusObject()
        _trace("replace %r with %r, same object=%s, %s", word, text, obj is old, _describe(obj))
        caret = None
        try:
            found = self._find_word(obj, word)
            caret = obj.makeTextInfo(textInfos.POSITION_CARET)
            span = found.copy()
            span.setEndPoint(caret, "endToEnd")
            expected = span.text or ""
            tail = expected[len(word):]
            separated = any(c in tail + text for c in _NOT_TYPED)
            _trace("found=%r span to caret=%r tail=%r separated=%s", found.text, expected, tail, separated)
            if not expected.startswith(word):
                raise LookupError("the word is not where it was found")
            if separated:
                after = found.copy()
                after.collapse(end=True)
                back_by = _distance(after, caret, len(tail) + 1)
                self._hold_selection_speech(obj)
                found.updateSelection()
                self._await_selection(obj, word)
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
                    self._finish_swap(obj, text)
                self._when_landed(obj, text, landed)
                return
            self._hold_selection_speech(obj)
            span.updateSelection()
            _trace_field(obj, "just after selecting")
            self._await_selection(obj, expected)
            _trace("typing %r over the selection", text + tail)
            self._type(text + tail)
        except Exception:
            logger.debug("Typix: replacing a word failed", exc_info=True)
            _trace("replacing failed", exc_info=True)
            if _TRACE:
                for delay in (0, 30, 100, 250, 500, 1000):
                    core.callLater(delay, _trace_field, obj, "%d ms after failing" % delay)
            # Left selected, the word would go with the next key.
            try:
                caret.updateCaret()
            except Exception:
                pass
            self._finish_swap(obj, self._unsafe(word))
            return
        self._last_misspelling = None
        # Not by the text before the caret: a fast typist's next key is
        # already there.
        def done(ok):
            if _TRACE:
                try:
                    line = obj.makeTextInfo(textInfos.POSITION_CARET)
                    line.expand(textInfos.UNIT_LINE)
                    _trace("landed=%s, line now %r", ok, line.text)
                except Exception:
                    _trace("landed=%s, line unreadable", ok, exc_info=True)
            self._finish_swap(obj, text)
        self._when_landed(obj, None, done)

    # --- Automatic replacements ---
    #
    # Text replacements select a word and type over it. A key the user had already pressed could land on that selection
    # and wipe it out: on claude.ai "this is a test" became "s a test". So
    # they wait for a pause with no modifier down, hold every new key at
    # NVDA's raw key hook, wait until keys already let through have reached
    # the field, then swap and replay the held keys in order. All words
    # waiting are swapped in one go, the last first, so the positions of the
    # earlier ones stay valid. Only the words are typed, and the caret goes
    # back where it was, so the app's undo takes back one word at a time.
    # Fixes chosen by command don't need any of this: the user isn't typing.

    def _init_auto_replace(self):
        self._auto_pending = deque()     # (obj, word, replacement, prefix) waiting for a pause
        self._auto_busy = False          # a swap is running
        self._key_lock = threading.Lock()
        self._held_keys = []             # raw key events held during a swap
        self._holding = False
        self._hold_serial = 0            # tells a hold's timer from a newer hold
        self._hold_deadline = 0.0        # when the hold gives up, renewed at each step
        self._last_key_time = 0.0        # last key press, from the raw hook
        self._keys_sent = 0              # character keys let through the hook
        self._keys_arrived = 0           # characters that reached event_typedCharacter
        self._alt_down = False
        self._chord_down = set()          # Ctrl and Windows keys held

    def _queue_auto_replace(self, obj, word, replacement, prefix):
        if any(c in replacement for c in _NOT_TYPED):
            # Enter sends the message in a chat app.
            return
        self._auto_pending.append((obj, word, replacement, prefix))
        if len(self._auto_pending) == 1 and not self._auto_busy:
            core.callLater(0, self._run_auto_replace)

    def _keys_in_flight(self):
        """Character keys let through the hook that haven't arrived as
        typed characters yet. Some never arrive (an NVDA command, a dead
        key), so after a long pause the counts start over."""
        with self._key_lock:
            in_flight = self._keys_sent - self._keys_arrived
            if in_flight < 0 or time.monotonic() - self._last_key_time > _IN_FLIGHT_FORGET:
                self._keys_sent = self._keys_arrived = 0
                return 0
            return in_flight

    def _run_auto_replace(self):
        if self._terminated or self._auto_busy or not self._auto_pending:
            return
        quiet_for = time.monotonic() - self._last_key_time
        if quiet_for < _IDLE_BEFORE_REPLACE:
            core.callLater(int((_IDLE_BEFORE_REPLACE - quiet_for) * 1000) + 10, self._run_auto_replace)
            return
        if _modifier_down():
            # Typed text would arrive with Shift, Ctrl or Alt applied.
            core.callLater(50, self._run_auto_replace)
            return
        if self._keys_in_flight() > 0:
            # Checked before holding anything, so waiting costs no lag.
            _trace("keys on their way: sent %d, arrived %d", self._keys_sent, self._keys_arrived)
            core.callLater(50, self._run_auto_replace)
            return
        self._auto_busy = True
        serial = self._hold_keys()
        if self._keys_in_flight() > 0:
            # A key got through just before the hold.
            self._end_auto(retry=True)
            return
        items = list(self._auto_pending)
        self._auto_pending.clear()
        try:
            self._auto_swap(serial, items)
        except Exception:
            logger.debug("Typix: automatic replacement failed", exc_info=True)
            _trace("auto swap failed", exc_info=True)
            self._end_auto()

    def _auto_swap(self, serial, items):
        """Selects from the first waiting word to the caret and types that
        stretch again with every waiting replacement in it, in one go. The
        caret ends where it was without being moved. Replacing only the
        word and moving the caret back lost the rest of the line on
        claude.ai in Firefox."""
        obj = api.getFocusObject()
        caret = obj.makeTextInfo(textInfos.POSITION_CARET)
        found = []
        for item_obj, word, replacement, prefix in items:
            if not _same_window(obj, item_obj):
                _trace("skip %r: focus has moved", word)
                continue
            place = self._find_word(obj, word, prefix)
            if place is None or place.text != word:
                _trace("skip %r: not found after %r", word, prefix)
                continue
            span = place.copy()
            span.setEndPoint(caret, "endToEnd")
            stretch = span.text or ""
            if any(c in stretch for c in _NOT_TYPED):
                # A line break since: the text may have been sent.
                _trace("skip %r: a line break follows it", word)
                continue
            if not stretch.startswith(word) or len(stretch) > _CARET_LIMIT:
                _trace("skip %r: the text up to the caret reads %r", word, stretch[:80])
                continue
            if any(len(stretch) == len(f[3]) for f in found):
                continue
            found.append((span, word, replacement, stretch))
        if not found:
            self._end_auto()
            return
        # The first word has the longest stretch; every other word sits in
        # it at a known distance from its end. Last first, so the earlier
        # positions stay valid.
        found.sort(key=lambda f: -len(f[3]))
        span, _word, _replacement, original = found[0]
        new = original
        for _span, word, replacement, stretch in reversed(found):
            at = len(original) - len(stretch)
            if new[at:at + len(word)] != word:
                _trace("skip all: %r is not where it was", word)
                self._end_auto()
                return
            new = new[:at] + replacement + new[at + len(word):]
        _trace("auto swap %r -> %r", original, new)
        self._hold_selection_speech(obj)
        try:
            span.updateSelection()
            self._await_selection(obj, original)
            self._type(new)
        except Exception:
            _trace("auto swap failed", exc_info=True)
            try:
                # Left selected, the text would go with the next key.
                caret.updateCaret()
            except Exception:
                pass
            self._finish_swap(obj)
            self._end_auto()
            return

        def landed(ok):
            if serial != self._hold_serial:
                return
            _trace_field(obj, "after the swap")
            if ok and self._echo_words(obj):
                speech.speakText(" ".join(f[2] for f in found))
            self._alert("REPLACED", 660)
            self._finish_swap(obj)
            self._end_auto()
        self._when_landed(obj, new, landed)

    def _end_auto(self, retry=False, tries=20):
        """Gives the held keys back, but only once nothing is selected: a
        key landing on a selection would wipe it out. After about a second
        they go anyway; they can't be held forever."""
        try:
            selection = api.getFocusObject().makeTextInfo(textInfos.POSITION_SELECTION)
            selected = not selection.isCollapsed
            if selected:
                selection.collapse(end=True)
                selection.updateCaret()
        except Exception:
            selected = False
        if selected and tries:
            _trace("a selection is still there, holding the keys a little longer")
            core.callLater(50, self._end_auto, retry, tries - 1)
            return
        self._auto_busy = False
        self._release_keys()
        if self._auto_pending:
            core.callLater(100 if retry else 0, self._run_auto_replace)

    def _on_raw_key(self, vkCode=None, scanCode=None, extended=False, pressed=True, **kwargs):
        """NVDA's raw key hook, on NVDA's input thread. Holds every key while
        a swap is running. Must stay quick and must never raise."""
        try:
            if vkCode == _VK_PACKET:
                return True
            if vkCode in _ALT_KEYS:
                self._alt_down = pressed
            elif vkCode in _CHORD_KEYS:
                if pressed:
                    self._chord_down.add(vkCode)
                else:
                    self._chord_down.discard(vkCode)
            with self._key_lock:
                if pressed:
                    self._last_key_time = time.monotonic()
                if self._holding:
                    self._held_keys.append((vkCode, scanCode, extended, pressed))
                    return False
                if pressed and vkCode in _CHARACTER_KEYS and not self._alt_down and not self._chord_down:
                    self._keys_sent += 1
            return True
        except Exception:
            return True

    def _hold_keys(self):
        with self._key_lock:
            self._holding = True
            self._hold_serial += 1
            serial = self._hold_serial
        self._hold_deadline = time.monotonic() + _KEY_HOLD_TIMEOUT
        core.callLater(int(_KEY_HOLD_TIMEOUT * 1000), self._hold_expired, serial)
        return serial

    def _hold_expired(self, serial):
        """A swap that never finished must not keep the keys or stop later
        replacements."""
        if serial != self._hold_serial or not self._holding:
            return
        left = self._hold_deadline - time.monotonic()
        if left > 0:
            core.callLater(int(left * 1000) + 10, self._hold_expired, serial)
            return
        _trace("hold timed out")
        self._hold_serial += 1  # the stuck swap stops at its next step
        self._end_auto()

    def _release_keys(self):
        """Ends the hold and replays the held keys, in order."""
        with self._key_lock:
            keys = self._held_keys
            self._held_keys = []
            self._holding = False
        if not keys:
            return
        _trace("replaying %d held key events", len(keys))
        user32 = winBindings.user32
        inputs = []
        for vk, scan, extended, pressed in keys:
            item = user32.INPUT()
            item.type = user32.INPUT_TYPE.KEYBOARD
            item.ii.ki = user32.KEYBDINPUT()
            item.ii.ki.wVk = vk or 0
            item.ii.ki.wScan = scan or 0
            flags = 0
            if extended:
                flags |= user32.KEYEVENTF.EXTENDEDKEY
            if not pressed:
                flags |= user32.KEYEVENTF.KEYUP
            item.ii.ki.dwFlags = flags
            inputs.append(item)
        try:
            winUser.SendInput(inputs)
        except Exception:
            logger.exception("Typix: could not replay held keys")

    @staticmethod
    def _no_way_back():
        # Translators: Spoken when focus doesn't return to the text field after the suggestions dialog.
        return _("Could not get back to the text, nothing replaced")

    @staticmethod
    def _unsafe(word):
        # Translators: Spoken when the misspelled word can't be selected safely for replacing. {word} is that word.
        return _("Could not select {word} safely, nothing replaced").format(word=word)

    @staticmethod
    def _await_selection(obj, expected):
        """Returns once the selection reads expected, or raises LookupError.
        Browsers apply a selection a few milliseconds after being asked. This
        blocks instead of using a timer, so typing follows the selection at
        once and a fast typist's next key can't land on the selected word."""
        started = time.monotonic()
        while True:
            if obj.makeTextInfo(textInfos.POSITION_SELECTION).text == expected:
                return
            if time.monotonic() - started >= _SELECT_TIMEOUT:
                raise LookupError("selection read back as %r, not %r" % (
                    obj.makeTextInfo(textInfos.POSITION_SELECTION).text, expected
                ))
            time.sleep(0.002)

    def _hold_selection_speech(self, obj):
        """Stops NVDA reading out the selection Typix is about to make in
        obj. NVDA reads selection changes from caret events, so holding back
        only those, only for this field, leaves all other speech alone."""
        self._hush_serial += 1
        self._hush = (obj, time.monotonic() + _HUSH_TIMEOUT)

    def _extend_hold(self):
        hush = self._hush
        if hush:
            self._hush = (hush[0], time.monotonic() + _HUSH_TIMEOUT)

    @staticmethod
    def _sync_selection(obj):
        """Brings NVDA's stored selection up to date, so its next caret event
        finds no change to read out. It compares against the focus object,
        which a browser may have replaced with a new one during the swap."""
        targets = [obj]
        focus = api.getFocusObject()
        if focus is not obj and _same_window(focus, obj):
            targets.append(focus)
        for target in targets:
            try:
                if hasattr(target, "detectPossibleSelectionChange"):
                    target._lastSelectionPos = target.makeTextInfo(textInfos.POSITION_SELECTION)
                    target.hasContentChangedSinceLastSelection = False
            except Exception:
                logger.debug("Typix: could not update NVDA's selection", exc_info=True)

    def _finish_swap(self, obj, message=None):
        """Speaks message and ends the hold a moment later, once the caret
        events the swap caused have arrived."""
        self._hold_selection_speech(obj)
        self._sync_selection(obj)
        core.callLater(_HUSH_GRACE, self._release_hold, obj, self._hush_serial)
        if message:
            ui.message(message)

    def _release_hold(self, obj, serial, tries=20):
        """Ends the hold unless a newer swap has taken it over, and lets
        braille and the review cursor catch up."""
        if serial != self._hush_serial or not self._hush:
            return
        try:
            pending = eventHandler.isPendingEvents("caret")
        except Exception:
            pending = False
        if pending and tries:
            self._extend_hold()
            core.callLater(50, self._release_hold, obj, serial, tries - 1)
            return
        self._sync_selection(obj)
        self._hush = None
        try:
            eventHandler.queueEvent("caret", obj)
        except Exception:
            logger.debug("Typix: could not queue a caret event", exc_info=True)

    def event_caret(self, obj, nextHandler):
        hush = self._hush
        if hush:
            if time.monotonic() > hush[1]:
                self._hush = None
            elif _same_window(obj, hush[0]):
                self._sync_selection(hush[0])
                return
        nextHandler()

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
            ui.message(self._unsafe(word))
            return
        self._next_fix(focus, state, start, 0, 0, tail)

    def _next_fix(self, obj, state, start, index, shift, tail):
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
            self._finish_swap(obj, _("Corrected"))
            return
        pos, word, choice = state.fixes[index]
        try:
            found = self._range_at(start, state.original, pos, shift, word)
            self._hold_selection_speech(obj)
            found.updateSelection()
            self._await_selection(obj, word)
            self._type(choice)
        except Exception:
            logger.debug("Typix: replacing a selected word failed", exc_info=True)
            try:
                start.updateCaret()
            except Exception:
                pass
            self._finish_swap(obj, self._unsafe(word))
            return

        def landed(ok):
            if ok:
                self._next_fix(obj, state, start, index + 1, shift + len(choice) - len(word), tail)
            else:
                self._finish_swap(obj, self._unsafe(word))
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

    def _when_landed(self, obj, text, then, deadline=None):
        """Calls then(True) once text, just typed, is in the field before the
        caret, or then(False) after about a second. Typed input is queued
        while selecting takes effect at once, so moving on too soon would
        put the text in the wrong place. With text None, it only waits for
        the selection to be replaced. The hold on selection speech is renewed
        on the way, because reading a browser's text can take a while."""
        if deadline is None:
            deadline = time.monotonic() + _LAND_TIMEOUT
        self._extend_hold()
        try:
            if text is None:
                landed = obj.makeTextInfo(textInfos.POSITION_SELECTION).isCollapsed
            else:
                info = obj.makeTextInfo(textInfos.POSITION_CARET)
                info.move(textInfos.UNIT_CHARACTER, -len(text), endPoint="start")
                landed = info.text == text
        except Exception:
            landed = False
        if landed or time.monotonic() >= deadline:
            then(landed)
        else:
            core.callLater(20, self._when_landed, obj, text, then, deadline)

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

    def _finished_word(self, obj, buffered, rules=True, ending=" "):
        """The word that just ended, read from the field: (word, the
        whitespace chunk around it, its replacement or None, always None
        without rules, the text before it on its line). ending is the
        character that ended the word, for traces. Speaks the word if
        NVDA's own echo would have got it wrong."""
        word = buffered
        token = ""
        replacement = None
        prefix = None
        speak = self._echo_words(obj)
        # Terminals: NVDA delays echo there; leave the field alone.
        in_terminal = _in_terminal(obj)
        rules_on = bool(self._expansions)
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
                        # Where the word is on its line, so the swap finds
                        # this occurrence and not another.
                        prefix = text[:text.rfind(found, 0, end + 1)]
                        if rules and word in self._expansions:
                            replacement = self._expansions[word]
                        _trace(
                            "finished word=%r buffered=%r ended by %r, before=%r token=%r rules=%s -> %r in %s",
                            word, buffered, ending, prefix[-60:], token, rules, replacement, _describe(obj),
                        )
                    else:
                        _trace("field word %r doesn't end with buffered %r, left alone", found, buffered)
                    break
        # A replacement is spoken once it is in the field, by _auto_swap;
        # the swap can still be dropped or fail.
        if speak and len(word) <= 100 and word != buffered:
            speech.clearTypedWordBuffer()
            speech.speakText(word)
        return word, token, replacement, prefix

    def event_typedCharacter(self, obj, nextHandler, ch):
        if self._is_own_char(ch):
            return
        with self._key_lock:
            self._keys_arrived += 1
        if ch in _NOT_TYPED and self._auto_pending:
            _trace("%r dropped %d waiting replacements", ch, len(self._auto_pending))
            self._auto_pending.clear()
        # A word just ended, by NVDA's own test, and NVDA buffered it. Read
        # the whole word from the field before nextHandler(), which speaks
        # and clears the buffer. NVDA also clears it on every caret move,
        # Backspace included, so after a correction it holds only what was
        # typed since, while the field holds the whole word. The field's
        # word is trusted only when it ends with the buffer, so
        # autocorrect, a moved caret or an unreadable field fall back to
        # NVDA's own behaviour. Enter ends a word too.
        buffered = word = replacement = prefix = None
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
            and not _in_terminal(obj)
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
                word, token, replacement, prefix = self._finished_word(
                    obj, buffered, rules=ch not in _NOT_TYPED, ending=ch
                )
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
            self._queue_auto_replace(obj, word, replacement, prefix)
        # Spelling works whether or not typing sounds are on. An expanded
        # shortcut isn't checked.
        if (
            word and word not in self._expansions and self._spellcheck_enabled
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
            # With Letters unchecked a capital plays only its caps sound.
            category = "LETTER" if self._category_on("LETTER") else None
        if category:
            paths.append((char and loader.get_char_sound_path(pack, char)) or loader.get_sound_path(pack, category))
        paths = [p for p in paths if p]
        for path in paths:
            self.audio_engine.play_sound(path, pan=pan, pitch=pitch)
        return bool(paths)
