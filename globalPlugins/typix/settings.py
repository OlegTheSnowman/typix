import os
import random
import logging

import addonHandler
import characterProcessing
import languageHandler
import gui
import nvwave
import wx
from gui import guiHelper, nvdaControls

from . import config as addon_config
from . import spellcheck as addon_spellcheck
from .sound_loader import CATEGORIES, char_category

log = logging.getLogger(__name__)

try:
    addonHandler.initTranslation()
except addonHandler.AddonError:
    pass

# Spoken names for the sound categories, in CATEGORIES order.
CATEGORY_LABELS = {
    # Translators: Name of a sound category: lowercase letters and digits.
    "LETTER": _("Letters"),
    # Translators: Name of a sound category: capital letters.
    "CAPS_LETTER": _("Capital letters"),
    # Translators: Name of a sound category: the space bar.
    "SPACE": _("Space"),
    # Translators: Name of a sound category: the Enter key.
    "ENTER": _("Enter"),
    # Translators: Name of a sound category: the Tab key.
    "TAB": _("Tab"),
    # Translators: Name of a sound category: the Backspace key.
    "BACKSPACE": _("Backspace"),
    # Translators: Name of a sound category: the Delete key.
    "DELETE": _("Delete"),
    # Translators: Name of a sound category: punctuation, currency and other symbols.
    "PUNCTUATION": _("Punctuation and symbols"),
    # Translators: Name of a sound category: the alert played after a misspelled word.
    "MISSPELLED": _("Misspelled word"),
    # Translators: Name of a sound category: played when a text replacement rule changes what you typed.
    "REPLACED": _("Automatic replacement"),
}


def _get_plugin():
    import globalPluginHandler
    for plugin in globalPluginHandler.runningPlugins:
        if hasattr(plugin, "sound_loader"):
            return plugin
    return None


def _play(path, pan=0.0, pitch=0.0):
    """Previews a sound through Typix's own engine, so pan and pitch are heard."""
    if not path or not os.path.isfile(path):
        wx.Bell()
        return
    plugin = _get_plugin()
    if plugin:
        plugin.audio_engine.play_sound(path, pan=pan, pitch=pitch)
    else:
        nvwave.playWaveFile(path, asynchronous=True)


def _describe_char(char):
    """How NVDA speaks a character, so a comma reads as "comma", not as
    a quoted symbol."""
    try:
        name = characterProcessing.processSpeechSymbol(languageHandler.getLanguage(), char)
    except Exception:
        name = char
    if name == char and char.isupper():
        # Translators: How a capital letter is named in character lists. {char} is the letter.
        name = _("cap {char}").format(char=char)
    return name or repr(char)


def _describe_effects(pan, pitch):
    parts = []
    if abs(pan) >= 0.005:
        if pan < 0:
            # Translators: Direction of a sound in stereo, used in the pan description below.
            side = _("left")
        else:
            # Translators: Direction of a sound in stereo, used in the pan description below.
            side = _("right")
        # Translators: Describes a character's stereo position. {side} is left or right, {amount} is 1 to 100.
        parts.append(_("pan {side} {amount}").format(side=side, amount=round(abs(pan) * 100)))
    if abs(pitch) >= 0.05:
        # Translators: Describes a character's pitch change. {semitones} is a signed number like +2.0.
        parts.append(_("pitch {semitones:+.1f} semitones").format(semitones=pitch))
    # Translators: Shown for a character with neither pan nor pitch set.
    return ", ".join(parts) or _("no effects")


def _selected(listbox, keys):
    """The key behind a list's selection, or None with a bell."""
    idx = listbox.GetSelection()
    if idx == wx.NOT_FOUND or idx >= len(keys):
        wx.Bell()
        return None
    return keys[idx]


def _ask_char(parent):
    """Asks for exactly one character. Returns it, or None if cancelled."""
    with wx.TextEntryDialog(
        parent,
        # Translators: Prompt of the dialog that asks for a single character.
        _("Type one character, for example , or . or @:"),
        # Translators: Title of dialogs about choosing a single character.
        _("Character"),
    ) as dlg:
        while dlg.ShowModal() == wx.ID_OK:
            value = dlg.GetValue()
            if len(value) == 1:
                return value
            if len(value.strip()) == 1:
                return value.strip()
            gui.messageBox(
                # Translators: Error when more or less than one character was typed.
                _("Please enter exactly one character."),
                # Translators: Title of dialogs about choosing a single character.
                _("Character"),
                wx.OK | wx.ICON_ERROR, parent,
            )
    return None


def _group(parent, label):
    """A labelled group. Controls inside must use the returned box as their
    parent: NVDA's helper already parents labelled controls to it, and
    mixing parents scrambles the Tab order."""
    sizer = wx.StaticBoxSizer(wx.VERTICAL, parent, label=label)
    return guiHelper.BoxSizerHelper(parent, sizer=sizer), sizer.GetStaticBox()


