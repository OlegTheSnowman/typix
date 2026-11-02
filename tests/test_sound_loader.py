import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
import zipfile


import _addon  # noqa: F401  (makes "typix" importable)
from typix.sound_loader import SoundPackLoader, char_category, read_manifest  # noqa: E402


def make_pack(root, name, files, manifest_extra=None):
    pack = os.path.join(root, name)
    os.makedirs(pack)
    manifest = {"name": name, "files": {}}
    manifest.update(manifest_extra or {})
    for fn, size in files.items():
        with open(os.path.join(pack, fn), "wb") as f:
            f.write(b"x" * size)
    with open(os.path.join(pack, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return pack


class TestSoundLoader(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.loader = SoundPackLoader(self.root)
        self.src = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)
        shutil.rmtree(self.src)

    def test_missing_manifest_is_not_a_pack(self):
        os.makedirs(os.path.join(self.root, "invalid"))
        self.assertIsNone(self.loader.load_pack(os.path.join(self.root, "invalid")))

    def test_valid_pack(self):
        path = make_pack(self.root, "p", {"letter.wav": 10}, {"files": {"LETTER": "letter.wav"}})
        pack = self.loader.load_pack(path)
        self.assertEqual(pack.name, "p")
        self.assertIn("LETTER", pack.sounds)

    def test_no_packs_gives_no_sound(self):
        self.loader.scan_packs()
        self.assertIsNone(self.loader.get_sound_path("nonexistent", "LETTER"))

    def test_fallback_chain(self):
        make_pack(self.root, "p", {"letter.wav": 10, "delete.wav": 10},
                  {"files": {"LETTER": "letter.wav", "DELETE": "delete.wav"}})
        self.loader.scan_packs()
        self.assertTrue(self.loader.get_sound_path("p", "BACKSPACE").endswith("delete.wav"))
        self.assertTrue(self.loader.get_sound_path("p", "TAB").endswith("letter.wav"))
        self.assertIsNone(self.loader.get_sound_path("p", "CAPS_LETTER"))
        self.assertIsNone(self.loader.get_sound_path("p", "MISSPELLED"))

    def test_empty_file_is_skipped_so_category_falls_back(self):
        make_pack(self.root, "p", {"letter.wav": 10, "space.wav": 0},
                  {"files": {"LETTER": "letter.wav", "SPACE": "space.wav"}})
        self.loader.scan_packs()
        self.assertTrue(self.loader.get_sound_path("p", "SPACE").endswith("letter.wav"))

    def test_char_category(self):
        self.assertEqual(char_category(" "), "SPACE")
        self.assertEqual(char_category(","), "PUNCTUATION")
        self.assertEqual(char_category("$"), "PUNCTUATION")
        self.assertEqual(char_category("A"), "CAPS_LETTER")
        self.assertEqual(char_category("a"), "LETTER")
        self.assertEqual(char_category("7"), "LETTER")

    def test_bundled_packs_load_and_default_has_misspelled_sound(self):
        bundled = os.path.join(os.path.dirname(os.path.dirname(__file__)), "globalPlugins", "typix", "soundpacks")
        loader = SoundPackLoader(bundled)
        loader.scan_packs()
        self.assertEqual(len(loader.available_packs), len(os.listdir(bundled)))
        self.assertTrue(os.path.isfile(loader.get_sound_path("default", "MISSPELLED")))
        self.assertTrue(loader.get_sound_path("default", "BACKSPACE").endswith("delete.wav"))

    def test_save_pack_copies_keeps_and_cleans_up(self):
        pack = make_pack(self.root, "p", {"letter.wav": 10, "old.wav": 10, "char_002c.wav": 10}, {
            "files": {"LETTER": "letter.wav", "SPACE": "old.wav"},
            "char_sounds": {",": "char_002c.wav"},
        })
        new = os.path.join(self.src, "new.wav")
        with open(new, "wb") as f:
            f.write(b"y" * 10)
        errors = self.loader.save_pack(
            "p", "Renamed", "me", "desc",
            {"LETTER": [os.path.join(pack, "letter.wav"), new]},
            {".": [new]},
            {".": 0.5, ",": 0.0},
            {".": 2.0},
        )
        self.assertEqual(errors, [])
        m = read_manifest(pack)
        self.assertEqual(m["name"], "Renamed")
        self.assertEqual(m["files"], {"LETTER": ["letter.wav", "letter_1.wav"]})
        self.assertEqual(m["char_sounds"], {".": "char_002e_1.wav"})
        self.assertEqual(m["char_panning"], {".": 0.5})
        self.assertEqual(m["char_pitch"], {".": 2.0})
        # Sounds no longer used are gone, the new ones are in.
        self.assertFalse(os.path.exists(os.path.join(pack, "old.wav")))
        self.assertFalse(os.path.exists(os.path.join(pack, "char_002c.wav")))
        self.assertTrue(os.path.exists(os.path.join(pack, "letter_1.wav")))

    def test_save_pack_rejects_non_wav(self):
        make_pack(self.root, "p", {"letter.wav": 10}, {"files": {"LETTER": "letter.wav"}})
        mp3 = os.path.join(self.src, "x.mp3")
        open(mp3, "wb").close()
        errors = self.loader.save_pack("p", "p", "", "", {"SPACE": [mp3]}, {}, {}, {})
        self.assertEqual(len(errors), 1)

    def test_export_then_import_round_trip(self):
        make_pack(self.root, "p", {"letter.wav": 10}, {"files": {"LETTER": "letter.wav"}})
        self.loader.scan_packs()
        z = os.path.join(self.src, "p.zip")
        self.assertTrue(self.loader.export_pack("p", z))
        self.assertEqual(self.loader.import_pack(z), ("exists", "p"))
        self.assertEqual(self.loader.import_pack(z, override=True), (True, "p"))

    def test_import_pack_at_zip_root_is_named_after_zip(self):
        z = os.path.join(self.src, "Rooted.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"name": "R", "files": {"LETTER": "a.wav"}}))
            zf.writestr("a.wav", b"x" * 10)
            zf.writestr("../escape.wav", b"x")
        self.assertEqual(self.loader.import_pack(z), (True, "Rooted"))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.root), "escape.wav")))

    def test_import_without_manifest_fails(self):
        z = os.path.join(self.src, "bad.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("x/a.wav", b"x")
        status, _msg = self.loader.import_pack(z)
        self.assertFalse(status)

    def test_manifest_cannot_name_files_outside_the_pack(self):
        outside = os.path.join(self.root, "outside.wav")
        with open(outside, "wb") as f:
            f.write(b"x" * 10)
        unc = "\\\\attacker.invalid\\share\\a.wav"
        pack = make_pack(self.root, "p", {"letter.wav": 10}, {"files": {
            "LETTER": "letter.wav", "SPACE": ["../outside.wav", outside], "ENTER": unc,
        }})
        real_isfile = os.path.isfile
        checked = []

        def isfile(path):
            checked.append(path)
            return real_isfile(path)
        with unittest.mock.patch("os.path.isfile", isfile):
            loaded = self.loader.load_pack(pack)
        self.assertEqual(list(loaded.sounds), ["LETTER"])
        # Looking at a network path is the leak, so it must never be looked at.
        self.assertFalse([p for p in checked if "attacker" in p])

    def test_sound_in_a_subfolder_of_the_pack_is_allowed(self):
        pack = make_pack(self.root, "p", {}, {"files": {"LETTER": "sub/a.wav"}})
        os.makedirs(os.path.join(pack, "sub"))
        with open(os.path.join(pack, "sub", "a.wav"), "wb") as f:
            f.write(b"x" * 10)
        self.assertIn("LETTER", self.loader.load_pack(pack).sounds)

    def test_broken_manifest_does_not_stop_the_scan(self):
        make_pack(self.root, "good", {"letter.wav": 10}, {"files": {"LETTER": "letter.wav"}})
        make_pack(self.root, "bad", {}, {"files": ["not", "a", "mapping"]})
        self.loader.scan_packs()
        self.assertEqual(list(self.loader.available_packs), ["good"])

    def test_failed_import_keeps_the_pack_it_would_replace(self):
        pack = make_pack(self.root, "p", {"letter.wav": 10}, {"files": {"LETTER": "letter.wav"}})
        z = os.path.join(self.src, "p.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"name": "p", "files": {"LETTER": "gone.wav"}}))
        status, _msg = self.loader.import_pack(z, override=True)
        self.assertFalse(status)
        self.assertTrue(os.path.exists(os.path.join(pack, "letter.wav")))
        self.assertEqual(os.listdir(self.root), ["p"])

    def test_create_pack(self):
        self.assertEqual(self.loader.create_pack("My Pack!"), "My_Pack")
        self.assertIsNone(self.loader.create_pack("My Pack!"))
        self.assertIsNone(self.loader.create_pack("!!!"))


if __name__ == "__main__":
    unittest.main()
