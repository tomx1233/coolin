"""FFmpeg discovery and conversion helpers.

Coolin needs an ffmpeg binary to decode whatever the user inserts and encode
it into an Ogg stream. It is found in this order:

1. ``ffmpeg`` on the system PATH.
2. The static ffmpeg shipped by the ``imageio-ffmpeg`` PyPI package, which
   means a plain ``pip install -r requirements.txt`` is enough on Windows.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional, Sequence

# Preferred codecs, in order. Opus first: Discord is happiest with OGG/Opus,
# and neither FMOD nor the default Windows players can decode it.
ENCODER_PREFERENCE = (
    ("opus", "libopus"),
    ("vorbis", "libvorbis"),
    ("vorbis", "vorbis"),  # native ffmpeg vorbis encoder, last resort
)

_INSTALL_HINT = (
    "No ffmpeg binary was found.\n"
    "Fix it with either of these:\n"
    "  1) pip install imageio-ffmpeg     (bundles a static ffmpeg, easiest on Windows)\n"
    "  2) install ffmpeg from https://www.gyan.dev/ffmpeg/builds/ and put it on PATH"
)


class FFmpegNotFound(RuntimeError):
    def __init__(self) -> None:
        super().__init__(_INSTALL_HINT)


def find_ffmpeg() -> str:
    """Return the path of a usable ffmpeg executable."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
    except ImportError:
        raise FFmpegNotFound() from None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - broken install edge case
        raise FFmpegNotFound() from exc


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )


def available_encoders(exe: str) -> set:
    result = _run([exe, "-hide_banner", "-encoders"])
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # encoder rows look like:  " A....D libopus   libopus Opus (codec opus)"
        if len(parts) >= 2 and parts[0] and set(parts[0]) <= set("AVS.XBD"):
            names.add(parts[1])
    return names


def pick_encoder(preferred: str, encoders: set) -> str:
    """Choose an encoder name for 'auto' / 'opus' / 'vorbis'."""
    for codec, encoder in ENCODER_PREFERENCE:
        if preferred != "auto" and codec != preferred:
            continue
        if encoder in encoders:
            return encoder
    raise RuntimeError(
        "This ffmpeg build has neither libopus, libvorbis nor the native "
        "vorbis encoder. Install a fuller ffmpeg build or 'pip install "
        "imageio-ffmpeg'."
    )


def encoder_args(encoder: str) -> List[str]:
    if encoder == "libopus":
        # Opus granule positions are defined at 48 kHz, so force 48 kHz out.
        return ["-c:a", "libopus", "-b:a", "96k", "-ar", "48000"]
    if encoder == "libvorbis":
        return ["-c:a", "libvorbis", "-q:a", "4"]
    return ["-c:a", "vorbis", "-q:a", "4", "-strict", "-2"]


def convert_to_ogg(
    exe: str,
    input_path: str,
    output_path: str,
    preferred_codec: str = "auto",
    duration: Optional[float] = None,
    start_time: Optional[float] = None,
    audio_filter: Optional[str] = None,
    log=print,
) -> str:
    """Transcode *input_path* to an Ogg file; returns the encoder used.
    If *start_time* is provided, starts transcoding from that offset.
    If *duration* is provided, limits output to that length.
    If *audio_filter* is provided, applies audio filter(s).
    """
    encoders = available_encoders(exe)
    encoder = pick_encoder(preferred_codec, encoders)
    seek_args = ["-ss", str(start_time)] if start_time is not None else []
    duration_args = ["-t", str(duration)] if duration is not None else []
    filter_args = ["-af", audio_filter] if audio_filter else []
    cmd = (
        [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        + seek_args
        + ["-i", input_path]
        + duration_args
        + ["-map", "0:a:0", "-vn"]
        + filter_args
        + encoder_args(encoder)
        + ["-f", "ogg", output_path]
    )
    log(f"ffmpeg: {' '.join(_quote(arg) for arg in cmd)}")
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to convert the input file:\n" + result.stderr.strip()
        )
    return encoder


def probe_duration(exe: str, path: str) -> Optional[float]:
    """Best-effort duration (seconds) as ffmpeg sees it; None if unknown."""
    result = _run([exe, "-hide_banner", "-i", path])
    for line in (result.stderr or "").splitlines():
        if "Duration:" in line:
            stamp = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
            if stamp == "N/A":
                return None
            try:
                hours, minutes, seconds = stamp.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except (ValueError, IndexError):
                return None
    return None


def decoded_pcm_bytes(exe: str, path: str) -> int:
    """Fully decode *path* and return the number of raw PCM bytes produced.

    This mimics a player that simply demuxes until EOF (like VLC does):
    it ignores the declared duration and consumes every packet in the file.
    """
    result = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-i", path,
         "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg could not fully decode the file:\n"
            + result.stderr.decode("utf-8", "replace").strip()
        )
    return len(result.stdout)


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg
