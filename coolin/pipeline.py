"""High-level pipeline: insert an audio file, get a Discord-style OGG out."""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import ffmpeg as ffmpeg_util
from . import ogg as ogg_util

DEFAULT_FAKE_SECONDS = 2.0
DEFAULT_DURATION_SECONDS = 2.0
MAX_SECONDS = 419.0  # 6 minutes and 59 seconds (6 * 60 + 59)
MAX_DURATION_STR = "6:59"
MAX_ASSET_NAME_LENGTH = 50
MIN_ASSET_NAME_LENGTH = 1
OUTPUT_SUFFIX = "_discord"

METHOD_INVERT = "invert"
METHOD_SPEED = "speed"
METHOD_INVERT_SPEED = "invert_speed"
METHOD_MULTISTREAM = "multistream"
METHOD_CHUNKED = "chunked"
SUPPORTED_METHODS = (
    METHOD_INVERT,
    METHOD_SPEED,
    METHOD_INVERT_SPEED,
    METHOD_MULTISTREAM,
    METHOD_CHUNKED,
)

# Default per-chunk length for the chunked method: 10s is the audio upload
# limit for unverified Roblox accounts (verified accounts allow 6:59).
DEFAULT_CHUNK_SECONDS = 10.0
# Safety margin kept under the per-chunk limit so the Opus encoder's preskip
# padding (~6.5ms) can never push a chunk's measured duration over it.
CHUNK_SAFETY_SECONDS = 0.05


def sanitize_asset_name(name: str, max_length: int = MAX_ASSET_NAME_LENGTH) -> str:
    """Ensure an asset name is valid (1-50 characters, printable, trimmed of whitespace)."""
    if name.lower().endswith(".ogg"):
        name = name[:-4]
    clean = "".join(c for c in name if c.isprintable()).strip()
    if not clean:
        clean = "Audio"
    if len(clean) > max_length:
        clean = clean[:max_length].rstrip()
    if len(clean) < MIN_ASSET_NAME_LENGTH:
        clean = "Audio"
    return clean


def make_valid_asset_filename(
    base_name: str,
    suffix: str = OUTPUT_SUFFIX,
    max_length: int = MAX_ASSET_NAME_LENGTH,
) -> str:
    """Generate an asset filename (stem) that is at most *max_length* characters (default 50)."""
    base_clean = "".join(c for c in base_name if c.isprintable()).strip()
    if not base_clean:
        base_clean = "Audio"
    if len(base_clean + suffix) <= max_length:
        return base_clean + suffix
    avail = max_length - len(suffix)
    if avail > 0:
        truncated = base_clean[:avail].rstrip()
        if truncated:
            return truncated + suffix
    return base_clean[:max_length].rstrip() or "Audio"


def parse_duration(val: str | float | int) -> float:
    """Parse a duration string or number into seconds.

    Supports float/int seconds ('419', 419.0) or MM:SS format ('6:59').
    Validates that 0 < seconds <= MAX_SECONDS (419.0s / 6m 59s).
    """
    if isinstance(val, (int, float)):
        seconds = float(val)
    else:
        val_str = str(val).strip()
        if not val_str:
            raise ValueError("Duration cannot be empty")
        if ":" in val_str:
            parts = val_str.split(":")
            if len(parts) == 2:
                mins, secs = parts
                seconds = float(mins) * 60 + float(secs)
            elif len(parts) == 3:
                hrs, mins, secs = parts
                seconds = float(hrs) * 3600 + float(mins) * 60 + float(secs)
            else:
                raise ValueError(f"Invalid duration format: {val!r}")
        else:
            seconds = float(val_str)

    if seconds <= 0:
        raise ValueError("Duration must be greater than zero")
    if seconds > MAX_SECONDS:
        raise ValueError(
            f"Duration cannot exceed 6 minutes and 59 seconds ({MAX_SECONDS} seconds / {MAX_DURATION_STR})"
        )
    return seconds


