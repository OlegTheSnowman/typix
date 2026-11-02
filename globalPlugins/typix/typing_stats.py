"""Typing statistics kept across NVDA sessions. Only counts are stored,
never what was typed: per day, characters, finished words, misspellings,
Backspace presses, seconds of active typing and the keys typed in that
time (the first key of a stretch has no time before it), plus the best
speed of any stretch of typing.

Speeds are net, as typing tests report them: characters you deleted with
Backspace are taken off. A key held down to auto-repeat isn't typing and
isn't counted."""

from datetime import date

from .storage import load_json, save_json

# A gap longer than this ends a stretch of typing and isn't counted as
# typing time.
PAUSE = 5.0
# Only stretches at least this long can set the best speed, so one quick
# word doesn't count as a record.
BEST_MIN_CHARS = 50
# Save after this many keys, as well as when NVDA closes.
SAVE_EVERY = 200
# The same character again within this many seconds is auto-repeat from a
# held key; people don't type a letter twice that fast.
REPEAT = 0.06


def wpm(chars, seconds):
    """Words per minute, counting five characters as a word as typing
    tests do, or None when there is too little to measure."""
    if chars < 10 or seconds < 2:
        return None
    return round(chars / 5 / (seconds / 60))


class TypingStats:
    def __init__(self, path):
        self.path = path
        data = load_json(path, {})
        if not isinstance(data, dict):
            data = {}
        self.days = data.get("days") if isinstance(data.get("days"), dict) else {}
        self.best = data.get("best_wpm") or 0
        self._last = None          # time of the last key
        self._burst_start = None   # time the current stretch began
        self._burst_chars = 0
        self._burst_deleted = 0
        self._last_char = None
        self._unsaved = 0

    def _today(self):
        return self.days.setdefault(
            date.today().isoformat(),
            {"chars": 0, "timed": 0, "deleted": 0, "words": 0, "misspellings": 0, "active": 0.0},
        )

    def key(self, now, char, word_ended=False):
        """Counts one typed character at time now (seconds, monotonic)."""
        if char == self._last_char and self._last is not None and 0 <= now - self._last < REPEAT:
            self._last = now
            return
        self._last_char = char
        day = self._today()
        if self._last is not None and 0 <= now - self._last < PAUSE:
            day["active"] += now - self._last
            day["timed"] = day.get("timed", 0) + 1
            self._burst_chars += 1
        else:
            self._end_burst()
            self._burst_start = now
            self._burst_chars = 1
            self._burst_deleted = 0
        self._last = now
        day["chars"] += 1
        if word_ended:
            day["words"] += 1
        self._unsaved += 1
        if self._unsaved >= SAVE_EVERY:
            self.save()

    def misspelling(self):
        self._today()["misspellings"] += 1

    def backspace(self):
        """Counts one character deleted with Backspace."""
        day = self._today()
        day["deleted"] = day.get("deleted", 0) + 1
        self._burst_deleted += 1

    def _end_burst(self):
        if self._burst_chars >= BEST_MIN_CHARS:
            speed = wpm(self._burst_chars - 1 - self._burst_deleted, self._last - self._burst_start)
            if speed and speed > self.best:
                self.best = speed

    def summary(self):
        """Numbers for the report: current, today and all-time speeds, today's
        words and misspellings, and the best speed (including the stretch
        under way)."""
        today = self._today()
        net = sum(d.get("timed", 0) - d.get("deleted", 0) for d in self.days.values())
        active = sum(d.get("active", 0.0) for d in self.days.values())
        # The stretch being typed now.
        current = (
            wpm(self._burst_chars - 1 - self._burst_deleted, self._last - self._burst_start)
            if self._last is not None else None
        )
        best = max(self.best, current or 0) if self._burst_chars >= BEST_MIN_CHARS else self.best
        return {
            "current": current,
            "today": wpm(today.get("timed", 0) - today.get("deleted", 0), today["active"]),
            "today_words": today["words"],
            "today_misspellings": today["misspellings"],
            "all_time": wpm(net, active),
            "best": best or None,
        }

    def save(self):
        self._end_burst()
        if save_json(self.path, {"days": self.days, "best_wpm": self.best}):
            self._unsaved = 0
