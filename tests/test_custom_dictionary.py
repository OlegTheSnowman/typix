import os
import shutil
import tempfile
import unittest


import _addon  # noqa: F401  (makes "typix" importable)
from typix import spellcheck  # noqa: E402


class TestCustomDictionary(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        spellcheck.set_enchant_dictionaries_directory(self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_and_is_custom_word(self):
        self.assertFalse(spellcheck.is_custom_word("juust", "en_US"))
        self.assertTrue(spellcheck.add_custom_word("juust", "en_US"))
        self.assertTrue(spellcheck.is_custom_word("juust", "en_US"))

    def test_custom_word_scoped_per_language(self):
        spellcheck.add_custom_word("colour", "en_GB")
        self.assertTrue(spellcheck.is_custom_word("colour", "en_GB"))
        self.assertFalse(spellcheck.is_custom_word("colour", "en_US"))

    def test_remove_custom_word(self):
        spellcheck.add_custom_word("juust", "en_US")
        self.assertTrue(spellcheck.remove_custom_word("juust", "en_US"))
        self.assertFalse(spellcheck.is_custom_word("juust", "en_US"))

    def test_remove_nonexistent_word_returns_false(self):
        self.assertFalse(spellcheck.remove_custom_word("nope", "en_US"))

    def test_add_rejects_empty_word_or_lang(self):
        self.assertFalse(spellcheck.add_custom_word("", "en_US"))
        self.assertFalse(spellcheck.add_custom_word("word", ""))

    def test_get_custom_words_returns_sorted_tuples(self):
        spellcheck.add_custom_word("zebra", "en_US")
        spellcheck.add_custom_word("apple", "en_US")
        spellcheck.add_custom_word("banane", "fr_FR")
        result = spellcheck.get_custom_words()
        self.assertEqual(result, [("apple", "en_US"), ("banane", "fr_FR"), ("zebra", "en_US")])

    def test_persists_across_reload(self):
        spellcheck.add_custom_word("juust", "en_US")
        # Simulate a fresh session by reloading from the same directory.
        spellcheck.set_enchant_dictionaries_directory(self.tmp_dir)
        self.assertTrue(spellcheck.is_custom_word("juust", "en_US"))

    def test_check_word_short_circuits_for_custom_word(self):
        # Even a nonsense word must check as correct once added, regardless
        # of whether any real dictionary is available.
        spellcheck.add_custom_word("juust", "en_US")
        self.assertTrue(spellcheck.check_word("juust", "en_US"))

    def test_custom_dictionary_file_is_valid_json(self):
        spellcheck.add_custom_word("juust", "en_US")
        path = os.path.join(self.tmp_dir, "spellcheck_dictionaries", "typix_custom_dictionary.json")
        self.assertTrue(os.path.isfile(path))
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data, {"en_US": ["juust"]})

    def test_removing_last_word_for_language_drops_the_language_key(self):
        spellcheck.add_custom_word("juust", "en_US")
        spellcheck.remove_custom_word("juust", "en_US")
        self.assertEqual(spellcheck.get_custom_words(), [])

    def test_is_custom_word_is_case_insensitive(self):
        spellcheck.add_custom_word("Apostrophy", "en_US")
        self.assertTrue(spellcheck.is_custom_word("apostrophy", "en_US"))
        self.assertTrue(spellcheck.is_custom_word("APOSTROPHY", "en_US"))
        self.assertTrue(spellcheck.is_custom_word("Apostrophy", "en_US"))

    def test_add_does_not_duplicate_differently_cased_word(self):
        spellcheck.add_custom_word("Apostrophy", "en_US")
        spellcheck.add_custom_word("apostrophy", "en_US")
        spellcheck.add_custom_word("APOSTROPHY", "en_US")
        result = spellcheck.get_custom_words()
        self.assertEqual(len(result), 1)

    def test_remove_is_case_insensitive(self):
        spellcheck.add_custom_word("Apostrophy", "en_US")
        self.assertTrue(spellcheck.remove_custom_word("apostrophy", "en_US"))
        self.assertFalse(spellcheck.is_custom_word("Apostrophy", "en_US"))

    def test_check_word_is_case_insensitive_for_custom_words(self):
        spellcheck.add_custom_word("juust", "en_US")
        self.assertTrue(spellcheck.check_word("JUUST", "en_US"))
        self.assertTrue(spellcheck.check_word("Juust", "en_US"))


if __name__ == "__main__":
    unittest.main()