@dataclass
class CraftResult:
    output_path: str
    encoder: str
    codec: str
    actual_seconds: float     # full audio that is really inside the file
    declared_seconds: float   # what Discord's player will see
    method: str = METHOD_INVERT
    in_game_script: str = ""
    chunk_paths: list = field(default_factory=list)  # set by the chunked method


def default_output_path(
    input_path: str,
    output_dir: Optional[str] = None,
    custom_name: Optional[str] = None,
    suffix: str = OUTPUT_SUFFIX,
    max_asset_length: int = MAX_ASSET_NAME_LENGTH,
) -> str:
    """Generate a valid output path ensuring the asset name (file stem) is <= 50 characters."""
    directory = output_dir if output_dir else os.path.dirname(input_path) or "."
    if custom_name:
        base = sanitize_asset_name(custom_name, max_asset_length)
    else:
        orig_base = os.path.splitext(os.path.basename(input_path))[0]
        base = make_valid_asset_filename(orig_base, suffix=suffix, max_length=max_asset_length)
    return os.path.join(directory, base + ".ogg")


def safe_encode_duration(seconds: float) -> float:
    """Adjust duration to account for Opus encoder 312-sample preskip padding (6.5ms @ 48kHz).
    Guarantees the encoded audio duration strictly stays <= MAX_SECONDS (00:06:59.00).
    """
    if seconds >= MAX_SECONDS:
        return max(0.1, MAX_SECONDS - (312 / 48000))
    return seconds


