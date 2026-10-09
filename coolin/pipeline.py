"""High-level pipeline: insert an audio file, get a Discord-style OGG out."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
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
    log: Callable[[str], None] = print,
    *,
    duration_seconds: Optional[float] = None,
    asset_name: Optional[str] = None,
) -> CraftResult:
    """Convert *input_path* into a multi-stream Discord OGG file:
    Stream 1 plays for the specified duration (up to 6 minutes and 59 seconds)
    and terminates with an EOS boundary so Discord stops playback at the
    specified song length, while Stream 2 carries the remainder of the song
    (up to the 6m 59s maximum length) with valid monotonic granules so VLC
    and full demuxers play the entire track without platform duration errors.
    Ensures the asset name length does not exceed 50 characters.
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
        finally:
            for p in (tmp_s1, tmp_s2):
                try:
                    os.remove(p)
                except OSError:
                    pass

    log(f"[4/4] Wrote {output_path}")
    log(
        f"      Discord will stop after "
        f"{ogg_util.format_seconds(declared_seconds)} (end of stream 1)."
    )
    log(
        f"      VLC will play the full "
        f"{ogg_util.format_seconds(actual_seconds)} song."
    )
    return CraftResult(
        output_path=output_path,
        encoder=encoder,
        codec=out_codec,
        actual_seconds=actual_seconds,
        declared_seconds=declared_seconds,
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
    return report