def _pick_wavs(parent, message):
    with wx.FileDialog(
        # Translators: File type filter in the open file dialog. Keep the |*.wav part unchanged.
        parent, message=message, wildcard=_("WAV files (*.wav)|*.wav"),
        style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
    ) as dlg:
        return list(dlg.GetPaths()) if dlg.ShowModal() == wx.ID_OK else []


class EffectsDialog(wx.Dialog):
    """Pan and pitch for one character. Used by both the pack editor and
    the global character rules."""

    def __init__(self, parent, char, pan, pitch):
        # Translators: Title of the pan and pitch dialog. {char} is the character's spoken name.
        super().__init__(parent, title=_("Effects for {char}").format(char=_describe_char(char)))
        main = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
        self.panCtrl = helper.addLabeledControl(
            # Translators: Label of the pan field in the effects dialog.
            _("&Pan, from -100 full left through 0 centre to 100 full right:"),
            nvdaControls.SelectOnFocusSpinCtrl, min=-100, max=100, initial=round(pan * 100),
        )
        self.pitchCtrl = helper.addLabeledControl(
            # Translators: Label of the pitch field in the effects dialog.
            _("P&itch in semitones, negative is lower:"),
            wx.SpinCtrlDouble, min=-24, max=24, initial=pitch, inc=0.5,
        )
        self.pitchCtrl.SetDigits(1)
        helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
        main.Add(helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
        self.SetSizerAndFit(main)
        self.CentreOnParent()
        self.panCtrl.SetFocus()

    @property
    def values(self):
        return self.panCtrl.GetValue() / 100.0, self.pitchCtrl.GetValue()


class ExpansionDialog(wx.Dialog):
    """A shortcut word and the text it becomes, for adding and editing text
    replacements."""

    def __init__(self, parent, shortcut, text):
        # Translators: Title of the dialog for adding or editing a text replacement.
        super().__init__(parent, title=_("Text replacement"))
        main = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
        # Translators: Label of the field for the word you type to trigger a text replacement.
        self.shortcutCtrl = helper.addLabeledControl(_("&Shortcut, one word you type:"), wx.TextCtrl, value=shortcut)
        # Translators: Label of the field for the text a shortcut becomes.
        self.textCtrl = helper.addLabeledControl(_("&Becomes:"), wx.TextCtrl, value=text)
        helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        main.Add(helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
        self.SetSizerAndFit(main)
        self.CentreOnParent()
        self.shortcutCtrl.SetFocus()

    @property
    def values(self):
        return self.shortcutCtrl.GetValue().strip(), self.textCtrl.GetValue()

    def _on_ok(self, event):
        shortcut, text = self.values
        # A shortcut must be what Typix sees as one finished word.
        if not shortcut or addon_spellcheck.trailing_word(shortcut) != shortcut or not text:
            gui.messageBox(
                # Translators: Error when a text replacement's shortcut isn't a single word or the text is empty.
                _("The shortcut must be a single word, letters and digits only, and the text can't be empty."),
                # Translators: Title of the dialog for adding or editing a text replacement.
                _("Text replacement"), wx.OK | wx.ICON_ERROR, self,
            )
            return
        self.EndModal(wx.ID_OK)


class PackEditorDialog(wx.Dialog):
    """Edits a sound pack's details, category sounds and per-character
    sounds and effects. Nothing touches the disk until OK."""

    def __init__(self, parent, loader, dir_name):
        pack = loader.available_packs.get(dir_name)
        name = pack.name if pack else dir_name
        super().__init__(
            # Translators: Title of the sound pack editor. {name} is the pack name.
            parent, title=_("Edit sound pack {name}").format(name=name),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.loader = loader
        self.dir_name = dir_name
        self.cat_sounds = {c: list(p) for c, p in pack.sounds.items()} if pack else {}
        self.char_sounds = {c: list(p) for c, p in pack.char_sounds.items()} if pack else {}
        self.char_panning = dict(pack.char_panning) if pack else {}
        self.char_pitch = dict(pack.char_pitch) if pack else {}

        main = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

        # Translators: Group in the pack editor holding the pack's name, author and description.
        details, box = _group(self, _("Details"))
        # Translators: Label of the pack name field.
        self.nameCtrl = details.addLabeledControl(_("&Name:"), wx.TextCtrl, value=name)
        # Translators: Label of the pack author field.
        self.authorCtrl = details.addLabeledControl(_("&Author:"), wx.TextCtrl, value=pack.author if pack else "")
        self.descriptionCtrl = details.addLabeledControl(
            # Translators: Label of the pack description field.
            _("&Description:"), wx.TextCtrl, value=pack.description if pack else "",
            style=wx.TE_MULTILINE, size=(-1, 60),
        )
        helper.addItem(details)

        # Translators: Group in the pack editor for the sounds of each category.
        sounds, box = _group(self, _("Category sounds"))
        # Translators: Label of the list of sound categories in the pack editor.
        self.categoryList = sounds.addLabeledControl(_("Ca&tegory:"), wx.ListBox, size=(-1, 160))
        self.categoryList.Bind(wx.EVT_LISTBOX, lambda e: self._refresh_variations())
        # Translators: Label of the list of sound files in the selected category.
        self.variationChoice = sounds.addLabeledControl(_("Sound &file:"), wx.Choice)
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that plays the selected sound file.
        buttons.addButton(box, label=_("&Play")).Bind(wx.EVT_BUTTON, self._on_play_variation)
        # Translators: Button that replaces all sounds of the selected category with chosen files.
        buttons.addButton(box, label=_("&Replace sounds...")).Bind(wx.EVT_BUTTON, self._on_replace_sounds)
        # Translators: Button that adds more sound files to the selected category.
        buttons.addButton(box, label=_("Add &variation...")).Bind(wx.EVT_BUTTON, self._on_add_variation)
        # Translators: Button that removes the selected sound file from the category.
        buttons.addButton(box, label=_("Remove &sound")).Bind(wx.EVT_BUTTON, self._on_remove_variation)
        sounds.addItem(buttons)
        helper.addItem(sounds)

        # Translators: Group in the pack editor for per-character sounds and effects.
        chars, box = _group(self, _("Characters"))
        # Translators: Label of the list of characters with their own sound or effects.
        self.charList = chars.addLabeledControl(_("C&haracter:"), wx.ListBox, size=(-1, 140))
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that adds a character with its own sound or effects.
        buttons.addButton(box, label=_("Add &character...")).Bind(wx.EVT_BUTTON, self._on_add_char)
        # Translators: Button that chooses sound files for the selected character.
        buttons.addButton(box, label=_("Choose s&ound...")).Bind(wx.EVT_BUTTON, self._on_char_sound)
        # Translators: Button that opens the pan and pitch dialog for the selected character.
        buttons.addButton(box, label=_("Set &effects...")).Bind(wx.EVT_BUTTON, self._on_char_effects)
        # Translators: Button that plays the selected character with its pan and pitch.
        buttons.addButton(box, label=_("Previe&w")).Bind(wx.EVT_BUTTON, self._on_char_preview)
        # Translators: Button that removes the selected character's sound and effects.
        buttons.addButton(box, label=_("Re&move character")).Bind(wx.EVT_BUTTON, self._on_remove_char)
        chars.addItem(buttons)
        helper.addItem(chars)

        helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        main.Add(helper.sizer, 1, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND)
        self.SetSizerAndFit(main)

        self._refresh_categories(0)
        self._refresh_chars()
        self.CentreOnParent()
        self.nameCtrl.SetFocus()

    # --- Category sounds ---

    def _refresh_categories(self, select):
        items = []
        for cat in CATEGORIES:
            n = len(self.cat_sounds.get(cat, []))
            if n == 0:
                # Translators: Shown next to a category with no sound files.
                count = _("no sound")
            else:
                # Translators: Number of sound files in a category. {n} is the number.
                count = ngettext("{n} sound", "{n} sounds", n).format(n=n)
            items.append(f"{CATEGORY_LABELS[cat]}: {count}")
        self.categoryList.Set(items)
        self.categoryList.SetSelection(select)
        self._refresh_variations()

    def _refresh_variations(self):
        cat = CATEGORIES[self.categoryList.GetSelection()]
        self.variationChoice.Set([os.path.basename(p) for p in self.cat_sounds.get(cat, [])])
        if self.variationChoice.GetCount():
            self.variationChoice.SetSelection(0)

    def _current_variation(self):
        cat = CATEGORIES[self.categoryList.GetSelection()]
        paths = self.cat_sounds.get(cat, [])
        return cat, _selected(self.variationChoice, list(range(len(paths))))

    def _on_play_variation(self, event):
        cat, idx = self._current_variation()
        if idx is not None:
            _play(self.cat_sounds[cat][idx])

    def _change_category(self, replace):
        idx = self.categoryList.GetSelection()
        cat = CATEGORIES[idx]
        # Translators: Title of the file dialog for choosing sounds. {category} is a category name.
        paths = _pick_wavs(self, _("Choose sounds for {category}").format(category=CATEGORY_LABELS[cat]))
        if paths:
            self.cat_sounds[cat] = paths if replace else self.cat_sounds.get(cat, []) + paths
            self._refresh_categories(idx)

    def _on_replace_sounds(self, event):
        self._change_category(replace=True)

    def _on_add_variation(self, event):
        self._change_category(replace=False)

    def _on_remove_variation(self, event):
        cat, idx = self._current_variation()
        if idx is not None:
            del self.cat_sounds[cat][idx]
            self._refresh_categories(self.categoryList.GetSelection())

    # --- Characters ---

    def _refresh_chars(self, select=None):
        self._char_keys = sorted(set(self.char_sounds) | set(self.char_panning) | set(self.char_pitch))
        items = []
        for c in self._char_keys:
            paths = self.char_sounds.get(c)
            # Translators: Shown for a character without its own sound, which uses its category's sound.
            sound = ", ".join(os.path.basename(p) for p in paths) if paths else _("category sound")
            effects = _describe_effects(self.char_panning.get(c, 0.0), self.char_pitch.get(c, 0.0))
            items.append(f"{_describe_char(c)}: {sound}, {effects}")
        self.charList.Set(items)
        if select in self._char_keys:
            self.charList.SetSelection(self._char_keys.index(select))
        elif self._char_keys:
            self.charList.SetSelection(0)

    def _on_add_char(self, event):
        char = _ask_char(self)
        if char is None:
            return
        if char not in self._char_keys:
            # Translators: Title of the file dialog for choosing sounds. {char} is the character's spoken name.
            paths = _pick_wavs(self, _("Choose sounds for {char}").format(char=_describe_char(char)))
            if paths:
                self.char_sounds[char] = paths
            else:
                self.char_panning.setdefault(char, 0.0)
        self._refresh_chars(char)

    def _on_char_sound(self, event):
        char = _selected(self.charList, self._char_keys)
        if char is not None:
            # Translators: Title of the file dialog for choosing sounds. {char} is the character's spoken name.
            paths = _pick_wavs(self, _("Choose sounds for {char}").format(char=_describe_char(char)))
            if paths:
                self.char_sounds[char] = paths
                self._refresh_chars(char)

    def _on_char_effects(self, event):
        char = _selected(self.charList, self._char_keys)
        if char is None:
            return
        with EffectsDialog(self, char, self.char_panning.get(char, 0.0), self.char_pitch.get(char, 0.0)) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.char_panning[char], self.char_pitch[char] = dlg.values
                self._refresh_chars(char)

    def _on_char_preview(self, event):
        char = _selected(self.charList, self._char_keys)
        if char is None:
            return
        paths = self.char_sounds.get(char)
        if not paths:
            cat = char_category(char)
            paths = self.cat_sounds.get(cat) or self.cat_sounds.get("LETTER")
        _play(random.choice(paths) if paths else None,
              self.char_panning.get(char, 0.0), self.char_pitch.get(char, 0.0))

    def _on_remove_char(self, event):
        char = _selected(self.charList, self._char_keys)
        if char is not None:
            for mapping in (self.char_sounds, self.char_panning, self.char_pitch):
                mapping.pop(char, None)
            self._refresh_chars()

    def _on_ok(self, event):
        errors = self.loader.save_pack(
            self.dir_name,
            self.nameCtrl.GetValue().strip(),
            self.authorCtrl.GetValue().strip(),
            self.descriptionCtrl.GetValue().strip(),
            self.cat_sounds, self.char_sounds, self.char_panning, self.char_pitch,
        )
        if errors:
            gui.messageBox(
                # Translators: Message after saving a pack when some files failed. A list of errors follows.
                _("Some sounds could not be saved:") + "\n" + "\n".join(errors),
                # Translators: Title of message boxes about a sound pack.
                _("Sound pack"), wx.OK | wx.ICON_WARNING, self,
            )
        self.loader.scan_packs()
        self.EndModal(wx.ID_OK)


class ManageDictionaryDialog(wx.Dialog):
    """Browse, add and remove words in Typix's custom spelling dictionary."""

    def __init__(self, parent):
        # Translators: Title of the custom spelling dictionary dialog.
        super().__init__(parent, title=_("Typix custom dictionary"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        main = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
        # Translators: Label of the list of words in the custom dictionary.
        self.wordList = helper.addLabeledControl(_("&Words:"), wx.ListBox, size=(360, 260))
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that adds a word to the custom dictionary.
        buttons.addButton(self, label=_("&Add word...")).Bind(wx.EVT_BUTTON, self._on_add)
        # Translators: Button that removes the selected word from the custom dictionary.
        buttons.addButton(self, label=_("&Remove word")).Bind(wx.EVT_BUTTON, self._on_remove)
        helper.addItem(buttons)
        helper.addDialogDismissButtons(wx.CLOSE)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.SetEscapeId(wx.ID_CLOSE)
        main.Add(helper.sizer, 1, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND)
        self.SetSizerAndFit(main)
        self._refresh()
        self.CentreOnParent()
        self.wordList.SetFocus()

    def _refresh(self, select=None):
        self._entries = addon_spellcheck.get_custom_words()
        self.wordList.Set([f"{word} ({lang})" for word, lang in self._entries])
        if self._entries:
            self.wordList.SetSelection(min(select or 0, len(self._entries) - 1))

    def _on_add(self, event):
        with wx.TextEntryDialog(
            self,
            # Translators: Prompt of the dialog that asks for a word to add to the dictionary.
            _("Word to add:"),
            # Translators: Title of the dialog that asks for a word to add to the dictionary.
            _("Add word"),
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK or not dlg.GetValue().strip():
                return
            word = dlg.GetValue().strip()
        langs = [lang for _w, lang in self._entries]
        default = max(set(langs), key=langs.count) if langs else "en_US"
        with wx.TextEntryDialog(
            self,
            # Translators: Prompt of the dialog that asks for the word's language code.
            _("Language of the word, for example en_US, en_GB or fr_FR:"),
            # Translators: Title of the dialog that asks for the word's language code.
            _("Language"),
            default,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK or not dlg.GetValue().strip():
                return
            lang = dlg.GetValue().strip()
        addon_spellcheck.add_custom_word(word, lang)
        self._refresh()

    def _on_remove(self, event):
        entry = _selected(self.wordList, self._entries)
        if entry is not None:
            idx = self.wordList.GetSelection()
            addon_spellcheck.remove_custom_word(*entry)
            self._refresh(idx)


class TypixSettingsPanel(gui.settingsDialogs.SettingsPanel):
    # Translators: Title of the Typix category in NVDA's settings dialog.
    title = _("Typix")

    def makeSettings(self, settingsSizer):
        get = addon_config.get_config
        helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        self._loader = _get_plugin().sound_loader

        # Translators: Checkbox that turns all typing sounds on or off.
        self.enabledCheckbox = helper.addItem(wx.CheckBox(self, label=_("&Play typing sounds")))
        self.enabledCheckbox.SetValue(get("enabled"))

        # Translators: Group in Typix settings for volume, panning and sound categories.
        sounds, box = _group(self, _("Sounds"))
        self.volumeSlider = sounds.addLabeledControl(
            # Translators: Label of the typing sound volume slider.
            _("&Volume:"), nvdaControls.EnhancedInputSlider,
            value=round(get("master_volume") * 100), minValue=0, maxValue=100,
        )
        # Translators: Checkbox that lets each key sound from its own place in stereo.
        self.panningCheckbox = sounds.addItem(wx.CheckBox(box, label=_("Stereo pannin&g per key")))
        self.panningCheckbox.SetValue(get("panning_enabled"))
        self.panningWidthSlider = sounds.addLabeledControl(
            # Translators: Label of the slider for how far sounds can pan left and right.
            _("Panning &width:"), nvdaControls.EnhancedInputSlider,
            value=round(get("panning_width") * 100), minValue=0, maxValue=100,
        )
        self.debounceCtrl = sounds.addLabeledControl(
            # Translators: Label of the field that ignores fast repeats of the same key.
            _("Key repeat &filter, ignores the same key again within this many milliseconds, 0 is off:"),
            nvdaControls.SelectOnFocusSpinCtrl, min=0, max=200, initial=get("debounce_ms"),
        )
        self.categoryList = sounds.addLabeledControl(
            # Translators: Label of the checklist of sound categories that play.
            _("Play sounds f&or:"), nvdaControls.CustomCheckListBox,
            choices=[CATEGORY_LABELS[c] for c in CATEGORIES],
        )
        cats = get("categories")
        self.categoryList.SetCheckedItems([i for i, c in enumerate(CATEGORIES) if cats[c]])
        self.categoryList.SetSelection(0)
        helper.addItem(sounds)

        # Translators: Group in Typix settings for choosing and managing sound packs.
        packs, box = _group(self, _("Sound pack"))
        self._pack_keys = []
        # Translators: Label of the list of installed sound packs.
        self.packChoice = packs.addLabeledControl(_("Sound pac&k:"), wx.Choice)
        self.packChoice.Bind(wx.EVT_CHOICE, lambda e: self._refresh_preview())
        # Translators: Label of the list of sounds in the selected pack.
        self.previewChoice = packs.addLabeledControl(_("Pr&eview sound:"), wx.Choice)
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that plays the selected preview sound.
        buttons.addButton(box, label=_("Pla&y")).Bind(wx.EVT_BUTTON, self._on_play_preview)
        # Translators: Button that opens the editor for the selected pack.
        buttons.addButton(box, label=_("Edi&t pack...")).Bind(wx.EVT_BUTTON, self._on_edit_pack)
        # Translators: Button that creates a new sound pack.
        buttons.addButton(box, label=_("&New pack...")).Bind(wx.EVT_BUTTON, self._on_new_pack)
        packs.addItem(buttons)
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that installs a sound pack from a zip file.
        buttons.addButton(box, label=_("&Import pack...")).Bind(wx.EVT_BUTTON, self._on_import_pack)
        # Translators: Button that saves the selected pack as a zip file.
        buttons.addButton(box, label=_("E&xport pack...")).Bind(wx.EVT_BUTTON, self._on_export_pack)
        # Translators: Button that deletes the selected pack.
        buttons.addButton(box, label=_("&Delete pack")).Bind(wx.EVT_BUTTON, self._on_delete_pack)
        # Translators: Button that opens the sound packs folder in File Explorer.
        buttons.addButton(box, label=_("Open packs folder")).Bind(wx.EVT_BUTTON, self._on_open_folder)
        packs.addItem(buttons)
        helper.addItem(packs)
        self._refresh_packs(get("current_pack"))

        # Translators: Group in Typix settings for pan and pitch rules that apply to every pack.
        rules, box = _group(self, _("Character rules for every pack"))
        self._rules = addon_config.get_json("global_char_rules_json")
        # Translators: Label of the list of character rules.
        self.rulesList = rules.addLabeledControl(_("R&ules:"), wx.ListBox, size=(-1, 100))
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that adds a character rule.
        buttons.addButton(box, label=_("Add rule...")).Bind(wx.EVT_BUTTON, self._on_add_rule)
        # Translators: Button that edits the selected character rule.
        buttons.addButton(box, label=_("Edit rule...")).Bind(wx.EVT_BUTTON, self._on_edit_rule)
        # Translators: Button that removes the selected character rule.
        buttons.addButton(box, label=_("Remove rule")).Bind(wx.EVT_BUTTON, self._on_remove_rule)
        rules.addItem(buttons)
        helper.addItem(rules)
        self._refresh_rules()

        # Translators: Group in Typix settings for text replacements and automatic capitals.
        replacing, box = _group(self, _("Text replacements"))
        self._expansions = addon_config.get_json("expansions_json")
        # Translators: Label of the list of text replacements, each a shortcut and the text it becomes.
        self.expansionsList = replacing.addLabeledControl(_("Replace&ments:"), wx.ListBox, size=(-1, 100))
        buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
        # Translators: Button that adds a text replacement.
        buttons.addButton(box, label=_("Add replacement...")).Bind(wx.EVT_BUTTON, self._on_add_expansion)
        # Translators: Button that edits the selected text replacement.
        buttons.addButton(box, label=_("Edit replacement...")).Bind(wx.EVT_BUTTON, self._on_edit_expansion)
        # Translators: Button that removes the selected text replacement.
        buttons.addButton(box, label=_("Remove replacement")).Bind(wx.EVT_BUTTON, self._on_remove_expansion)
        replacing.addItem(buttons)
        self.capitalizeSentencesCheckbox = replacing.addItem(
            # Translators: Checkbox that capitalises the first word of each sentence as you type.
            wx.CheckBox(box, label=_("Capitali&ze the first word of a sentence")),
        )
        self.capitalizeSentencesCheckbox.SetValue(get("capitalize_sentences"))
        self.capitalizeICheckbox = replacing.addItem(
            # Translators: Checkbox that turns a lone lowercase i, and i'm, i'll and so on, into a capital I.
            wx.CheckBox(box, label=_("Capital I when i stands a&lone")),
        )
        self.capitalizeICheckbox.SetValue(get("capitalize_i"))
        helper.addItem(replacing)
        self._refresh_expansions()

        # Translators: Group in Typix settings for spelling options.
        spelling, box = _group(self, _("Spelling"))
        self.spellcheckCheckbox = spelling.addItem(
            # Translators: Checkbox that plays an alert after typing a misspelled word.
            wx.CheckBox(box, label=_("Beep on mi&sspelled words as you type")),
        )
        self.spellcheckCheckbox.SetValue(get("spellcheck_enabled"))
        # Translators: Button that opens the custom spelling dictionary.
        spelling.addItem(wx.Button(box, label=_("&Custom dictionary..."))).Bind(
            wx.EVT_BUTTON, lambda e: self._show(ManageDictionaryDialog(self)),
        )
        helper.addItem(spelling)

    def _show(self, dlg):
        with dlg:
            return dlg.ShowModal()

    # --- Packs ---

    def _refresh_packs(self, select_key=None):
        """Rescans and refills the pack list, keeping select_key selected."""
        if select_key is None:
            select_key = self._selected_pack(bell=False)
        self._loader.scan_packs()
        self._pack_keys = sorted(self._loader.available_packs)
        labels = []
        for key in self._pack_keys:
            pack = self._loader.available_packs[key]
            label = pack.name
            if pack.author:
                # Translators: A pack in the pack list. {name} is the pack name, {author} its author.
                label = _("{name} by {author}").format(name=label, author=pack.author)
            if pack.description:
                desc = pack.description if len(pack.description) <= 80 else pack.description[:77] + "..."
                label = f"{label}: {desc}"
            labels.append(label)
        self.packChoice.Set(labels)
        if self._pack_keys:
            idx = self._pack_keys.index(select_key) if select_key in self._pack_keys else 0
            self.packChoice.SetSelection(idx)
        self._refresh_preview()

    def _selected_pack(self, bell=True):
        idx = self.packChoice.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._pack_keys):
            if bell:
                wx.Bell()
            return None
        return self._pack_keys[idx]

    def _refresh_preview(self):
        self._preview_paths = []
        labels = []
        key = self._selected_pack(bell=False)
        pack = self._loader.available_packs.get(key) if key else None
        if pack:
            for cat in CATEGORIES:
                for path in pack.sounds.get(cat, []):
                    labels.append(f"{CATEGORY_LABELS[cat]}: {os.path.basename(path)}")
                    self._preview_paths.append(path)
        self.previewChoice.Set(labels)
        if labels:
            self.previewChoice.SetSelection(0)

    def _on_play_preview(self, event):
        path = _selected(self.previewChoice, self._preview_paths)
        if path:
            _play(path)

    def _edit(self, key):
        if self._show(PackEditorDialog(self, self._loader, key)) == wx.ID_OK:
            _get_plugin().audio_engine.clear_cache()
        self._refresh_packs(key)

    def _on_edit_pack(self, event):
        key = self._selected_pack()
        if key:
            self._edit(key)

    def _on_new_pack(self, event):
        with wx.TextEntryDialog(
            self,
            # Translators: Prompt of the dialog that asks for a new pack's name.
            _("Name of the new sound pack:"),
            # Translators: Title of dialogs about creating a new sound pack.
            _("New sound pack"),
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK or not dlg.GetValue().strip():
                return
            name = dlg.GetValue().strip()
        key = self._loader.create_pack(name)
        if not key:
            gui.messageBox(
                # Translators: Error when a new pack can't be created.
                _("Could not create the pack. A pack with that name may already exist."),
                # Translators: Title of the error message when a new pack can't be created.
                _("New sound pack"), wx.OK | wx.ICON_ERROR, self,
            )
            return
        self._loader.scan_packs()
        self._edit(key)

    def _on_open_folder(self, event):
        os.startfile(self._loader.soundpacks_root)

    def _on_export_pack(self, event):
        key = self._selected_pack()
        if not key:
            return
        with wx.FileDialog(
            # Translators: Title of the file dialog for exporting a pack.
            self, message=_("Export sound pack"), defaultFile=f"{key}.zip",
            # Translators: File type filter in the save file dialog. Keep the |*.zip part unchanged.
            wildcard=_("ZIP files (*.zip)|*.zip"), style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            dest = dlg.GetPath()
        if self._loader.export_pack(key, dest):
            gui.messageBox(
                # Translators: Message after a pack was exported.
                _("Pack exported."),
                # Translators: Title of message boxes about exporting a pack.
                _("Export"),
                wx.OK | wx.ICON_INFORMATION, self,
            )
        else:
            gui.messageBox(
                # Translators: Message after exporting a pack failed.
                _("Export failed."),
                # Translators: Title of message boxes about exporting a pack.
                _("Export"),
                wx.OK | wx.ICON_ERROR, self,
            )

    def _on_import_pack(self, event):
        with wx.FileDialog(
            self,
            # Translators: Title of the file dialog for importing a pack.
            message=_("Import sound pack"),
            # Translators: File type filter in the open file dialog. Keep the |*.zip part unchanged.
            wildcard=_("ZIP files (*.zip)|*.zip"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            zip_path = dlg.GetPath()
        status, info = self._loader.import_pack(zip_path)
        if status == "exists":
            if gui.messageBox(
                # Translators: Asks whether to replace an existing pack on import. {name} is the pack folder name.
                _("A pack named {name} already exists. Replace it?").format(name=info),
                # Translators: Title of message boxes about importing a pack.
                _("Import"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self,
            ) != wx.YES:
                return
            status, info = self._loader.import_pack(zip_path, override=True)
        if status is True:
            _get_plugin().audio_engine.clear_cache()
            self._refresh_packs(info)
            gui.messageBox(
                # Translators: Message after a pack was imported. {name} is the pack folder name.
                _("Pack {name} imported.").format(name=info),
                # Translators: Title of message boxes about importing a pack.
                _("Import"),
                wx.OK | wx.ICON_INFORMATION, self,
            )
        else:
            # Translators: Title of the error message when importing a pack failed.
            gui.messageBox(info, _("Import"), wx.OK | wx.ICON_ERROR, self)

    def _on_delete_pack(self, event):
        key = self._selected_pack()
        if not key:
            return
        if gui.messageBox(
            # Translators: Asks to confirm deleting a pack. {name} is the pack folder name.
            _("Delete the pack {name}? This cannot be undone.").format(name=key),
            # Translators: Title of message boxes about deleting a pack.
            _("Delete pack"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self,
        ) != wx.YES:
            return
        if not self._loader.delete_pack(key):
            gui.messageBox(
                # Translators: Error when a pack couldn't be deleted.
                _("Could not delete the pack."),
                # Translators: Title of message boxes about deleting a pack.
                _("Delete pack"),
                wx.OK | wx.ICON_ERROR, self,
            )
        self._refresh_packs("default")

    # --- Global character rules ---

    def _refresh_rules(self, select=None):
        self._rule_keys = sorted(self._rules)
        self.rulesList.Set([
            f"{_describe_char(c)}: {_describe_effects(r.get('pan', 0.0), r.get('pitch', 0.0))}"
            for c, r in ((c, self._rules[c]) for c in self._rule_keys)
        ])
        if select in self._rule_keys:
            self.rulesList.SetSelection(self._rule_keys.index(select))
        elif self._rule_keys:
            self.rulesList.SetSelection(0)

    def _edit_rule(self, char):
        rule = self._rules.get(char, {})
        with EffectsDialog(self, char, rule.get("pan", 0.0), rule.get("pitch", 0.0)) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            pan, pitch = dlg.values
        rule = {}
        if abs(pan) >= 0.005:
            rule["pan"] = pan
        if abs(pitch) >= 0.05:
            rule["pitch"] = pitch
        if rule:
            self._rules[char] = rule
        else:
            self._rules.pop(char, None)
        self._refresh_rules(char)

    def _on_add_rule(self, event):
        char = _ask_char(self)
        if char is not None:
            self._edit_rule(char)

    def _on_edit_rule(self, event):
        char = _selected(self.rulesList, self._rule_keys)
        if char is not None:
            self._edit_rule(char)

    def _on_remove_rule(self, event):
        char = _selected(self.rulesList, self._rule_keys)
        if char is not None:
            del self._rules[char]
            self._refresh_rules()

    # --- Text replacements ---

    def _refresh_expansions(self, select=None):
        self._expansion_keys = sorted(self._expansions, key=str.lower)
        self.expansionsList.Set([
            # Translators: A text replacement in the list. {shortcut} is what you type, {text} what it becomes.
            _("{shortcut} becomes {text}").format(shortcut=k, text=self._expansions[k])
            for k in self._expansion_keys
        ])
        if select in self._expansion_keys:
            self.expansionsList.SetSelection(self._expansion_keys.index(select))
        elif self._expansion_keys:
            self.expansionsList.SetSelection(0)

    def _edit_expansion(self, shortcut=""):
        with ExpansionDialog(self, shortcut, self._expansions.get(shortcut, "")) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_shortcut, text = dlg.values
        if shortcut and new_shortcut != shortcut:
            self._expansions.pop(shortcut, None)
        self._expansions[new_shortcut] = text
        self._refresh_expansions(new_shortcut)

    def _on_add_expansion(self, event):
        self._edit_expansion()

    def _on_edit_expansion(self, event):
        shortcut = _selected(self.expansionsList, self._expansion_keys)
        if shortcut is not None:
            self._edit_expansion(shortcut)

    def _on_remove_expansion(self, event):
        shortcut = _selected(self.expansionsList, self._expansion_keys)
        if shortcut is not None:
            del self._expansions[shortcut]
            self._refresh_expansions()

    def onSave(self):
        set_ = addon_config.set_config
        set_("enabled", self.enabledCheckbox.IsChecked())
        set_("master_volume", self.volumeSlider.GetValue() / 100.0)
        set_("panning_enabled", self.panningCheckbox.IsChecked())
        set_("panning_width", self.panningWidthSlider.GetValue() / 100.0)
        set_("debounce_ms", self.debounceCtrl.GetValue())
        checked = set(self.categoryList.GetCheckedItems())
        cats = addon_config.get_config("categories")
        for i, cat in enumerate(CATEGORIES):
            cats[cat] = i in checked
        set_("spellcheck_enabled", self.spellcheckCheckbox.IsChecked())
        key = self._selected_pack(bell=False)
        if key:
            set_("current_pack", key)
        addon_config.set_json("global_char_rules_json", self._rules)
        addon_config.set_json("expansions_json", self._expansions)
        set_("capitalize_sentences", self.capitalizeSentencesCheckbox.IsChecked())
        set_("capitalize_i", self.capitalizeICheckbox.IsChecked())
        plugin = _get_plugin()
        plugin.update_settings()
        plugin.reload_packs()
