import json
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta

import _addon  # noqa: F401  (makes "typix" importable)
from test_typing_path import plugin_module
from typix.typing_stats import TypingStats  # noqa: E402

stats_text = plugin_module.addon_settings.stats_text


def day_record(words=100, chars=500, timed=490, deleted=10, misspellings=2, active=120.0):
    return {"words": words, "chars": chars, "timed": timed, "deleted": deleted,
            "misspellings": misspellings, "active": active}


class TestStatsReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stats = TypingStats(os.path.join(self.tmp, "stats.json"))
        self.today = date.today()

    def add(self, days_ago, **values):
        self.stats.days[(self.today - timedelta(days_ago)).isoformat()] = day_record(**values)

    def test_nothing_typed_yet(self):
        text = stats_text(self.stats.details())
        self.assertIn("Words: 0", text)
        self.assertIn("Average speed: not enough typing to measure", text)
        self.assertNotIn("Recent days", text)
        self.assertNotIn("Busiest day", text)

    def test_periods_add_up_the_right_days(self):
        self.add(0, words=100)
        self.add(6, words=50)
        self.add(7, words=1000)
        details = self.stats.details()
        self.assertEqual(details["today"]["words"], 100)
        self.assertEqual(details["recent"]["words"], 150)
        self.assertEqual(details["all_time"]["words"], 1150)
        self.assertEqual(details["days_typed"], 3)
        self.assertEqual(details["busiest"][1], 1000)

    def test_speed_and_misspelling_rate(self):
        self.add(0)
        today = self.stats.details()["today"]
        self.assertEqual(today["speed"], 48)  # 480 net characters, 96 words, in 2 minutes
        self.assertEqual(today["misspelling_rate"], 2.0)

    def test_summary_comes_first_then_the_detail_in_order(self):
        self.add(0)
        self.add(1, words=40)
        lines = stats_text(self.stats.details()).split("\n")
        self.assertIn("Words today: 100", lines[0])
        self.assertIn("Average speed today: 48 words per minute", lines[0])
        self.assertEqual(lines[1], "")
        order = [lines.index(heading) for heading in ("Today", "Last 7 days", "All time", "Recent days")]
        self.assertEqual(order, sorted(order))
        self.assertIn("Time spent typing: 2 minutes", lines)
        history = lines[lines.index("Recent days") + 1:]
        self.assertEqual(len(history), 2)
        self.assertTrue(history[0].startswith(self.today.isoformat()))

    def test_history_stops_at_seven_days(self):
        for n in range(10):
            self.add(n)
        self.assertEqual(len(self.stats.details()["history"]), 7)

    def test_a_damaged_day_is_ignored(self):
        path = os.path.join(self.tmp, "damaged.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"days": {self.today.isoformat(): day_record(), "broken": "not a day"}}, f)
        self.assertEqual(TypingStats(path).details()["days_typed"], 1)

    def test_a_word_finished_by_enter_counts_as_a_word_only(self):
        self.stats.word()
        today = self.stats.details()["today"]
        self.assertEqual((today["words"], today["chars"]), (1, 0))

    def test_time_wording(self):
        time_text = plugin_module.addon_settings._time_text
        self.assertEqual(time_text(10), "less than a minute")
        self.assertEqual(time_text(60), "1 minute")
        self.assertEqual(time_text(3900), "1 hour 5 minutes")
        self.assertEqual(time_text(7200), "2 hours")


if __name__ == "__main__":
    unittest.main()
