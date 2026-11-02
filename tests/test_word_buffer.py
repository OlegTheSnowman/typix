import unittest


import _addon  # noqa: F401  (makes "typix" importable)
from typix.spellcheck import is_word_char, trailing_word  # noqa: E402


class TestWordRules(unittest.TestCase):
    def test_letters_digits_and_marks_are_word_chars(self):
        for ch in "abcTEST0123\u00e9\u0301\u0436":
            self.assertTrue(is_word_char(ch), ch)

    def test_separators_end_a_word(self):
        # A word ends at punctuation too, not only at a space, like NVDA.
        for ch in " .,!?;:'\"-()\r\n\t":
            self.assertFalse(is_word_char(ch), repr(ch))
        self.assertFalse(is_word_char(""))

    def test_trailing_word_skips_the_separator_just_typed(self):
        # The ending character may or may not be in the field yet.
        self.assertEqual(trailing_word("I said hello"), "hello")
        self.assertEqual(trailing_word("I said hello "), "hello")
        self.assertEqual(trailing_word("I said hello, "), "hello")

    def test_apostrophe_inside_a_word_belongs_to_it(self):
        self.assertEqual(trailing_word("I can't "), "can't")
        self.assertEqual(trailing_word("I can\u2019t"), "can\u2019t")
        self.assertEqual(trailing_word("rock 'n' roll"), "roll")

    def test_quotes_around_a_word_are_not_part_of_it(self):
        self.assertEqual(trailing_word("she said 'hello' "), "hello")
        self.assertEqual(trailing_word("the dogs' "), "dogs")

    def test_trailing_word_of_nothing(self):
        self.assertEqual(trailing_word(""), "")
        self.assertEqual(trailing_word("  ...  "), "")

    def test_trailing_word_keeps_non_latin_words_whole(self):
        self.assertEqual(trailing_word("\u043f\u0440\u0438\u0432\u0456\u0442 "), "\u043f\u0440\u0438\u0432\u0456\u0442")


if __name__ == "__main__":
    unittest.main()
