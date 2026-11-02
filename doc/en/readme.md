# Typix

Typix plays a sound for every key you type in a text field. Letters, capitals, space, Enter, Tab, Backspace, Delete and punctuation can each have their own sound, and each character can have its own stereo position and pitch. It can also beep when you finish a misspelled word, turn shortcuts into longer text, and report your typing speed.

Typix needs NVDA 2026.1 or later. It comes with a few mechanical keyboard packs, and you can build, import, export and share your own.

## Settings

To open the settings, press NVDA+N, then choose Preferences, Settings, and the Typix category.

Play typing sounds turns all typing sounds on or off.

### Sounds

* Volume: how loud typing sounds are.
* Stereo panning per key: lets each key sit left or right, as the pack or your rules say. When this is off, every sound plays in the centre.
* Panning width: how far left and right sounds can go, from 0 for centre only to 100 for full stereo.
* Key repeat filter: ignores the same key again within this many milliseconds, so holding a key down doesn't flood you with clicks. 0 turns the filter off.
* Play sounds for: a checklist of the sound categories. Uncheck a category to silence it. If you uncheck capital letters, capitals still play the normal letter sound and only the extra caps sound goes.

### Sound pack

* Sound pack: the pack in use. Each entry reads as name, author and description.
* Preview sound and Play: hear any sound in the selected pack.
* Edit pack: opens the pack editor.
* New pack: asks for a name, then opens the editor on an empty pack.
* Import pack and Export pack: install a pack from a zip file, or save a pack as a zip to share.
* Delete pack: removes the pack for good. A bundled pack you delete stays deleted.
* Open packs folder: opens the folder in File Explorer. Packs live in your NVDA configuration folder under typix_packs, so they survive add-on updates.

### Character rules for every pack

A rule sets the pan and pitch of one character for every pack. Add rule asks for a character, then for its pan, from -100 full left to 100 full right, and its pitch in semitones, from -24 to 24. Leave a value at 0 to keep what the pack says.

### Text replacements

A replacement turns a shortcut word into text as you type. Add a replacement from myemail to your email address, and when you finish myemail with a space, punctuation or Enter, Typix puts your address there. A shortcut must be one word.

Replacements and automatic capitals don't run in terminals, where a capital would break commands: git would become Git. Spelling beeps and the spelling command still work there.

Two checkboxes add automatic capitals: one for the first word of a sentence, and one for a lone i, including i'm, i'll, i've and i'd. A dot inside a chunk like e.g. or a file name doesn't count as the end of a sentence.

When Typix replaces something, it says the new text if NVDA's Speak typed words is on. It then plays the Automatic replacement sound, or a short beep if the pack has none.

To make the change, Typix selects the word, checks that it really is that word, and types the new text over it. NVDA doesn't read out the selection, and anything else it has to say comes through as usual. If Typix can't select the word safely, the text stays as it was. Ctrl+Z in the app undoes a replacement you didn't want, though some apps need more than one press.

Typix never uses the clipboard. A typed Enter or Tab could send a message in a chat app, so a word you end with Enter or Tab isn't replaced or capitalized.

Replacements are saved in NVDA's configuration, so a configuration profile can have its own set, and Typix follows the profile when NVDA switches to it.

### Spelling

Beep on misspelled words as you type checks each word you finish against a dictionary for your current keyboard language, then plays the pack's misspelled sound, or a beep if the pack has none. Typix reads the finished word from the text field, so a word you corrected with Backspace is checked whole. This works with typing sounds turned off.

Typix doesn't check single letters, words containing digits, links, email addresses, file names, code like snake_case or camelCase, or password fields.

The first time you use a language, Typix downloads its dictionary from LibreOffice's dictionaries on GitHub. This is the only time Typix goes online, and what you type is never sent anywhere.

Custom dictionary lists the words you've marked as correct. You can also open it from the NVDA menu, under Tools, Typix.

### Whole word echo

When NVDA's Speak typed words is on, NVDA normally says only the letters typed since your last correction. Type "trying", backspace to "t", type "rying" and a space, and NVDA says "rying". Typix reads the finished word from the field and says "trying". Words with an apostrophe, like can't, are spoken and checked as one word; NVDA on its own says "can" and then "t".

This has no setting of its own. It follows NVDA's Speak typed words option, and it leaves terminals, password fields and anything it can't read to NVDA.

### Password fields

In a password field every character key plays the same plain letter sound, centred, with no pitch change, so nobody listening, and no recording, can tell which character you typed. Backspace, Delete, Enter and Tab keep their own sounds. Passwords are never spellchecked or replaced.

## Commands

Typix has two commands, in the Typix category of NVDA's Input gestures dialog. Neither has a key by default.

Toggles Typix typing sounds on and off does what it says, without opening settings.

The second command handles spelling and statistics, and what it does depends on where you are.

With text selected, it checks the selection word by word and offers suggestions for each misspelling, then corrects each word in place. Escape ends the check, and corrections you already chose are still applied. If the selection changed while the check was open, Typix changes nothing.

