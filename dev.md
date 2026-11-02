# Typix developer notes

Typix is an NVDA add-on that plays a sound for each key typed in a text field, and adds spelling, text replacements and typing statistics. Everything runs inside NVDA, so only the parts in `tests/` can run without it. README.md covers building, testing and translating.

## Files

* `globalPlugins/typix/__init__.py`: the GlobalPlugin. Keystrokes, word reading, spelling commands and replacing text.
* `audio_engine.py`: a pool of nvwave players, each with a worker thread. Decoding, resampling, pitch and pan happen on the worker, and results are cached.
* `sound_loader.py`: finds, loads, saves, imports and exports packs. `CATEGORIES` is the one list of sound categories.
* `config.py`: settings in NVDA's configuration, under `typix`, plus the migrations from the old Typing Soundscape name. Keep the migrations; existing users depend on them.
* `settings.py`: the settings panel, pack editor and dialogs.
* `spellcheck.py`: bundled pyenchant, dictionary downloads and the custom dictionary. Its word rules match NVDA's.
* `storage.py`: atomic JSON reads and writes.
* `typing_stats.py`: statistics. Counts only, never text.

## How keystrokes arrive

`event_typedCharacter` handles ordinary characters. `inputCore.decide_executeGesture` handles Backspace, Delete, Enter and Tab, which never fire that event; the hook must always return True, or NVDA drops the key.

An exception before `nextHandler()` costs NVDA the character, so everything before it is guarded. UIA objects can die mid-call with a COMError.

`_is_editable` accepts `DOCUMENT` as well as `EDITABLETEXT` and `TERMINAL`, because modern Word reports its document as `DOCUMENT`. If an app is silent, look here first.

Global plugin focus events pass through a chain that any add-on can break. Poll `api.getFocusObject()` if focus tracking is ever needed.

## Reading the finished word

NVDA's buffer, `speech.speech._curWordChars`, is cleared on every caret move, so it holds only what was typed since a correction. Typix reads the word from the field and trusts it only when it ends with the buffer. This is private to NVDA, so check it at each new NVDA release.

Moving up a line from inside a line only reaches the start of that line. `_lines_before_caret` therefore moves from the line's start.

## Changing text

Typix never uses the clipboard. It selects through NVDA's text API, reads the selection back, and types Unicode input over it in one `SendInput` call. Don't use `brailleInput.sendChars`: it moved in NVDA 2026.3, and in some apps it calls `event_typedCharacter` directly.

`_is_own_char` stops Typix reacting to its own typed characters. Enter and Tab are never typed, because Enter sends the message in a chat app. Typed input is queued while selecting takes effect at once, so `_when_landed` waits for the text before the next step.

Never mute NVDA for a swap: `setSpeechMode` drops everything else too, such as the reply from a MUD. NVDA reads selection changes from `event_caret`, through `detectPossibleSelectionChange`, so `event_caret` holds back caret events for that one window during a swap, and `_finish_swap` then brings `_lastSelectionPos` up to date.

Don't position text with Backspace or arrow keys. It broke in rich web editors.

Automatic rules don't run in terminals, where a capital would break commands. In a password field every character key plays the plain letter sound, centred, and nothing is read or replaced.

## NVDA versions

The minimum is 2026.1, the first 64-bit NVDA, so only the 64-bit spelling libraries ship. NVDA breaks its API once a year, in the `.1` release, so check its developer changes before raising `lastTestedNVDAVersion`.

## Settings panel

Controls inside a `_group` box must use the box as their parent. Mixed parents scramble the Tab order.
