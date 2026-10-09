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
from coolin import pipeline
import coolin_cli

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


def make_long_wav(path: str, seconds: float = 425.0, rate: int = 8000) -> None:
    """Write a mono WAV longer than the 6:59 upload limit (small + fast)."""
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        chunk = bytearray()
        for i in range(rate):  # one second of a 440 Hz tone, tiled
            chunk += struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * i / rate)))
        for _ in range(int(seconds)):
            handle.writeframes(bytes(chunk))


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

    def test_parse_duration_limits_and_formats(self):
        """Duration parsing handles seconds, MM:SS, and enforces 6:59 (419s) max limit."""
        self.assertEqual(pipeline.parse_duration(419), 419.0)
        self.assertEqual(pipeline.parse_duration("419"), 419.0)
        self.assertEqual(pipeline.parse_duration(419.0), 419.0)
        self.assertEqual(pipeline.parse_duration("6:59"), 419.0)
        self.assertEqual(pipeline.parse_duration("0:02"), 2.0)
        self.assertEqual(pipeline.parse_duration("1:30"), 90.0)
        self.assertEqual(pipeline.parse_duration(2.5), 2.5)

        # Rejections: exceeding 6 min 59 seconds (419s), <= 0, or malformed
        for invalid in (420, "420", 419.1, "7:00", "6:60", 0, -1, "", "abc", "1:2:3:4"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    pipeline.parse_duration(invalid)

    def test_craft_stops_at_specified_duration(self):
        """Crafted output must declare the specified duration (e.g. 2.0s) and end
        Stream 1 with an EOS boundary so Discord stops, while keeping full audio for VLC."""
        craft_out = os.path.join(self.tmpdir.name, "tone_crafted_2s.ogg")
        result = pipeline.craft(
            self.wav_path,
            output_path=craft_out,
            fake_seconds=2.0,
            method="multistream",
            log=lambda _: None,
        )
        self.assertTrue(os.path.isfile(craft_out))
        self.assertAlmostEqual(result.declared_seconds, 2.0, delta=0.1)
        self.assertAlmostEqual(result.actual_seconds, TONE_SECONDS, delta=0.5)

        # OGG pages must have Stream 1 ending with EOS flag and Stream 2 starting with BOS flag
        data = self._read(craft_out)
        pages = ogg_util.parse_pages(data)
        self.assertTrue(all(ogg_util.page_crc_valid(data, p) for p in pages))
        # Find stream transition
        bos_pages = [i for i, p in enumerate(pages) if p.is_bos]
        self.assertGreaterEqual(len(bos_pages), 2, "Must contain chained streams for multi-stream trick")
        stream1_last_idx = bos_pages[1] - 1
        self.assertTrue(pages[stream1_last_idx].is_eos, "Stream 1 must end with EOS page")

        # Full VLC-style decode still produces the full audio
        pcm_bytes = ffmpeg_util.decoded_pcm_bytes(self.ffmpeg, craft_out)
        decoded_seconds = pcm_bytes / (48000 * 2 * 2)
        self.assertGreater(decoded_seconds, TONE_SECONDS * 0.85)

        # Granules in all streams must be strictly monotonic (no backward jumps/underflow)
        report = ogg_util.describe(data)
        self.assertTrue(report["granules_monotonic"], "Chained stream granules must be monotonic")
        self.assertLessEqual(report["full_audio_seconds"], pipeline.MAX_SECONDS)

    def test_craft_rejects_exceeding_max_duration(self):
        """Craft must reject durations longer than 6 min 59 seconds (419s)."""
        craft_out = os.path.join(self.tmpdir.name, "should_fail.ogg")
        with self.assertRaises(ValueError):
            pipeline.craft(self.wav_path, output_path=craft_out, fake_seconds=420)
        with self.assertRaises(ValueError):
            pipeline.craft(self.wav_path, output_path=craft_out, fake_seconds="7:00")

    def test_safe_encode_duration_caps_under_7_minutes(self):
        """safe_encode_duration must guarantee duration <= 419.00s."""
        self.assertLessEqual(pipeline.safe_encode_duration(419.0), 419.0)
        self.assertLessEqual(pipeline.safe_encode_duration(500.0), 419.0)
        self.assertAlmostEqual(pipeline.safe_encode_duration(419.0), 419.0 - (312 / 48000), places=4)

    def test_cli_handles_duration_formats_and_limits(self):
        """CLI accepts MM:SS format up to 6:59 and rejects > 6:59."""
        parser = coolin_cli.build_parser()
        args = parser.parse_args(["song.mp3", "-d", "6:59"])
        self.assertEqual(pipeline.parse_duration(args.seconds), 419.0)

        # Over max limit:
        with self.assertRaises(SystemExit):
            coolin_cli.main(["song.mp3", "-d", "7:00"])
        with self.assertRaises(SystemExit):
            coolin_cli.main(["song.mp3", "-d", "420"])

    def test_asset_name_length_limits(self):
        """Asset names must be between 1 and 50 characters for platform compatibility."""
        self.assertEqual(pipeline.MAX_ASSET_NAME_LENGTH, 50)
        self.assertEqual(pipeline.MIN_ASSET_NAME_LENGTH, 1)

        # Truncation of long names
        very_long = "A" * 80
        sanitized = pipeline.sanitize_asset_name(very_long)
        self.assertLessEqual(len(sanitized), 50)
        self.assertGreaterEqual(len(sanitized), 1)

        # Filename generation with suffix
        gen = pipeline.make_valid_asset_filename("Very Long Song Title Exceeding Fifty Characters Easily")
        self.assertLessEqual(len(gen), 50)
        self.assertGreaterEqual(len(gen), 1)

        # Empty or whitespace fallback
        self.assertEqual(pipeline.sanitize_asset_name("   "), "Audio")
        self.assertLessEqual(len(pipeline.make_valid_asset_filename("")), 50)

        # Craft produces a filename within 50 characters even with a long input path
        long_wav_name = os.path.join(self.tmpdir.name, ("Z" * 60) + ".wav")
        make_tone_wav(long_wav_name, seconds=1.0)
        result = pipeline.craft(long_wav_name, log=lambda _: None)
        out_stem = os.path.splitext(os.path.basename(result.output_path))[0]
        self.assertLessEqual(len(out_stem), 50)
        self.assertGreaterEqual(len(out_stem), 1)

    def test_invert_method_mono_cancellation(self):
        """Invert method must produce phase-inverted stereo that cancels out to near silence in mono."""
        out_path = os.path.join(self.tmpdir.name, "tone_invert.ogg")
        res = pipeline.craft(self.wav_path, output_path=out_path, method="invert", log=lambda _: None)
        self.assertTrue(os.path.isfile(out_path))
        self.assertIn("Invert Method", res.in_game_script)
        self.assertIn("sound:Play()", res.in_game_script)

        # Check mono cancellation
        cmd = [self.ffmpeg, "-hide_banner", "-y", "-i", out_path, "-af", "pan=mono|c0=0.5*c0+0.5*c1,volumedetect", "-f", "null", "-"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        mean_vol = None
        for line in p.stderr.splitlines():
            if "mean_volume:" in line:
                mean_vol = float(line.split("mean_volume:")[1].split("dB")[0].strip())
        self.assertIsNotNone(mean_vol)
        self.assertLess(mean_vol, -50.0, "Mono downmix must have at least 50 dB attenuation due to phase cancellation")

    def test_speed_method_physically_short_duration(self):
        """Speed method must physically truncate duration to target seconds and provide in-game PlaybackSpeed script."""
        out_path = os.path.join(self.tmpdir.name, "tone_speed.ogg")
        res = pipeline.craft(self.wav_path, output_path=out_path, method="speed", fake_seconds=2.0, log=lambda _: None)
        self.assertTrue(os.path.isfile(out_path))
        self.assertAlmostEqual(res.declared_seconds, 2.0, delta=0.5)
        self.assertIn("PlaybackSpeed", res.in_game_script)

        # Verify probe sees the physically short duration (cannot play past 2s)
        dur = ffmpeg_util.probe_duration(self.ffmpeg, out_path)
        self.assertIsNotNone(dur)
        self.assertAlmostEqual(dur, 2.0, delta=0.5)

    def test_chunked_method_splits_song_into_uploadable_chunks(self):
        """Chunked method: the song is split into consecutive chunk files that
        are each genuinely under the per-chunk limit, with a playlist script."""
        out_path = os.path.join(self.tmpdir.name, "chunks", "tone.ogg")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        res = pipeline.craft(self.wav_path, output_path=out_path, method="chunked",
                             fake_seconds=2.0, log=lambda _: None)
        # 6s song at ~2s chunks -> at least 3 chunks
        self.assertGreaterEqual(len(res.chunk_paths), 3)
        self.assertGreater(res.actual_seconds, TONE_SECONDS * 0.9)
        self.assertAlmostEqual(res.declared_seconds, 2.0, delta=0.1)

        total = 0.0
        for p in res.chunk_paths:
            self.assertTrue(os.path.isfile(p), f"missing chunk {p}")
            stem = os.path.splitext(os.path.basename(p))[0]
            self.assertLessEqual(len(stem), pipeline.MAX_ASSET_NAME_LENGTH)
            # Metadata scan: under the 2s chunk limit...
            probed = ffmpeg_util.probe_duration(self.ffmpeg, p)
            self.assertIsNotNone(probed)
            self.assertLessEqual(probed, 2.0)
            # ...and the decoded duration (what Roblox measures) too.
            decoded = ffmpeg_util.decode_duration(self.ffmpeg, p)
            self.assertLessEqual(decoded, 2.0)
            total += decoded
            report = ogg_util.describe(self._read(p))
            self.assertTrue(report["all_crcs_valid"])
            self.assertEqual(report["chained_streams"], 1)

        # No audio lost: the chunks tile the whole song.
        self.assertAlmostEqual(total, TONE_SECONDS, delta=0.5)

        # The playlist script references every chunk file.
        self.assertIn("CHUNK_IDS", res.in_game_script)
        self.assertIn("rbxassetid://", res.in_game_script)
        for p in res.chunk_paths:
            self.assertIn(os.path.basename(p), res.in_game_script)

    def test_chunked_method_allows_songs_longer_than_upload_limit(self):
        """A 425s song (over the 6:59 limit) becomes chunks that are each under
        the 6:59 per-chunk limit - the full song survives as chunk files."""
        long_wav = os.path.join(self.tmpdir.name, "long_song.wav")
        make_long_wav(long_wav, seconds=425.0)
        out_path = os.path.join(self.tmpdir.name, "chunks_long", "long.ogg")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        res = pipeline.craft(long_wav, output_path=out_path, method="chunked",
                             fake_seconds=pipeline.MAX_SECONDS, log=lambda _: None)
        self.assertGreater(res.actual_seconds, pipeline.MAX_SECONDS)
        self.assertGreaterEqual(len(res.chunk_paths), 2)
        for p in res.chunk_paths:
            probed = ffmpeg_util.probe_duration(self.ffmpeg, p)
            self.assertIsNotNone(probed)
            self.assertLessEqual(probed, pipeline.MAX_SECONDS)
            self.assertTrue(os.path.isfile(p))


if __name__ == "__main__":
    unittest.main()
