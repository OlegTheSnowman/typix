import io
import json
import logging
import os
import shutil
import tempfile
import unittest
import unittest.mock


import _addon  # noqa: F401  (makes "typix" importable)
from typix import spellcheck  # noqa: E402


class TestSpellcheckWordBoundary(unittest.TestCase):
    def test_check_word_returns_none_without_dictionary_dir_configured(self):
        result = spellcheck.check_word("test", "xx_XX_not_a_real_language")
        self.assertIsNone(result)

    def test_check_word_handles_empty_word(self):
        result = spellcheck.check_word("", "en_US")
        self.assertIsNone(result)

    def test_check_word_never_raises_on_enchant_failure(self):
        for _ in range(3):
            spellcheck.check_word("hello", "zz_ZZ")



class FakeDict:
    def __init__(self, words):
        self.words = words

    def suggest(self, word):
        return self.words


class TestFailuresAndDownloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patcher = unittest.mock.patch.object(spellcheck, "_dictionaries_dir", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        spellcheck._download_attempts.clear()

    def _serve(self, listing, files):
        def urlopen(req, timeout=None):
            url = req.full_url
            if url.startswith("https://api.github.com/"):
                return io.BytesIO(json.dumps(listing).encode())
            return io.BytesIO(files[url])
        return unittest.mock.patch("urllib.request.urlopen", urlopen)

    def test_no_enchant_gives_no_verdict_instead_of_raising(self):
        with unittest.mock.patch.object(spellcheck, "_ENCHANT_AVAILABLE", False), \
                unittest.mock.patch.object(spellcheck, "_warned_unavailable", False):
            self.assertIsNone(spellcheck.check_word("hello", "en_US"))

    def test_typed_word_never_reaches_the_log(self):
        class Broken:
            def check(self, word):
                raise RuntimeError("boom")

            def suggest(self, word):
                raise RuntimeError("boom")
        with unittest.mock.patch.dict(spellcheck._dict_cache, {"xx_LOG": Broken()}), \
                self.assertLogs(spellcheck.log, logging.ERROR) as logs:
            spellcheck.check_word("secretword", "xx_LOG")
            spellcheck.suggestions("secretword", "xx_LOG")
        self.assertNotIn("secretword", "\n".join(logs.output))

    def test_download_writes_dictionary_under_its_plain_name(self):
        url = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/uk_UA/uk_UA.dic"
        listing = [{"name": "../../uk_UA.dic", "download_url": url}]
        with self._serve(listing, {url: b"data"}):
            spellcheck._do_download("uk_UA")
        self.assertEqual(os.listdir(os.path.join(self.tmp, "hunspell")), ["uk_UA.dic"])

    def test_download_refuses_any_other_host(self):
        listing = [{"name": "uk_UA.dic", "download_url": "https://attacker.invalid/uk_UA.dic"}]
        with self._serve(listing, {}), self.assertRaises(ValueError):
            spellcheck._do_download("uk_UA")

    def test_failed_download_is_not_retried_on_every_word(self):
        started = []
        with unittest.mock.patch.object(spellcheck, "_ENCHANT_AVAILABLE", True), \
                unittest.mock.patch.object(spellcheck, "_resolve_download_tag", lambda tag: tag), \
                unittest.mock.patch.object(spellcheck, "_download_dictionary_worker",
                                           lambda tag, lang: (started.append(tag), spellcheck._download_in_progress.discard(tag))):
            for _ in range(5):
                spellcheck.ensure_dictionary_async("uk_UA")
        self.assertEqual(started, ["uk_UA"])


class TestSuggestionsAndIgnoreRules(unittest.TestCase):
    def setUp(self):
        self._saved = dict(spellcheck._dict_cache)

    def tearDown(self):
        spellcheck._dict_cache.clear()
        spellcheck._dict_cache.update(self._saved)

    def _with(self, words):
        spellcheck._dict_cache["xx_TEST"] = FakeDict(words)

    def _words(self, word):
        return [s for s, _what in spellcheck.suggestions(word, "xx_TEST")]

    def test_suggestions_follow_the_words_case(self):
        self._with(["hello", "hell", "hello", "Helot"])
        self.assertEqual(self._words("helo"), ["hello", "hell", "Helot"])
        self.assertEqual(self._words("Helo"), ["Hello", "Hell", "Helot"])
        self.assertEqual(self._words("HELO"), ["HELLO", "HELL", "HELOT"])

    def test_suggestions_drop_the_word_itself(self):
        self._with(["helo", "hello"])
        self.assertEqual(self._words("helo"), ["hello"])

    def test_what_is_wrong(self):
        self._with(["hello", "help", "held", "hole", "helloworld"])
        got = dict(spellcheck.suggestions("helo", "xx_TEST"))
        self.assertEqual(got["hello"], "missing l")
        self.assertEqual(got["help"], "p instead of o")
        self.assertEqual(got["held"], "d instead of o")
        self.assertEqual(got["helloworld"], "")
        self._with(["the", "there"])
        got = dict(spellcheck.suggestions("teh", "xx_TEST"))
        self.assertEqual(got["the"], "e and h swapped")
        self._with(["cat"])
        self.assertEqual(dict(spellcheck.suggestions("catt", "xx_TEST"))["cat"], "extra t")
        self.assertEqual(dict(spellcheck.suggestions("Catt", "xx_TEST"))["Cat"], "extra t")

    def test_code_and_links_are_recognised(self):
        for token in ("https://example.com", "me@mail.org", "my_var", "file.txt", "C:\\dir", "and/or", "camelCase,"):
            self.assertTrue(spellcheck.looks_like_code(token), token)
        for token in ("hello", "Hello,", "end.", "(word)", "don't"):
            self.assertFalse(spellcheck.looks_like_code(token), token)

    def test_no_dictionary_no_suggestions(self):
        self.assertEqual(spellcheck.suggestions("helo", "zz_NONE"), [])

    def test_words_with_digits_and_single_letters_get_no_verdict(self):
        self._with([])
        self.assertIsNone(spellcheck.check_word("abc123", "xx_TEST"))
        self.assertIsNone(spellcheck.check_word("x", "xx_TEST"))


if __name__ == "__main__":
    unittest.main()