def craft(
    input_path: str,
    output_path: Optional[str] = None,
    fake_seconds: float = DEFAULT_FAKE_SECONDS,
    codec: str = "auto",
    method: str = METHOD_INVERT,
    speed_factor: Optional[float] = None,
    log: Callable[[str], None] = print,
    *,
    duration_seconds: Optional[float] = None,
    asset_name: Optional[str] = None,
) -> CraftResult:
    """Convert *input_path* using the chosen game-compatible audio method:

    - 'invert': Phase Inversion method. Inverts polarity of the right channel (L=+audio, R=-audio).
      In mono review or web preview, channels sum to complete silence (-90 dB cancellation),
      while in game it plays normally.
    - 'speed': PlaybackSpeed Inversion method. Speds up audio by a factor to fit target seconds
      (default 2.0s). The file is physically short so it stops at 2s in Discord and cannot be rejected
      for duration. In game, Sound.PlaybackSpeed = 1/factor plays the full song at 100% normal pitch.
    - 'invert_speed': Combines both Phase Inversion and Speed Inversion.
    - 'multistream': Chained multi-stream OGG (Stream 1 2s EOS + Stream 2 full track).
    - 'chunked': The bulletproof way past upload duration limits. Roblox transcodes audio on
      import and measures the DECODED duration, so no single-file trick can hide a long song.
      Instead the ENTIRE song (any length, even over 6:59) is split into consecutive chunk
      files that are each genuinely under the upload limit - nothing to detect - and a
      generated in-game Script plays them back to back as one continuous song at normal
      speed, pitch and quality.
    """
    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if output_path is None:
        output_path = default_output_path(input_path, custom_name=asset_name)
    else:
        out_dir = os.path.dirname(output_path) or "."
        out_base, ext = os.path.splitext(os.path.basename(output_path))
        if not ext:
            ext = ".ogg"
        if len(out_base) > MAX_ASSET_NAME_LENGTH or asset_name:
            chosen_name = asset_name if asset_name else out_base
            out_base = sanitize_asset_name(chosen_name, MAX_ASSET_NAME_LENGTH)
            output_path = os.path.join(out_dir, out_base + ext)

    output_path = os.path.abspath(output_path)
    if os.path.normcase(output_path) == os.path.normcase(input_path):
        raise ValueError("Output path must differ from the input path")

    raw_duration = duration_seconds if duration_seconds is not None else fake_seconds
    target_seconds = parse_duration(raw_duration)

    log(f"[1/4] Looking for ffmpeg...")
    exe = ffmpeg_util.find_ffmpeg()
    log(f"      using {exe}")

    log(f"[2/4] Analyzing input audio duration...")
    total_dur = ffmpeg_util.probe_duration(exe, input_path)
    if total_dur is None:
        tmp_probe_fd, tmp_probe_path = tempfile.mkstemp(suffix=".probe.ogg")
        os.close(tmp_probe_fd)
        try:
            ffmpeg_util.convert_to_ogg(
                exe, input_path, tmp_probe_path, codec, duration=MAX_SECONDS, log=lambda _: None
            )
            with open(tmp_probe_path, "rb") as h:
                p_data = h.read()
            p_pages = ogg_util.parse_pages(p_data)
            _, p_rate = ogg_util.detect_codec(p_data, p_pages)
            total_dur = p_pages[-1].granule / p_rate
        finally:
            try:
                os.remove(tmp_probe_path)
            except OSError:
                pass

    song_max = min(total_dur, MAX_SECONDS) if total_dur else MAX_SECONDS

    chunk_paths: list = []  # filled by the chunked method
    if method == METHOD_INVERT:
        log(f"[3/4] Applying Phase Invert Method (L=+audio, R=-audio; mono preview cancels to silence)...")
        enc_dur = safe_encode_duration(song_max)
        inv_filter = "pan=stereo|c0=c0|c1=-1*c0"
        encoder = ffmpeg_util.convert_to_ogg(
            exe, input_path, output_path, codec, duration=enc_dur, audio_filter=inv_filter, log=log
        )
        with open(output_path, "rb") as handle:
            out_data = handle.read()
        pages = ogg_util.parse_pages(out_data)
        out_codec, rate = ogg_util.detect_codec(out_data, pages)
        actual_seconds = pages[-1].granule / rate
        declared_seconds = actual_seconds
        in_game_script = (
            "-- Invert Method (Phase-Inverted Stereo)\n"
            "-- Cancels to silence in mono preview/review, plays in game.\n"
            "local sound = script.Parent\n"
            "sound.Volume = 1.0\n"
            "sound:Play()\n"
        )
    elif method == METHOD_SPEED:
        if speed_factor is not None and speed_factor > 0:
            factor = float(speed_factor)
        else:
            factor = max(1.0, song_max / max(0.1, target_seconds))
        out_dur = song_max / factor
        enc_dur = safe_encode_duration(out_dur)
        log(f"[3/4] Applying Speed-Invert Method ({factor:.2f}x speed -> {enc_dur:.2f}s duration)...")
        speed_filter = f"aresample=48000,asetrate=48000*{factor:.6f},aresample=48000"
        encoder = ffmpeg_util.convert_to_ogg(
            exe, input_path, output_path, codec, duration=enc_dur, audio_filter=speed_filter, log=log
        )
        with open(output_path, "rb") as handle:
            out_data = handle.read()
        pages = ogg_util.parse_pages(out_data)
        out_codec, rate = ogg_util.detect_codec(out_data, pages)
        declared_seconds = pages[-1].granule / rate
        actual_seconds = song_max
        playback_speed = 1.0 / factor
        in_game_script = (
            f"-- Speed-Invert Method ({factor:.2f}x sped up, file length: {declared_seconds:.2f}s)\n"
            f"-- In-Game Playback Script (paste into a Script under your Sound):\n"
            f"local sound = script.Parent\n"
            f"sound.PlaybackSpeed = {playback_speed:.4f} -- Restores normal pitch & full {actual_seconds:.2f}s song\n"
            f"sound:Play()\n"
        )
    elif method == METHOD_INVERT_SPEED:
        if speed_factor is not None and speed_factor > 0:
            factor = float(speed_factor)
        else:
            factor = max(1.0, song_max / max(0.1, target_seconds))
        out_dur = song_max / factor
        enc_dur = safe_encode_duration(out_dur)
        log(f"[3/4] Applying Invert + Speed Method ({factor:.2f}x speed + phase inversion)...")
        combo_filter = f"aresample=48000,asetrate=48000*{factor:.6f},aresample=48000,pan=stereo|c0=c0|c1=-1*c0"
        encoder = ffmpeg_util.convert_to_ogg(
            exe, input_path, output_path, codec, duration=enc_dur, audio_filter=combo_filter, log=log
        )
        with open(output_path, "rb") as handle:
            out_data = handle.read()
        pages = ogg_util.parse_pages(out_data)
        out_codec, rate = ogg_util.detect_codec(out_data, pages)
        declared_seconds = pages[-1].granule / rate
        actual_seconds = song_max
        playback_speed = 1.0 / factor
        in_game_script = (
            f"-- Invert + Speed Method ({factor:.2f}x speed, phase inverted)\n"
            f"-- Cancels to silence in mono, file duration: {declared_seconds:.2f}s.\n"
            f"-- In-Game Playback Script:\n"
            f"local sound = script.Parent\n"
            f"sound.PlaybackSpeed = {playback_speed:.4f}\n"
            f"sound.Volume = 1.0\n"
            f"sound:Play()\n"
        )
    elif method == METHOD_CHUNKED:
        # The bulletproof way past upload duration limits: every chunk file is
        # a genuinely short, clean, single-stream OGG that passes validation on
        # its own (real duration, real metadata - nothing to detect), and the
        # generated in-game Script stitches them back into the full song at
        # normal speed, pitch and quality.
        full_dur = total_dur if total_dur else song_max
        chunk_len = target_seconds
        # Keep every chunk just under the limit so the Opus encoder's preskip
        # padding (~6.5ms) can never push its measured duration over it.
        step = max(0.05, chunk_len - CHUNK_SAFETY_SECONDS)
        count = max(1, int(math.ceil(full_dur / step)))

        out_dir = os.path.dirname(output_path) or "."
        stem = os.path.splitext(os.path.basename(output_path))[0]
        if stem.lower().endswith(OUTPUT_SUFFIX):
            stem = stem[: -len(OUTPUT_SUFFIX)]
        # Leave room for the _cNNN suffix inside the 50-char asset-name limit.
        stem = sanitize_asset_name(stem, MAX_ASSET_NAME_LENGTH - 5)

        log(
            f"[3/4] Chunked Method: splitting the full {ogg_util.format_seconds(full_dur)} "
            f"song into {count} upload-safe chunk(s) of at most {chunk_len:.2f}s each..."
        )
        chunk_paths: list = []
        encoder = ""
        for i in range(1, count + 1):
            start = (i - 1) * step
            remaining = full_dur - start
            if remaining <= 0.02:
                break
            dur = min(step, remaining)
            chunk_path = os.path.join(out_dir, f"{stem}_c{i:03d}.ogg")
            encoder = ffmpeg_util.convert_to_ogg(
                exe, input_path, chunk_path, codec,
                start_time=start, duration=dur, log=log,
            )
            chunk_paths.append(chunk_path)
        if not chunk_paths:
            raise RuntimeError("Chunked method produced no chunk files")

        with open(chunk_paths[0], "rb") as handle:
            first_data = handle.read()
        first_pages = ogg_util.parse_pages(first_data)
        out_codec, _first_rate = ogg_util.detect_codec(first_data, first_pages)
        output_path = chunk_paths[0]
        declared_seconds = chunk_len
        actual_seconds = full_dur

        script_lines = [
            "-- Coolin Chunked Song Player (generated)",
            f"-- Full song: {ogg_util.format_seconds(full_dur)} split into {len(chunk_paths)} "
            f"chunk(s), each <= {chunk_len:.2f}s (upload-limit safe).",
            "-- 1) Upload every chunk file listed below to Roblox.",
            "-- 2) Replace each placeholder ID with your uploaded asset ID (keep the order!).",
            "-- 3) Put this Script inside a Part (or SoundService) - it plays the whole song.",
            "",
            "local CHUNK_IDS = {",
        ]
        for p in chunk_paths:
            script_lines.append(f'\t"rbxassetid://0", -- {os.path.basename(p)}')
        script_lines += [
            "}",
            "",
            "local LOOP = false      -- true: restart the whole song when it ends",
            "local VOLUME = 1        -- 0 to 1",
            "local PRESTART = 0.05   -- switch chunks this many seconds early (gapless)",
            "",
            'local RunService = game:GetService("RunService")',
            "",
            "local sounds = {}",
            "for i, id in ipairs(CHUNK_IDS) do",
            '\tlocal s = Instance.new("Sound")',
            '\ts.Name = "Chunk" .. i',
            "\ts.SoundId = id",
            "\ts.Volume = VOLUME",
            "\ts.Parent = script",
            "\tsounds[i] = s",
            "end",
            "",
            "local index = 0",
            "local current = nil",
            "local lastSwitch = 0",
            "",
            "local function playNext()",
            "\tif os.clock() - lastSwitch < 0.05 then return end",
            "\tlastSwitch = os.clock()",
            "\tindex += 1",
            "\tif index > #sounds then",
            "\t\tif LOOP then index = 1 else return end",
            "\tend",
            "\tif current then current:Stop() end",
            "\tcurrent = sounds[index]",
            "\tcurrent.TimePosition = 0",
            "\tcurrent:Play()",
            "end",
            "",
            "for _, s in ipairs(sounds) do",
            "\ts.Ended:Connect(function()",
            "\t\tif s == current then playNext() end",
            "\tend)",
            "end",
            "",
            "RunService.Heartbeat:Connect(function()",
            "\tif current and current.TimeLength > 0",
            "\t\tand current.TimePosition >= current.TimeLength - PRESTART then",
            "\t\tplayNext()",
            "\tend",
            "end)",
            "",
            "playNext()",
        ]
        in_game_script = "\n".join(script_lines) + "\n"
        log(
            f"      Wrote {len(chunk_paths)} chunk file(s); upload them all and paste "
            f"their asset IDs into the script below."
        )
    else:  # METHOD_MULTISTREAM
        if target_seconds >= song_max:
            log(
                f"[3/4] Song length ({song_max:.2f}s) <= target duration ({target_seconds:.2f}s); "
                f"converting clean single stream (capped at {ogg_util.format_seconds(song_max)})..."
            )
            enc_dur = safe_encode_duration(song_max)
            encoder = ffmpeg_util.convert_to_ogg(
                exe, input_path, output_path, codec, duration=enc_dur, log=log
            )
            with open(output_path, "rb") as handle:
                out_data = handle.read()
            pages = ogg_util.parse_pages(out_data)
            out_codec, rate = ogg_util.detect_codec(out_data, pages)
            actual_seconds = pages[-1].granule / rate
            declared_seconds = actual_seconds
            in_game_script = ""
        else:
            log(
                f"[3/4] Building multi-stream Discord OGG: "
                f"Stream 1 (0 to {target_seconds:.2f}s, Discord stops here), "
                f"Stream 2 ({target_seconds:.2f}s to {song_max:.2f}s, VLC full audio)..."
            )
            tmp_dir = os.path.dirname(output_path) or "."
            fd1, tmp_s1 = tempfile.mkstemp(suffix=".s1.ogg", dir=tmp_dir)
            os.close(fd1)
            fd2, tmp_s2 = tempfile.mkstemp(suffix=".s2.ogg", dir=tmp_dir)
            os.close(fd2)
            try:
                encoder = ffmpeg_util.convert_to_ogg(
                    exe, input_path, tmp_s1, codec, duration=target_seconds, log=log
                )
                rem_dur = song_max - target_seconds
                enc_rem = (
                    safe_encode_duration(song_max) - target_seconds
                    if song_max >= MAX_SECONDS
                    else rem_dur
                )
                ffmpeg_util.convert_to_ogg(
                    exe, input_path, tmp_s2, codec, start_time=target_seconds, duration=enc_rem, log=log
                )
                with open(tmp_s1, "rb") as h1:
                    s1_data = h1.read()
                with open(tmp_s2, "rb") as h2:
                    s2_data = h2.read()

                combined = s1_data + s2_data
                with open(output_path, "wb") as handle:
                    handle.write(combined)

                s1_pages = ogg_util.parse_pages(s1_data)
                s2_pages = ogg_util.parse_pages(s2_data)
                out_codec, s1_rate = ogg_util.detect_codec(s1_data, s1_pages)
                _, s2_rate = ogg_util.detect_codec(s2_data, s2_pages)
                s1_dur = s1_pages[-1].granule / s1_rate
                s2_dur = s2_pages[-1].granule / s2_rate
                declared_seconds = s1_dur
                actual_seconds = s1_dur + s2_dur
                in_game_script = ""
            finally:
                for p in (tmp_s1, tmp_s2):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    log(f"[4/4] Wrote {output_path}")
    log(
        f"      Preview/declared duration: "
        f"{ogg_util.format_seconds(declared_seconds)}."
    )
    log(
        f"      Total audio inside: "
        f"{ogg_util.format_seconds(actual_seconds)}."
    )
    if in_game_script:
        log("\n--- In-Game Playback Script ---")
        for line in in_game_script.strip().splitlines():
            log(f"      {line}")
        log("-------------------------------\n")

    return CraftResult(
        output_path=output_path,
        encoder=encoder,
        codec=out_codec,
        actual_seconds=actual_seconds,
        declared_seconds=declared_seconds,
        method=method,
        in_game_script=in_game_script,
        chunk_paths=chunk_paths if method == METHOD_CHUNKED else [],
    )


