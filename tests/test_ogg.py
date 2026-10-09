"""Tests for the Ogg page surgery that powers Coolin.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import tempfile
import unittest
import wave

from coolin import ffmpeg as ffmpeg_util
from coolin import ogg as ogg_util

TONE_SECONDS = 6.0
FAKE_SECONDS = 2.0


def make_tone_wav(path: str, seconds: float = TONE_SECONDS, rate: int = 44100) -> None:
    """Write a small stereo frequency-sweep WAV (no external deps)."""
    with wave.open(path, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        total = int(seconds * rate)
        for i in range(total):
            freq = 220.0 + 660.0 * (i / total)
            value = int(12000 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", value, value // 2)
        handle.writeframes(bytes(frames))


class OggCraftingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = ffmpeg_util.find_ffmpeg()
        except ffmpeg_util.FFmpegNotFound:
            raise unittest.SkipTest("no ffmpeg binary available")

        cls.tmpdir = tempfile.TemporaryDirectory(prefix="coolin-test-")
        cls.addClassCleanup(cls.tmpdir.cleanup)
        cls.wav_path = os.path.join(cls.tmpdir.name, "tone.wav")
        make_tone_wav(cls.wav_path)

        cls.opus_path = os.path.join(cls.tmpdir.name, "tone-opus.ogg")
        cls.vorbis_path = os.path.join(cls.tmpdir.name, "tone-vorbis.ogg")
        for path, codec in ((cls.opus_path, "opus"), (cls.vorbis_path, "vorbis")):
            try:
                ffmpeg_util.convert_to_ogg(cls.ffmpeg, cls.wav_path, path,
                                           codec, log=lambda line: None)
            except RuntimeError as exc:
                raise unittest.SkipTest(f"encoder for {codec} unavailable: {exc}")

    # -- helpers -----------------------------------------------------------
    def _read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def _patch(self, data, seconds=FAKE_SECONDS):
        return ogg_util.inject_fake_duration(data, seconds)

    # -- tests ---------------------------------------------------------------
    def test_ffmpeg_output_passes_our_crc_and_parser(self):
        """Our CRC-32 implementation must agree with ffmpeg's."""
        for path in (self.opus_path, self.vorbis_path):
            data = self._read(path)
            pages = ogg_util.parse_pages(data)
            self.assertTrue(pages, f"no pages parsed from {path}")
            for page in pages:
                self.assertTrue(
                    ogg_util.page_crc_valid(data, page),
                    f"CRC mismatch on page at offset {page.offset} in {path}",
                )
            self.assertTrue(pages[-1].is_eos)

    def test_codec_detection(self):
        codec, rate = ogg_util.detect_codec(self._read(self.opus_path),
                                            ogg_util.parse_pages(self._read(self.opus_path)))
        self.assertEqual(codec, "opus")
        self.assertEqual(rate, 48000)

        vorbis_data = self._read(self.vorbis_path)
        codec, rate = ogg_util.detect_codec(vorbis_data, ogg_util.parse_pages(vorbis_data))
        self.assertEqual(codec, "vorbis")
        self.assertEqual(rate, 44100)

    def test_fake_duration_rewrite(self):
        for path in (self.opus_path, self.vorbis_path):
            with self.subTest(path=path):
                data = self._read(path)
                patched, info = self._patch(data)

                # Every page must still have a valid CRC after surgery.
                pages = ogg_util.parse_pages(patched)
                for page in pages:
                    self.assertTrue(ogg_util.page_crc_valid(patched, page))

                # Declared duration is the fake one, real audio untouched.
                self.assertAlmostEqual(info["declared_seconds"], FAKE_SECONDS, places=2)
                self.assertGreater(info["actual_seconds"], TONE_SECONDS * 0.9)

                # Non-monotonic granules: this is what breaks FMOD.
                granules = [p.granule for p in pages if p.granule >= 0]
                self.assertLess(granules[-1], max(granules[:-1]))

                # Everything except the last page's granule/CRC is identical.
                last = pages[-1]
                head_end = last.offset + 6
                self.assertEqual(patched[:head_end], data[:head_end])
                self.assertEqual(patched[last.offset + 26:], data[last.offset + 26:])

    def test_fake_longer_than_real_is_rejected(self):
        data = self._read(self.opus_path)
        with self.assertRaises(ValueError):
            self._patch(data, seconds=TONE_SECONDS + 10)

    def test_vlc_style_full_decode_still_contains_everything(self):
        """Decode the patched file all the way to EOF (what VLC effectively
        does) - it must still produce the full-length audio."""
        patched, _ = self._patch(self._read(self.opus_path))
        patched_path = os.path.join(self.tmpdir.name, "patched.ogg")
        with open(patched_path, "wb") as handle:
            handle.write(patched)

        pcm_bytes = ffmpeg_util.decoded_pcm_bytes(self.ffmpeg, patched_path)
        # ffmpeg decodes opus at 48 kHz stereo s16 -> 192000 bytes per second.
        decoded_seconds = pcm_bytes / (48000 * 2 * 2)
        self.assertGreater(decoded_seconds, TONE_SECONDS * 0.85)

    def test_declared_duration_as_seen_by_ffmpeg(self):
        """ffmpeg (and therefore Chromium/Discord) must report the fake
        duration for the patched file."""
        patched, _ = self._patch(self._read(self.opus_path))
        patched_path = os.path.join(self.tmpdir.name, "patched2.ogg")
        with open(patched_path, "wb") as handle:
            handle.write(patched)

        duration = ffmpeg_util.probe_duration(self.ffmpeg, patched_path)
        self.assertIsNotNone(duration)
        self.assertAlmostEqual(duration, FAKE_SECONDS, delta=0.5)

    def test_describe_reports_crafted_file(self):
        patched, _ = self._patch(self._read(self.opus_path))
        report = ogg_util.describe(patched)
        self.assertTrue(report["all_crcs_valid"])
        self.assertFalse(report["granules_monotonic"])
        self.assertAlmostEqual(report["declared_seconds"], FAKE_SECONDS, places=2)
        self.assertGreater(report["full_audio_seconds"], TONE_SECONDS * 0.9)


if __name__ == "__main__":
    unittest.main()
