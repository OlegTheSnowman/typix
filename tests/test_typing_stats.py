import os
import shutil
import tempfile
import unittest

import _addon  # noqa: F401,E402  (makes "typix" importable)
from typix.typing_stats import TypingStats, wpm  # noqa: E402


class TestTypingStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "stats.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def type_burst(self, stats, start, chars, per_second=5.0, words_every=5):
        for i in range(chars):
            stats.key(start + i / per_second, "abcdefg"[i % 7], word_ended=(i % words_every == words_every - 1))
        return start + chars / per_second

    def test_wpm_rule(self):
        self.assertEqual(wpm(300, 60), 60)
        self.assertIsNone(wpm(5, 60))
        self.assertIsNone(wpm(100, 1))

    def test_speed_today_and_current(self):
        s = TypingStats(self.path)
        self.type_burst(s, 0.0, 100)  # five keys a second is 60 wpm
        n = s.summary()
        self.assertEqual(n["current"], 60)
        self.assertEqual(n["today"], 60)
        self.assertEqual(n["today_words"], 20)

    def test_pauses_are_not_typing_time(self):
        s = TypingStats(self.path)
        end = self.type_burst(s, 0.0, 100)
        self.type_burst(s, end + 600, 100)  # ten minutes away
        self.assertEqual(s.summary()["today"], 60)

    def test_best_needs_a_long_stretch(self):
        s = TypingStats(self.path)
        end = self.type_burst(s, 0.0, 30, per_second=10)  # fast but short
        end = self.type_burst(s, end + 10, 100, per_second=5)
        self.type_burst(s, end + 10, 20)
        self.assertEqual(s.summary()["best"], 60)

    def test_survives_a_restart_without_storing_text(self):
        s = TypingStats(self.path)
        self.type_burst(s, 0.0, 100)
        s.misspelling()
        s.save()
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        for key in ("days", "best_wpm", "chars", "active"):
            self.assertIn(key, raw)
        again = TypingStats(self.path)
        n = again.summary()
        self.assertEqual(n["today_misspellings"], 1)
        self.assertEqual(n["all_time"], 60)
        self.assertEqual(n["best"], 60)
        self.assertIsNone(n["current"])

    def test_backspaced_characters_are_taken_off(self):
        s = TypingStats(self.path)
        self.type_burst(s, 0.0, 100)
        for _ in range(50):
            s.backspace()
        n = s.summary()
        self.assertEqual(n["today"], 30)
        self.assertEqual(n["current"], 30)

    def test_held_key_auto_repeat_is_not_typing(self):
        s = TypingStats(self.path)
        end = self.type_burst(s, 0.0, 100)
        for i in range(200):  # holding = for a separator line
            s.key(end + 0.5 + i * 0.033, "=")
        n = s.summary()
        # Only the first press of the held key counts: 100 keys plus one,
        # with half a second added before it.
        self.assertEqual(n["today"], 59)
        self.assertEqual(s.days[next(iter(s.days))]["chars"], 101)

    def test_double_letters_typed_by_hand_still_count(self):
        s = TypingStats(self.path)
        s.key(0.0, "l")
        s.key(0.12, "l")
        self.assertEqual(s.days[next(iter(s.days))]["chars"], 2)

    def test_broken_file_starts_fresh(self):
        with open(self.path, "w") as f:
            f.write("not json")
        self.assertEqual(TypingStats(self.path).summary()["today_words"], 0)


if __name__ == "__main__":
    unittest.main()
