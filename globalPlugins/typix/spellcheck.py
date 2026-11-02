import os
import re
import sys
import json
import logging
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from .storage import load_json, save_json

log = logging.getLogger(__name__)

try:
    import addonHandler
    addonHandler.initTranslation()
except ImportError:  # outside NVDA, in the tests
    def _(text):
        return text

_LIBS_DIR = os.path.join(os.path.dirname(__file__), "libs")
if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

try:
    import enchant
    import enchant.errors
    _ENCHANT_AVAILABLE = True
except Exception:
    log.exception("Failed to import vendored enchant; spellcheck will be a no-op")
    enchant = None
    _ENCHANT_AVAILABLE = False

DICT_GITHUB_API_URL = "https://api.github.com/repos/LibreOffice/dictionaries/contents/{lang_tag}?ref=master"
DICTIONARY_FILE_EXTS = {".dic", ".aff"}
DICT_DOWNLOAD_HOST = "raw.githubusercontent.com"
# A language that can't be fetched or still has no dictionary afterwards is
# tried again after this many seconds, not on every finished word.
DOWNLOAD_RETRY = 3600

_DOWNLOADABLE_LANGUAGES_FILE = os.path.join(os.path.dirname(__file__), "downloadable_languages.txt")
try:
    with open(_DOWNLOADABLE_LANGUAGES_FILE, "r") as _f:
        DOWNLOADABLE_LANGUAGES = {tag.strip() for tag in _f if tag.strip()}
except Exception:
    DOWNLOADABLE_LANGUAGES = set()

_dictionaries_dir = None
_dict_cache = {}  # lang_tag -> enchant.Dict or False (permanently unavailable this session)
_download_in_progress = set()  # download-folder tags currently downloading
_download_attempts = {}  # download-folder tag -> time of the last attempt
_warned_unavailable = False
_lock = threading.Lock()

_custom_dict_path = None
_custom_words = {}  # lang_tag -> set of words, user-managed via "Add to dictionary" / Manage Dictionary


def _load_custom_dict():
    global _custom_words
    _custom_words = {}
    raw = load_json(_custom_dict_path, {}) if _custom_dict_path else {}
    if isinstance(raw, dict):
        _custom_words = {lang: set(words) for lang, words in raw.items() if isinstance(words, list)}


def _save_custom_dict():
    if _custom_dict_path:
        save_json(_custom_dict_path, {lang: sorted(words) for lang, words in _custom_words.items() if words})


def add_custom_word(word, lang_tag):
    """Marks word as correctly spelled for lang_tag going forward,
    case-insensitively (adding "Apostrophy" also matches "apostrophy" or
    "APOSTROPHY" later). Kept as a plain JSON store (not fed into
    enchant's own personal word list) specifically so it stays fully
    listable and reversible for the Manage Dictionary UI -- enchant's own
    add-word mechanism has no clean way to undo an addition through this
    wrapper."""
    if not word or not lang_tag:
        return False
    words = _custom_words.setdefault(lang_tag, set())
    if not any(w.lower() == word.lower() for w in words):
        words.add(word)
        _save_custom_dict()
    return True


def remove_custom_word(word, lang_tag):
    words = _custom_words.get(lang_tag)
    if not words:
        return False
    target = word.lower()
    match = next((w for w in words if w.lower() == target), None)
    if match is None:
        return False
    words.discard(match)
    if not words:
        _custom_words.pop(lang_tag, None)
    _save_custom_dict()
    return True


def is_custom_word(word, lang_tag):
    words = _custom_words.get(lang_tag)
    if not words:
        return False
    target = word.lower()
    return any(w.lower() == target for w in words)


def get_custom_words():
    """Returns a sorted list of (word, lang_tag) tuples across all languages."""
    result = []
    for lang, words in _custom_words.items():
        for w in words:
            result.append((w, lang))
    result.sort()
    return result


def _resolve_download_tag(lang_tag):
    """LibreOffice's dictionaries repo groups locales by base language
    folder (e.g. 'en' contains en_US.dic, en_GB.dic, ...), not by full
    locale tag. Returns the folder tag to download for lang_tag, or None
    if lang_tag isn't downloadable at all."""
    if lang_tag in DOWNLOADABLE_LANGUAGES:
        return lang_tag
    if "_" in lang_tag:
        base = lang_tag.split("_")[0]
        if base in DOWNLOADABLE_LANGUAGES:
            return base
    return None


