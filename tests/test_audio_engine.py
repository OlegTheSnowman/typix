import array
import os
import shutil
import tempfile
import unittest
import wave


import _addon  # noqa: F401  (makes "typix" importable)
from typix.audio_engine import AudioEngine, MAX_PITCH, OUTPUT_RATE, resample  # noqa: E402


def write_wav(path, channels, sampwidth, rate, frames):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(frames)


class TestAudioEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = AudioEngine()  # no nvwave outside NVDA: processing only

    def tearDown(self):
        self.engine.close()
        shutil.rmtree(self.tmp)

    def _stereo(self, pcm):
        out = array.array("h")
        out.frombytes(pcm)
        return out

    def test_mono_16bit_centre_splits_evenly(self):
        p = os.path.join(self.tmp, "m.wav")
        write_wav(p, 1, 2, OUTPUT_RATE, array.array("h", [10000] * 100).tobytes())
        out = self._stereo(self.engine._get_processed(p, 0.0, 1.0, 0.0))
        self.assertEqual(len(out), 200)
        self.assertEqual(out[0], 5000)
        self.assertEqual(out[1], 5000)

    def test_full_right_pan_silences_left(self):
        p = os.path.join(self.tmp, "m.wav")
        write_wav(p, 1, 2, OUTPUT_RATE, array.array("h", [10000] * 10).tobytes())
        self.engine.configure(1.0, True, 1.0)
        out = self._stereo(self.engine._get_processed(p, 1.0, 1.0, 0.0))
        self.assertEqual(out[0], 0)
        self.assertEqual(out[1], 10000)

    def test_panning_disabled_stays_centred(self):
        p = os.path.join(self.tmp, "m.wav")
        write_wav(p, 1, 2, OUTPUT_RATE, array.array("h", [10000] * 10).tobytes())
        self.engine.configure(1.0, False, 1.0)
        out = self._stereo(self.engine._get_processed(p, 1.0, 1.0, 0.0))
        self.assertEqual(out[0], out[1])

    def test_other_sample_rate_is_converted(self):
        # 22050 Hz must come out twice as many frames at 44100, or it plays
        # at double speed and an octave up.
        p = os.path.join(self.tmp, "r.wav")
        write_wav(p, 1, 2, 22050, array.array("h", [1000] * 1000).tobytes())
        out = self._stereo(self.engine._get_processed(p, 0.0, 1.0, 0.0))
        self.assertEqual(len(out) // 2, 2000)

    def test_8_24_and_32_bit_load(self):
        cases = {
            1: bytes([255] * 50),
            3: (b"\x00\x00\x40") * 50,
            4: (b"\x00\x00\x00\x40") * 50,
        }
        for width, frames in cases.items():
            p = os.path.join(self.tmp, f"w{width}.wav")
            write_wav(p, 1, width, OUTPUT_RATE, frames)
            out = self._stereo(self.engine._get_processed(p, 0.0, 1.0, 0.0))
            self.assertEqual(len(out), 100, width)
            self.assertGreater(out[0], 8000, width)

    def test_pitch_up_one_octave_halves_length(self):
        p = os.path.join(self.tmp, "m.wav")
        write_wav(p, 2, 2, OUTPUT_RATE, array.array("h", [100, -100] * 1000).tobytes())
        out = self._stereo(self.engine._get_processed(p, 0.0, 1.0, 12.0))
        self.assertEqual(len(out) // 2, 500)
        self.assertEqual(out[0], 50)
        self.assertEqual(out[1], -50)

    def test_empty_or_missing_file_returns_none(self):
        p = os.path.join(self.tmp, "empty.wav")
        open(p, "wb").close()
        self.assertIsNone(self.engine._get_processed(p, 0.0, 1.0, 0.0))
        self.assertIsNone(self.engine._get_processed(os.path.join(self.tmp, "nope.wav"), 0.0, 1.0, 0.0))

    def test_resample_identity(self):
        a = array.array("h", [1, 2, 3])
        self.assertIs(resample(a, 1, 1.0), a)

    def test_extreme_pitch_from_a_manifest_is_limited(self):
        # An unlimited pitch of -240 would make this about 100 billion frames.
        p = os.path.join(self.tmp, "m.wav")
        write_wav(p, 1, 2, OUTPUT_RATE, array.array("h", [1000] * 100).tobytes())
        out = self._stereo(self.engine._get_processed(p, 0.0, 1.0, -240.0))
        self.assertEqual(len(out) // 2, 100 * 2 ** int(MAX_PITCH / 12))

    def test_play_without_nvwave_is_a_no_op(self):
        self.engine.play_sound(os.path.join(self.tmp, "nope.wav"))


if __name__ == "__main__":
    unittest.main()
