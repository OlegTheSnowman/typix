# Typix

[Українська](README.uk.md)

Typix is an add-on for the NVDA screen reader that plays a sound for every key you type in a text field. Letters, capitals, space, Enter, Tab, Backspace, Delete and punctuation can each have their own sound, and each character can have its own stereo position and pitch.

It can also beep when you finish a misspelled word, offer corrections, turn shortcuts into longer text, say the whole word after you correct it, and report your typing speed.

The interface and help are available in English and Ukrainian.

## Install

Typix needs NVDA 2026.1 or later.

1. Download the latest `.nvda-addon` file from the [Releases page](../../releases).
2. Open the file and confirm the installation.
3. Restart NVDA.

The settings are in NVDA's Settings dialog, under Typix. The two commands have no keys by default; assign them in Input gestures, in the Typix category.

## Help

Read the full help in [English](doc/en/readme.md) or [Ukrainian](doc/uk/readme.md). Inside NVDA it opens from the Add-on Store, on the Installed add-ons tab.

## Privacy

Typix goes online only to download a spelling dictionary from LibreOffice's dictionaries on GitHub, the first time you use a language. What you type is never sent anywhere. Typing statistics are counts only, saved in your NVDA configuration folder.

## Translate

Each language has one file, `locale/<language>/LC_MESSAGES/nvda.po`, which you can edit in Poedit or any other .po editor.

To start a new language, open `locale/typix.pot` in Poedit with New from POT/PO file, pick your language, and save it as `locale/<language>/LC_MESSAGES/nvda.po`. Every string has a note saying where it appears. Keep placeholders like `{name}` as they are. The letter after an ampersand is the keyboard shortcut, so choose one that suits your language and is not used twice in a dialog. The help goes in `doc/<language>/readme.md`.

## Build

You need Windows, Python and Babel (`pip install babel`).

```
build.bat
```

The script raises the version number in `manifest.ini`, converts the help to HTML, compiles the translations, and writes the package to `dist/`.

After changing interface text, run `python tools/i18n.py update`. It regenerates the template and merges it into every language.

## Test

The tests are plain unittest scripts. Each one runs on its own, for example:

```
python tests/test_sound_loader.py
```

They cover the code that doesn't need NVDA. Anything in `__init__.py` or `settings.py` has to be tried in NVDA itself.

## Author and license

Copyright (C) 2025 OlegTheSnowman. Typix is released under the GNU General Public License version 2. See [LICENSE](LICENSE).
