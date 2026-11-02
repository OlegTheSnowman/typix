import os
import shutil
import zipfile
import logging
import random
import unicodedata

from .storage import load_json, save_json

log = logging.getLogger(__name__)

try:
    import addonHandler
    addonHandler.initTranslation()
except ImportError:
    pass  # outside NVDA, in the tests

# Every sound category, in the order the settings show them. config.py
# builds its per-category toggles from this list, so adding a category
# here is the only change needed.
CATEGORIES = [
    "LETTER", "CAPS_LETTER", "SPACE", "ENTER", "TAB",
    "BACKSPACE", "DELETE", "PUNCTUATION", "MISSPELLED", "REPLACED",
]

# Where a category borrows its sound from when a pack has none for it.
# None means stay silent: a caps beep or misspelling alert that sounds
# like an ordinary letter would be worse than nothing. Unlisted
# categories fall back to LETTER.
FALLBACK = {
    "LETTER": None,
    "CAPS_LETTER": None,
    "MISSPELLED": None,
    "REPLACED": None,
    "BACKSPACE": "DELETE",
}

MANIFEST = "manifest.json"


def char_category(ch):
    """Sound category for a typed character. The plugin plays by it and
    the pack editor previews by it, so they always agree."""
    if ch == " ":
        return "SPACE"
    if unicodedata.category(ch)[0] in "PS":
        return "PUNCTUATION"
    return "CAPS_LETTER" if ch.isupper() else "LETTER"


def read_manifest(pack_dir):
    """Returns the pack's manifest dict, or None if missing or unreadable."""
    data = load_json(os.path.join(pack_dir, MANIFEST))
    return data if isinstance(data, dict) else None


def write_manifest(pack_dir, manifest):
    """Writes atomically, so a crash mid-save can't leave a broken pack."""
    if not save_json(os.path.join(pack_dir, MANIFEST), manifest):
        raise OSError(f"could not write the manifest in {pack_dir}")