def set_enchant_dictionaries_directory(base_dir):
    """base_dir: a user-writable directory (e.g. NVDA's configPath) under
    which a 'spellcheck_dictionaries' subfolder will be created and pointed
    to via ENCHANT_CONFIG_DIR."""
    global _dictionaries_dir, _custom_dict_path
    _dictionaries_dir = os.path.join(base_dir, "spellcheck_dictionaries")
    try:
        if not os.path.isdir(_dictionaries_dir):
            os.makedirs(_dictionaries_dir, exist_ok=True)
        os.environ["ENCHANT_CONFIG_DIR"] = _dictionaries_dir
        _custom_dict_path = os.path.join(_dictionaries_dir, "typix_custom_dictionary.json")
        _load_custom_dict()
    except Exception:
        log.exception("Failed to set up spellcheck dictionaries directory")


def _get_dict(lang_tag):
    if not _ENCHANT_AVAILABLE or not lang_tag:
        return None
    if lang_tag in _dict_cache:
        cached = _dict_cache[lang_tag]
        return cached or None
    try:
        d = enchant.request_dict(lang_tag)
        _dict_cache[lang_tag] = d
        return d
    except Exception:
        _dict_cache[lang_tag] = False
        return None


def start_checker(text, lang_tag):
    """Returns an enchant.checker.SpellChecker preloaded with text, or None
    if unavailable. Iterate it once (each item exposes .word, .suggest(),
    .replace(new_word)); afterwards call .get_text() to get the corrected
    string with any .replace() calls applied. Never raises."""
    if not text or not _ENCHANT_AVAILABLE or not lang_tag:
        return None
    try:
        d = _get_dict(lang_tag)
        if d is None:
            ensure_dictionary_async(lang_tag)
            return None
        from enchant.checker import SpellChecker
        checker = SpellChecker(d)
        checker.set_text(text)
        return checker
    except Exception:
        log.exception("start_checker failed")
        return None


def is_word_char(ch):
    """Letter, mark or digit: the same test NVDA's speakTypedCharacters
    uses, so a word ends exactly where NVDA thinks it does."""
    return bool(ch) and unicodedata.category(ch)[0] in "LMN"


APOSTROPHES = "'\u2019"


def trailing_word(text):
    """The last word in text, ignoring separators after it. An apostrophe
    between letters belongs to the word, so "can't" is one word. The
    character that ended the word may or may not be in the field yet when
    we read it, so trailing separators are skipped either way."""
    end = len(text)
    while end and not is_word_char(text[end - 1]):
        end -= 1
    start = end
    while start:
        ch = text[start - 1]
        if is_word_char(ch):
            start -= 1
        elif ch in APOSTROPHES and start >= 2 and is_word_char(text[start - 2]):
            start -= 1
        else:
            break
    return text[start:end]


def suggestions(word, lang_tag):
    """Corrections for word as (suggestion, what is wrong) pairs. Each
    suggestion is in the word's own case (Helo gives Hello, HELO gives
    HELLO), duplicates are dropped, and the description names a single
    missing, extra, wrong or swapped letter, or is empty when the
    difference is bigger. Empty list if no dictionary is available."""
    d = _get_dict(lang_tag)
    if d is None or not word:
        return []
    try:
        raw = d.suggest(word)
    except Exception:
        # Never the word itself: the NVDA log must not hold what was typed.
        log.exception(f"suggestions failed for language {lang_tag!r}")
        return []
    result = []
    seen = set()
    w = word.lower()
    for sug in raw:
        if word.isupper() and len(word) > 1:
            sug = sug.upper()
        elif word[0].isupper():
            sug = sug[:1].upper() + sug[1:]
        if sug == word or sug in seen:
            continue
        seen.add(sug)
        t = sug.lower()
        what = ""
        if len(t) == len(w) + 1:
            i = next((i for i in range(len(t)) if t[:i] + t[i + 1:] == w), None)
            if i is not None:
                # Translators: What is wrong with a misspelled word. {letter} is the letter that is missing.
                what = _("missing {letter}").format(letter=t[i])
        elif len(t) == len(w) - 1:
            i = next((i for i in range(len(w)) if w[:i] + w[i + 1:] == t), None)
            if i is not None:
                # Translators: What is wrong with a misspelled word. {letter} is the letter that should not be there.
                what = _("extra {letter}").format(letter=w[i])
        elif len(t) == len(w):
            diff = [i for i in range(len(w)) if w[i] != t[i]]
            if len(diff) == 1:
                i = diff[0]
                # Translators: What is wrong with a misspelled word. {right} is the correct letter, {wrong} the letter typed.
                what = _("{right} instead of {wrong}").format(right=t[i], wrong=w[i])
            elif len(diff) == 2 and diff[1] == diff[0] + 1 and w[diff[0]] == t[diff[1]] and w[diff[1]] == t[diff[0]]:
                # Translators: What is wrong with a misspelled word: two neighbouring letters typed in the wrong order.
                what = _("{first} and {second} swapped").format(first=w[diff[0]], second=w[diff[1]])
        result.append((sug, what))
    return result


