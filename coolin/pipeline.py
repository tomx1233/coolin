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
OUTPUT_SUFFIX = "_discord"


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


def default_output_path(input_path: str) -> str:
    root, _ = os.path.splitext(input_path)
    return root + OUTPUT_SUFFIX + ".ogg"


def craft(
    input_path: str,
    output_path: Optional[str] = None,
    fake_seconds: float = DEFAULT_FAKE_SECONDS,
    codec: str = "auto",
    log: Callable[[str], None] = print,
    *,
    duration_seconds: Optional[float] = None,
) -> CraftResult:
    """Convert *input_path* into an OGG whose audio is stopped/trimmed at
    the specified duration (up to 6 minutes and 59 seconds), so Discord's
    audio player actually stops playing at the specified song length.
    """
    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if output_path is None:
        output_path = default_output_path(input_path)
    output_path = os.path.abspath(output_path)
    if os.path.normcase(output_path) == os.path.normcase(input_path):
        raise ValueError("Output path must differ from the input path")

    raw_duration = duration_seconds if duration_seconds is not None else fake_seconds
    target_seconds = parse_duration(raw_duration)

    log(f"[1/4] Looking for ffmpeg...")
    exe = ffmpeg_util.find_ffmpeg()
    log(f"      using {exe}")

    log(
        f"[2/4] Converting {os.path.basename(input_path)} to OGG "
        f"(stopping at {ogg_util.format_seconds(target_seconds)})..."
    )
    encoder = ffmpeg_util.convert_to_ogg(
        exe, input_path, output_path, codec, duration=target_seconds, log=log
    )

    log(f"[3/4] Verifying generated OGG container...")
    with open(output_path, "rb") as handle:
        out_data = handle.read()
    pages = ogg_util.parse_pages(out_data)
    out_codec, rate = ogg_util.detect_codec(out_data, pages)
    actual_seconds = pages[-1].granule / rate

    log(f"[4/4] Wrote {output_path}")
    log(
        f"      Discord will stop playing after "
        f"{ogg_util.format_seconds(actual_seconds)}."
    )
    return CraftResult(
        output_path=output_path,
        encoder=encoder,
        codec=out_codec,
        actual_seconds=actual_seconds,
        declared_seconds=actual_seconds,
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
    if report["declared_seconds"] is not None and report["full_audio_seconds"]:
        if not report["granules_monotonic"] and report["full_audio_seconds"] > report["declared_seconds"]:
            log("Verdict:             Coolin-crafted file (fake short duration).")
        else:
            log("Verdict:             Discord-ready OGG file (clean duration).")
    return report
