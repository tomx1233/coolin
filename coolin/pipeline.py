"""High-level pipeline: insert an audio file, get a Discord-style OGG out."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

from . import ffmpeg as ffmpeg_util
from . import ogg as ogg_util

DEFAULT_FAKE_SECONDS = 2.0
OUTPUT_SUFFIX = "_discord"


@dataclass
class CraftResult:
    output_path: str
    encoder: str
    codec: str
    actual_seconds: float     # full audio that is really inside the file
    declared_seconds: float   # what Discord's player will believe


def default_output_path(input_path: str) -> str:
    root, _ = os.path.splitext(input_path)
    return root + OUTPUT_SUFFIX + ".ogg"


def craft(
    input_path: str,
    output_path: Optional[str] = None,
    fake_seconds: float = DEFAULT_FAKE_SECONDS,
    codec: str = "auto",
    log: Callable[[str], None] = print,
) -> CraftResult:
    """Convert *input_path* into an OGG whose declared duration is
    *fake_seconds*, while the whole song stays inside the file.
    """
    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if output_path is None:
        output_path = default_output_path(input_path)
    output_path = os.path.abspath(output_path)
    if os.path.normcase(output_path) == os.path.normcase(input_path):
        raise ValueError("Output path must differ from the input path")

    log(f"[1/4] Looking for ffmpeg...")
    exe = ffmpeg_util.find_ffmpeg()
    log(f"      using {exe}")

    log(f"[2/4] Converting {os.path.basename(input_path)} to OGG...")
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".full.ogg", dir=os.path.dirname(output_path) or "."
    )
    os.close(tmp_fd)
    try:
        encoder = ffmpeg_util.convert_to_ogg(exe, input_path, tmp_path, codec, log)
        with open(tmp_path, "rb") as handle:
            full_data = handle.read()
        full_pages = ogg_util.parse_pages(full_data)
        full_codec, rate = ogg_util.detect_codec(full_data, full_pages)
        actual_seconds = full_pages[-1].granule / rate

        log(
            f"[3/4] Rewriting last-page granule position: "
            f"{actual_seconds:.2f}s of real audio, declared duration -> "
            f"{fake_seconds:.2f}s"
        )
        patched, info = ogg_util.inject_fake_duration(full_data, fake_seconds)

        with open(output_path, "wb") as handle:
            handle.write(patched)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    log(f"[4/4] Wrote {output_path}")
    log(
        f"      Discord/Chromium will stop after "
        f"{ogg_util.format_seconds(info['declared_seconds'])}, "
        f"VLC will play the full "
        f"{ogg_util.format_seconds(info['actual_seconds'])}."
    )
    return CraftResult(
        output_path=output_path,
        encoder=encoder,
        codec=info["codec"],
        actual_seconds=info["actual_seconds"],
        declared_seconds=info["declared_seconds"],
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
        if report["full_audio_seconds"] > report["declared_seconds"]:
            log("Verdict:             Coolin-crafted file (fake short duration).")
        else:
            log("Verdict:             normal Ogg file.")
    return report