def verify(path: str, log: Callable[[str], None] = print) -> dict:
    """Print a diagnostic report for an existing OGG file."""
    with open(path, "rb") as handle:
        data = handle.read()
    report = ogg_util.describe(data)
    log(f"File:                {path}")
    log(f"Codec:               {report['codec']} @ {report['granule_rate']} Hz granule base")
    log(f"Ogg pages:           {report['pages']} (serial {report['serial']})")
    log(f"Declared duration:   {ogg_util.format_seconds(report['declared_seconds'])}")
    log(f"Audio really inside: {ogg_util.format_seconds(report['full_audio_seconds'])}")
    log(f"Last page EOS flag:  {report['last_page_is_eos']}")
    log(f"Granules monotonic:  {report['granules_monotonic']}")
    log(f"All page CRCs valid: {report['all_crcs_valid']}")
    if report.get("chained_streams", 1) > 1:
        log(f"Chained streams:     {report['chained_streams']}")
        log("Verdict:             Coolin-crafted multi-stream file (Discord stops at Stream 1, VLC plays full song).")
    elif report["declared_seconds"] is not None and report["full_audio_seconds"]:
        if not report["granules_monotonic"] and report["full_audio_seconds"] > report["declared_seconds"]:
            log("Verdict:             Coolin-crafted file (fake short duration).")
        else:
            log("Verdict:             Discord-ready OGG file (clean duration).")

    # See through the declared duration by decoding every packet.
    try:
        exe = ffmpeg_util.find_ffmpeg()
        real_seconds = ffmpeg_util.decode_duration(exe, path)
        log(f"Full-decode length:  {ogg_util.format_seconds(real_seconds)}")
        if report["declared_seconds"] is not None and real_seconds > report["declared_seconds"] * 1.5:
            log(
                "Verdict:             hidden audio detected (metadata says short, "
                f"really {ogg_util.format_seconds(real_seconds)} of audio inside)."
            )
    except Exception:
        pass
    return report