With nothing selected, if your last misspelled word is still on the caret's line or the line above, it offers suggestions for that word. Pick one and Typix selects that word, checks it, and replaces it. This works after you've typed more, and after switching windows and coming back.

Otherwise, including in windows with no text, it opens your typing statistics.

The suggestions list always opens in front, like NVDA's own dialogs. Each suggestion says what was wrong, like "hello, missing l", "the, e and h swapped" or "help, p instead of o". Suggestions follow the word's case, so Helo suggests Hello. The list also offers Add to dictionary and Skip.

### Typing statistics

Statistics open in a window with a read-only text field and a Close button. Read the field with the arrow keys, and press Escape to close it.

The first line sums things up: your current speed, today's average, words and misspellings today, your average over all days, and your best speed. Below it are sections for today, the last 7 days and all time, each with words, characters typed, characters deleted, misspellings, time spent typing and average speed. The all time section also gives the number of days with typing, your best speed and your busiest day. The last section has one line for each of your 7 most recent days with typing.

Speed is net words per minute, counting five characters as a word, as typing tests do. Characters you deleted with Backspace are taken off, and a key held down to auto-repeat isn't counted. Pauses longer than five seconds don't count as typing time, so the number reads a little higher than a typing test that counts every second. Your best speed only counts stretches of at least 50 keys.

Statistics are kept from day to day in typix_stats.json in your NVDA configuration folder. Only counts are saved, never what you typed.

## Pack editor

The editor changes nothing on disk until you press OK.

### Details

Name, author and description of the pack.

### Category sounds

Pick a category, then use these controls:

* Sound file and Play: hear each sound in the category.
* Replace sounds: choose one or more WAV files to replace what's there.
* Add variation: add more WAV files. When a category has several, Typix picks one at random each time.
* Remove sound: take the selected file out.

| Category | Played for | If the pack has no sound for it |
|----------|------------|----------------------------------|
| Letters | letters and digits | silent |
| Capital letters | capital letters, on top of the letter sound | only the letter sound plays |
| Space | the space bar | letter sound |
| Enter | Enter | letter sound |
| Tab | Tab | letter sound |
| Backspace | Backspace | Delete sound, then letter sound |
| Delete | Delete | letter sound |
| Punctuation and symbols | punctuation, currency and other symbols | letter sound |
| Misspelled word | a finished word that's misspelled | a short beep |
| Automatic replacement | a text replacement or automatic capital | a short beep |

### Characters

One character can have its own sound, pan and pitch.

* Add character: asks for the character, then for its sound files. Cancel the file picker to give it only effects and keep its category sound.
* Choose sound: change the selected character's sound files.
* Set effects: pan and pitch for the character.
* Preview: hear the character with its effects.
* Remove character: drop its sound and effects.

Sound files you add are copied into the pack folder. Files the pack no longer uses are deleted when you press OK.

## Pack format

A pack is a folder with a manifest.json and some WAV files. Typix reads 8, 16, 24 and 32 bit WAV files at any sample rate, mono or stereo.

```json
{
    "name": "My Pack",
    "author": "Your Name",
    "version": "1.0",
    "description": "A typing sound pack",
    "files": {
        "LETTER": ["letter_1.wav", "letter_2.wav"],
        "CAPS_LETTER": "caps.wav",
        "SPACE": "space.wav",
        "ENTER": "enter.wav",
        "TAB": "tab.wav",
        "BACKSPACE": "backspace.wav",
        "DELETE": "delete.wav",
        "PUNCTUATION": "punctuation.wav",
        "MISSPELLED": "misspelled.wav",
        "REPLACED": "replaced.wav"
    },
    "char_sounds": {",": "comma.wav"},
    "char_panning": {",": -0.5, ".": 0.5},
    "char_pitch": {",": -2.0}
}
```

* Every field is optional. A missing category borrows a sound as the table above says.
* A file name or a list of file names works anywhere a sound goes. Sound files must be inside the pack folder.
* char_panning runs from -1.0, full left, to 1.0, full right. char_pitch is in semitones, from -24 to 24.
* Unknown fields are kept and ignored.
* A pack zip can hold the pack in one folder, or have manifest.json at its root, in which case the pack is named after the zip.

## Troubleshooting

* No sound at all: check that Play typing sounds is on, the volume isn't 0, and the pack has sounds.
* Silent in one app: Typix only plays in editable text fields, and relies on the app reporting them through accessibility. If an app reports its text field in an unusual way, Typix can't hear it yet. Please report which app.
* A pack doesn't show up: its folder needs a valid manifest.json and at least one non-empty sound file inside the pack.
* No misspelling beeps: the dictionary for your keyboard language may still be downloading, or there may be none for that language. If a download fails, Typix tries again an hour later. The NVDA log has the details.

## Author

Copyright (C) 2025 OlegTheSnowman