def looks_like_code(token):
    """True when a whitespace-separated token is a link, an email address,
    a file name or a code identifier, which a dictionary would only flag
    as noise: anything with :// @ _ \\ or /, a dot between letters, or a
    capital inside a lowercase run (camelCase)."""
    if any(mark in token for mark in ("://", "@", "_", "\\", "/")):
        return True
    return bool(re.search(r"\w\.\w", token) or re.search(r"[a-z][A-Z]", token))


def check_word(word, lang_tag):
    """Returns True (correct), False (misspelled), or None (no verdict:
    dictionary missing, still downloading, or a word not worth checking,
    like a single letter or anything with a digit in it). Never raises."""
    if not word or not word.strip():
        return None
    if len(word) < 2 or any(ch.isdigit() for ch in word):
        return None
    # Dictionaries spell contractions with a straight apostrophe.
    word = word.replace("\u2019", "'")
    if lang_tag and is_custom_word(word, lang_tag):
        return True
    if not _ENCHANT_AVAILABLE:
        global _warned_unavailable
        if not _warned_unavailable:
            _warned_unavailable = True
            log.warning("Typix: spellcheck disabled -- vendored enchant failed to load, see earlier traceback")
        return None
    if not lang_tag:
        log.warning("Typix: spellcheck skipped -- no input language tag could be determined")
        return None
    try:
        d = _get_dict(lang_tag)
        if d is None:
            ensure_dictionary_async(lang_tag)
            return None
        return bool(d.check(word))
    except Exception:
        # Never the word itself: the NVDA log must not hold what was typed.
        log.exception(f"check_word failed for language {lang_tag!r}")
        return None


def ensure_dictionary_async(lang_tag):
    """Kicks off a background download of the hunspell dictionary folder
    covering lang_tag if it's not already present/downloading. Silently
    does nothing if the language isn't available for download or enchant
    is unusable."""
    if not _ENCHANT_AVAILABLE or not lang_tag or not _dictionaries_dir:
        return
    download_tag = _resolve_download_tag(lang_tag)
    if download_tag is None:
        return
    with _lock:
        now = time.monotonic()
        last = _download_attempts.get(download_tag)
        if download_tag in _download_in_progress or (last is not None and now - last < DOWNLOAD_RETRY):
            return
        _download_attempts[download_tag] = now
        _download_in_progress.add(download_tag)
    thread = threading.Thread(
        target=_download_dictionary_worker, args=(download_tag, lang_tag), daemon=True
    )
    thread.start()


def _download_dictionary_worker(download_tag, original_lang_tag):
    try:
        _do_download(download_tag)
        _dict_cache.pop(original_lang_tag, None)  # allow re-lookup with fresh dictionary
    except Exception:
        log.exception(f"Dictionary download failed for {download_tag!r}")
    finally:
        with _lock:
            _download_in_progress.discard(download_tag)


def _do_download(lang_tag):
    req = urllib.request.Request(
        DICT_GITHUB_API_URL.format(lang_tag=lang_tag),
        headers={"User-Agent": "Typix-NVDA-addon"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        listing = json.loads(resp.read().decode("utf-8"))

    entries = [
        entry for entry in listing
        if os.path.splitext(entry.get("name", ""))[-1] in DICTIONARY_FILE_EXTS
    ]
    if not entries:
        return

    hunspell_dir = os.path.join(_dictionaries_dir, "hunspell")
    os.makedirs(hunspell_dir, exist_ok=True)

    for entry in entries:
        url = entry["download_url"]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != DICT_DOWNLOAD_HOST:
            raise ValueError(f"unexpected dictionary download address: {url}")
        dest = os.path.join(hunspell_dir, os.path.basename(entry["name"]))
        file_req = urllib.request.Request(url, headers={"User-Agent": "Typix-NVDA-addon"})
        with urllib.request.urlopen(file_req, timeout=30) as resp:
            data = resp.read()
        # Written whole, so a dropped connection can't leave a cut-off
        # dictionary for enchant to load.
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
