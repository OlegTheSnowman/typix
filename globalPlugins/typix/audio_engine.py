import wave
import array
import queue
import sys
import threading
import logging

log = logging.getLogger(__name__)

try:
    import nvwave
    import config as nvda_config
    _HAS_NVWAVE = True
except ImportError:
    _HAS_NVWAVE = False

POOL_SIZE = 6
OUTPUT_RATE = 44100
# The most the editor allows. A pack's manifest can say anything, and a very
# low pitch stretches a sound into gigabytes of memory.
MAX_PITCH = 24.0


def resample(samples, channels, ratio):
    """Linear-interpolation resample of an interleaved int16 array.
    ratio > 1 shortens the sound (plays it faster/higher), ratio < 1
    lengthens it. Used both for pitch shifting and for converting a WAV's
    own sample rate to the output rate, so the two can never disagree."""
    if abs(ratio - 1.0) < 1e-4:
        return samples
    n_frames = len(samples) // channels
    out_frames = int(n_frames / ratio)
    if out_frames < 1 or n_frames < 2:
        return samples
    out = array.array('h', bytes(out_frames * channels * 2))
    last = n_frames - 1
    for i in range(out_frames):
        pos = i * ratio
        idx = int(pos)
        if idx >= last:
            idx, frac = last, 0.0
        else:
            frac = pos - idx
        a = idx * channels
        b = a + channels if frac else a
        o = i * channels
        for ch in range(channels):
            s0 = samples[a + ch]
            out[o + ch] = int(s0 + (samples[b + ch] - s0) * frac)
    return out


def _clip(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


class AudioEngine:
    """
    Plays typing sounds with stereo panning and pitch shifting.

    Each WavePlayer in the pool has its own worker thread and queue, so
    a keystroke never pays for thread creation and a wake-up can never be
    lost between workers. Decoding and processing also happen on the
    worker, keeping NVDA's main thread free; the processed result is
    cached so repeated keystrokes cost a dictionary lookup.
    """

    def __init__(self):
        self.master_volume = 1.0
        self.panning_enabled = True
        self.panning_width = 0.8
        self._raw_cache = {}        # path -> (int16 array at OUTPUT_RATE, channels)
        self._processed_cache = {}  # (path, pan, volume, pitch) -> stereo bytes
        self._players = []
        self._queues = []
        self._workers = []
        self._next = 0
        self._create_pool()

    def _create_pool(self):
        if not _HAS_NVWAVE:
            return
        for _ in range(POOL_SIZE):
            try:
                player = nvwave.WavePlayer(
                    channels=2,
                    samplesPerSec=OUTPUT_RATE,
                    bitsPerSample=16,
                    outputDevice=nvda_config.conf["audio"]["outputDevice"],
                )
            except Exception:
                log.exception("Typix: failed to create WavePlayer")
                continue
            q = queue.SimpleQueue()
            t = threading.Thread(target=self._worker_loop, args=(player, q), daemon=True)
            self._players.append(player)
            self._queues.append(q)
            self._workers.append(t)
            t.start()

    def _worker_loop(self, player, q):
        while True:
            job = q.get()
            if job is None:
                return
            try:
                pcm = self._get_processed(*job)
                if pcm:
                    player.stop()
                    player.feed(pcm)
            except Exception:
                log.debug("Typix: playback failed", exc_info=True)

    def _load_raw(self, path):
        """Loads a WAV as an int16 array at OUTPUT_RATE plus its channel count."""
        cached = self._raw_cache.get(path)
        if cached is not None:
            return cached
        try:
            with wave.open(path, 'rb') as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                rate = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
        except Exception as e:
            log.error(f"Typix: failed to load {path}: {e}")
            return None
        samples = array.array('h')
        if sampwidth == 2:
            samples.frombytes(raw)
            if sys.byteorder != "little":
                samples.byteswap()
        elif sampwidth == 1:
            samples.extend((b - 128) << 8 for b in raw)
        elif sampwidth in (3, 4):
            # Keep the two most significant bytes of each sample.
            hi = sampwidth - 2
            samples.extend(
                int.from_bytes(raw[i + hi:i + sampwidth], "little", signed=True)
                for i in range(0, len(raw) - sampwidth + 1, sampwidth)
            )
        if not samples:
            log.error(f"Typix: unsupported or empty WAV {path} (sample width {sampwidth})")
            return None
        samples = resample(samples, channels, rate / OUTPUT_RATE)
        self._raw_cache[path] = (samples, channels)
        return self._raw_cache[path]

    def _get_processed(self, path, pan, volume, pitch):
        """Returns stereo int16 bytes for path with pan, volume and pitch applied."""
        pan = round(pan, 2)
        volume = round(volume, 2)
        pitch = round(max(-MAX_PITCH, min(MAX_PITCH, pitch)), 1)
        key = (path, pan, volume, pitch)
        pcm = self._processed_cache.get(key)
        if pcm is not None:
            return pcm
        loaded = self._load_raw(path)
        if not loaded:
            return None
        samples, channels = loaded
        if pitch:
            samples = resample(samples, channels, 2.0 ** (pitch / 12.0))
        # Linear crossfade: -1.0 is full left, 0 centre, 1.0 full right.
        effective_pan = pan * self.panning_width if self.panning_enabled else 0.0
        right = (max(-1.0, min(1.0, effective_pan)) + 1.0) / 2.0
        gain = volume * self.master_volume
        lg, rg = gain * (1.0 - right), gain * right
        # Mix any channel count down to stereo; stereo keeps its own sides,
        # anything wider uses its first two channels.
        n_frames = len(samples) // channels
        out = array.array('h', bytes(n_frames * 4))
        if channels == 1:
            for i, s in enumerate(samples):
                out[2 * i] = _clip(int(s * lg))
                out[2 * i + 1] = _clip(int(s * rg))
        else:
            for i in range(n_frames):
                a = i * channels
                out[2 * i] = _clip(int(samples[a] * lg))
                out[2 * i + 1] = _clip(int(samples[a + 1] * rg))
        pcm = out.tobytes()
        self._processed_cache[key] = pcm
        return pcm

    def play_sound(self, sound_path, pan=0.0, volume=1.0, pitch=0.0):
        """Non-blocking. pan: -1.0 left to 1.0 right. pitch: semitones."""
        if not sound_path or not self._queues:
            return
        q = self._queues[self._next]
        self._next = (self._next + 1) % len(self._queues)
        q.put((sound_path, pan, volume, pitch))

    def configure(self, master_volume, panning_enabled, panning_width):
        self.master_volume = max(0.0, min(1.0, master_volume))
        self.panning_enabled = bool(panning_enabled)
        self.panning_width = max(0.0, min(1.0, panning_width))
        self._processed_cache.clear()

    def clear_cache(self):
        self._raw_cache.clear()
        self._processed_cache.clear()

    def close(self):
        for q in self._queues:
            q.put(None)
        for t in self._workers:
            t.join(timeout=1.0)
        for player in self._players:
            try:
                player.close()
            except Exception:
                pass
        self._players.clear()
        self._queues.clear()
        self._workers.clear()
        self.clear_cache()