def _as_list(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


class SoundPack:
    """A loaded sound pack. Sound maps hold absolute paths."""

    def __init__(self, dir_name, manifest, sounds, char_sounds, char_panning, char_pitch):
        self.dir_name = dir_name
        self.name = str(manifest.get("name", dir_name))
        self.author = str(manifest.get("author", ""))
        self.description = str(manifest.get("description", ""))
        self.sounds = sounds              # category -> [paths]
        self.char_sounds = char_sounds    # character -> [paths]
        self.char_panning = char_panning  # character -> pan, -1.0 to 1.0
        self.char_pitch = char_pitch      # character -> semitones


class SoundPackLoader:
    """Finds, loads, saves, imports and exports sound packs."""

    def __init__(self, soundpacks_root):
        self.soundpacks_root = soundpacks_root
        self.available_packs = {}

    def pack_dir(self, dir_name):
        return os.path.join(self.soundpacks_root, dir_name)

    def scan_packs(self):
        self.available_packs = {}
        os.makedirs(self.soundpacks_root, exist_ok=True)
        for entry in os.scandir(self.soundpacks_root):
            if entry.is_dir():
                # A broken pack must not stop the add-on from starting.
                try:
                    pack = self.load_pack(entry.path)
                except Exception:
                    log.exception(f"Typix: could not load the pack {entry.name}")
                    continue
                if pack:
                    self.available_packs[entry.name] = pack
        log.info(f"Typix: loaded {len(self.available_packs)} sound packs")

    @staticmethod
    def _resolve(pack_path, value):
        """Manifest value (filename or list) -> list of usable absolute paths.
        Empty files are skipped so a category falls back instead of going
        silent. Anything outside the pack folder is skipped before the disk
        is touched: a shared pack could otherwise name a network path, and
        just looking at it would send the user's Windows login to that
        server."""
        root = os.path.abspath(pack_path)
        paths = []
        for fn in _as_list(value):
            p = os.path.abspath(os.path.join(root, fn))
            if p.startswith(root + os.sep) and os.path.isfile(p) and os.path.getsize(p) > 0:
                paths.append(p)
        return paths

    @staticmethod
    def _floats(mapping):
        out = {}
        if isinstance(mapping, dict):
            for char, val in mapping.items():
                try:
                    out[char] = float(val)
                except (ValueError, TypeError):
                    pass
        return out

    def load_pack(self, pack_path):
        manifest = read_manifest(pack_path)
        if manifest is None:
            return None
        sounds = {}
        for category, value in (manifest.get("files") or {}).items():
            if category in CATEGORIES:
                paths = self._resolve(pack_path, value)
                if paths:
                    sounds[category] = paths
        char_sounds = {}
        for char, value in (manifest.get("char_sounds") or {}).items():
            paths = self._resolve(pack_path, value)
            if paths:
                char_sounds[char] = paths
        if not sounds and not char_sounds:
            return None
        return SoundPack(
            os.path.basename(pack_path), manifest, sounds, char_sounds,
            self._floats(manifest.get("char_panning")),
            self._floats(manifest.get("char_pitch")),
        )

    def _pack(self, pack_name):
        return self.available_packs.get(pack_name) or self.available_packs.get("default")

    def get_sound_path(self, pack_name, category):
        """A random sound for category, following FALLBACK when the pack has none."""
        pack = self._pack(pack_name)
        if not pack:
            return None
        seen = set()
        while category and category not in seen:
            paths = pack.sounds.get(category)
            if paths:
                return random.choice(paths)
            seen.add(category)
            category = FALLBACK.get(category, "LETTER")
        return None

    def get_char_sound_path(self, pack_name, char):
        pack = self._pack(pack_name)
        paths = pack.char_sounds.get(char) if pack else None
        return random.choice(paths) if paths else None

    def get_char_effects(self, pack_name, char):
        """(pan, pitch) the pack sets for char, 0.0 for each when unset."""
        pack = self._pack(pack_name)
        if not pack:
            return 0.0, 0.0
        return pack.char_panning.get(char, 0.0), pack.char_pitch.get(char, 0.0)

    def create_pack(self, name, author="", description=""):
        """Makes an empty pack and returns its directory name, or None if the
        name is unusable or taken."""
        dir_name = "".join(c for c in name if c.isalnum() or c in " _-").strip().replace(" ", "_")
        if not dir_name:
            return None
        pack_dir = self.pack_dir(dir_name)
        if os.path.exists(pack_dir):
            return None
        os.makedirs(pack_dir)
        write_manifest(pack_dir, {
            "name": name, "author": author, "version": "1.0", "description": description,
            "files": {}, "char_sounds": {}, "char_panning": {}, "char_pitch": {},
        })
        return dir_name

    def save_pack(self, dir_name, name, author, description,
                  sounds, char_sounds, char_panning, char_pitch):
        """Writes the whole pack in one go from the editor's state.

        sounds and char_sounds map to lists of paths. Paths already inside
        the pack are kept by name; anything else is copied in under a fresh
        name. Files the old manifest used and the new one doesn't are
        deleted. Returns a list of error strings, empty on success."""
        pack_dir = self.pack_dir(dir_name)
        if not os.path.isdir(pack_dir):
            # Translators: Error when saving a pack whose folder no longer exists.
            return [_("Pack directory not found.")]
        old = read_manifest(pack_dir) or {}
        pack_abs = os.path.normcase(os.path.abspath(pack_dir))
        errors = []
        used = set()

        def take_in(path, stem):
            """Returns the in-pack filename for path, copying it if needed."""
            src = os.path.abspath(path)
            if os.path.normcase(os.path.dirname(src)) == pack_abs:
                used.add(os.path.basename(src).lower())
                return os.path.basename(src)
            if os.path.splitext(src)[1].lower() != ".wav":
                # Translators: Error when a chosen sound isn't a WAV file. {file} is the file name.
                errors.append(_("{file}: only .wav files are supported.").format(file=os.path.basename(src)))
                return None
            n = 1
            while True:
                fn = f"{stem}_{n}.wav"
                if fn.lower() not in used and not os.path.exists(os.path.join(pack_dir, fn)):
                    break
                n += 1
            try:
                shutil.copy2(src, os.path.join(pack_dir, fn))
            except OSError as e:
                errors.append(f"{os.path.basename(src)}: {e}")
                return None
            used.add(fn.lower())
            return fn

        def build(mapping, stem_for):
            out = {}
            for key, paths in mapping.items():
                names = [fn for fn in (take_in(p, stem_for(key)) for p in paths) if fn]
                if names:
                    out[key] = names[0] if len(names) == 1 else names
            return out

        manifest = dict(old)
        manifest.update({
            "name": name or old.get("name") or dir_name,
            "author": author,
            "description": description,
            "files": build(sounds, str.lower),
            "char_sounds": build(char_sounds, lambda c: f"char_{ord(c[0]):04x}"),
            "char_panning": {c: v for c, v in char_panning.items() if abs(v) >= 0.005},
            "char_pitch": {c: v for c, v in char_pitch.items() if abs(v) >= 0.05},
        })
        write_manifest(pack_dir, manifest)

        def referenced(m):
            names = set()
            for section in ("files", "char_sounds"):
                for value in (m.get(section) or {}).values():
                    names.update(fn.lower() for fn in _as_list(value))
            return names

        for fn in referenced(old) - referenced(manifest):
            path = os.path.abspath(os.path.join(pack_dir, fn))
            if os.path.normcase(os.path.dirname(path)) == pack_abs and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    log.warning(f"Typix: could not remove unused sound {path}")
        return errors

    def delete_pack(self, dir_name):
        pack_dir = self.pack_dir(dir_name)
        if not os.path.isdir(pack_dir):
            return False
        shutil.rmtree(pack_dir, ignore_errors=True)
        self.available_packs.pop(dir_name, None)
        return not os.path.exists(pack_dir)

    def export_pack(self, dir_name, dest_zip_path):
        pack_dir = self.pack_dir(dir_name)
        if not os.path.isdir(pack_dir):
            return False
        try:
            with zipfile.ZipFile(dest_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(pack_dir):
                    for f in files:
                        full = os.path.join(root, f)
                        zf.write(full, os.path.relpath(full, self.soundpacks_root))
            return True
        except Exception as e:
            log.error(f"Typix: export failed: {e}")
            return False

    def import_pack(self, zip_path, override=False):
        """Installs a pack zip. The pack may sit in one top folder (what
        export_pack writes) or at the zip root, in which case it is named
        after the zip file. Returns (True, dir_name), ("exists", dir_name)
        or (False, error message)."""
        staging = None
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = [m for m in zf.infolist() if not m.is_dir()]
                names = [m.filename.replace("\\", "/") for m in members]
                if MANIFEST in names:
                    top = os.path.splitext(os.path.basename(zip_path))[0]
                    prefix = ""
                else:
                    manifests = [n for n in names if n.count("/") == 1 and n.endswith("/" + MANIFEST)]
                    if len(manifests) != 1:
                        # Translators: Error when an imported zip has no pack manifest.
                        return (False, _("No manifest.json found in the pack."))
                    prefix = manifests[0][:-len(MANIFEST)]
                    top = prefix.rstrip("/")
                top = "".join(c for c in top if c not in '<>:"/\\|?*').strip(" .")
                if not top:
                    # Translators: Error when a file chosen for import isn't a usable zip file.
                    return (False, _("Invalid zip file."))
                dest = self.pack_dir(top)
                if os.path.exists(dest) and not override:
                    return ("exists", top)
                # Unpacked beside the real folder and checked there, so a
                # bad zip can't cost the user the pack it would replace.
                staging = os.path.abspath(dest + ".importing")
                shutil.rmtree(staging, ignore_errors=True)
                for member, name in zip(members, names):
                    if not name.startswith(prefix):
                        continue
                    target = os.path.abspath(os.path.join(staging, name[len(prefix):]))
                    if not target.startswith(staging + os.sep):
                        continue  # path tries to escape the pack folder
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
            try:
                loaded = self.load_pack(staging)
            except Exception:
                loaded = None
            if not loaded:
                # Translators: Error when an imported pack has no sound files that can be played.
                return (False, _("The pack has no usable sounds."))
            shutil.rmtree(dest, ignore_errors=True)
            os.replace(staging, dest)
            self.scan_packs()
            return (True, top)
        except zipfile.BadZipFile:
            # Translators: Error when a file chosen for import isn't a usable zip file.
            return (False, _("Invalid zip file."))
        except Exception as e:
            # Translators: Error when importing a pack fails for another reason. {error} is the system's error message.
            return (False, _("Import failed: {error}").format(error=e))
        finally:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
