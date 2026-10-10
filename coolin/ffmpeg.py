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


def convert_segment(
    exe: str,
    input_path: str,
    output_path: str,
    fmt: str = "ogg",
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    bitrate: Optional[int] = None,
    audio_filter: Optional[str] = None,
    log=print,
) -> str:
    """Encode ONE segment of *input_path* as an upload-safe chunk file.

    fmt: 'wav' (lossless PCM), 'flac' (lossless), 'mp3' or 'ogg' (Opus).
    The extension of *output_path* must match the format.  Returns the codec.
    """
    if fmt == "wav":
        codec_args = ["-c:a", "pcm_s16le", "-ar", "48000"]
        codec = "pcm_s16le"
    elif fmt == "flac":
        codec_args = ["-c:a", "flac", "-compression_level", "8", "-ar", "48000"]
        codec = "flac"
    elif fmt == "mp3":
        # MP3 is the most battle-tested format on Roblox's import pipeline
        # (multiple devforum threads: MP3 succeeds where OGG/Opus fails).
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000"]
        codec = "libmp3lame"
    elif fmt == "ogg":
        encoders = available_encoders(exe)
        encoder = pick_encoder("auto", encoders)
        if encoder == "libopus" and bitrate:
            codec_args = ["-c:a", "libopus", "-b:a", str(int(bitrate)), "-ar", "48000"]
        else:
            codec_args = encoder_args(encoder)
        codec = encoder
    elif fmt == "vorbis":
        # The gtiiii.ogg profile: plain Ogg Vorbis, 44.1 kHz stereo, ~160 kbps
        # (nominal 160000 in the Vorbis ID header), zero metadata tags.
        # Vorbis is the format Roblox's own audio engine natively speaks, so
        # this profile uploads/accepts instantly with no transcode surprises.
        encoders = available_encoders(exe)
        if "libvorbis" in encoders:
            codec_args = ["-c:a", "libvorbis", "-q:a", "5"]
            codec = "libvorbis"
        else:
            codec_args = ["-c:a", "vorbis", "-q:a", "5", "-strict", "-2"]
            codec = "vorbis"
        codec_args += ["-ar", "44100", "-ac", "2", "-map_metadata", "-1"]
    else:
        raise ValueError(f"Unsupported chunk format: {fmt!r}")
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
        + codec_args
        + [output_path]
    )
    log(f"ffmpeg: {' '.join(_quote(arg) for arg in cmd)}")
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to convert the input file:\n" + result.stderr.strip()
        )
    return codec


def convert_bait(
    exe: str,
    input_path: str,
    output_path: str,
    fmt: str = "flac",
    duration: Optional[float] = None,
    bait_path: Optional[str] = None,
    cut_filter: str = "",
    log=print,
) -> str:
    """Encode a 'bait' asset: LEFT channel = bait, RIGHT channel = song with
    the spectral mask applied (low+mid cut).

    The Roblox preview / any plain player hears the bait (the masked song is
    faint >4 kHz sizzle under it).  The generated in-game script selects only
    the RIGHT channel with an AudioChannelSplitter and restores the song with
    chained AudioEqualizers.

    *bait_path*: an audio file to use as the bait (looped to cover the song);
    when None, a soft generated chime is used.  Returns the codec used.
    """
    bait_target_rms = -20.0
    if bait_path:
        bait_rms = probe_mean_volume(
            exe, ["-i", bait_path],
            "aformat=channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1")
        if bait_rms is None:
            bait_rms = -20.0
        bait_gain = bait_target_rms - bait_rms
        bait_input = ["-stream_loop", "-1", "-i", bait_path]
        bait_chain = (
            "[1:a]aresample=48000,aformat=channel_layouts=stereo,"
            "pan=mono|c0=0.5*c0+0.5*c1"
            f",volume={bait_gain:.2f}dB[bait]"
        )
    else:
        # Generated default bait: a soft pulsing chime, normalized to the
        # target level (sine source amplitude differs across ffmpeg builds,
        # so measure a sample and compute the exact gain).
        noise_dur = (duration + 1.0) if duration is not None else 720.0
        chime_probe = "sine=frequency=660:sample_rate=48000:duration=3"
        chime_rms = probe_mean_volume(
            exe, ["-f", "lavfi", "-i", chime_probe], "tremolo=f=0.6:d=0.4")
        if chime_rms is None:
            chime_rms = -23.0
        bait_gain = bait_target_rms - chime_rms
        bait_input = ["-f", "lavfi", "-i",
                      f"sine=frequency=660:sample_rate=48000:"
                      f"duration={noise_dur:.2f}"]
        bait_chain = f"[1:a]tremolo=f=0.6:d=0.4,volume={bait_gain:.2f}dB[bait]"

    graph = (
        "[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        "pan=mono|c0=0.5*c0+0.5*c1"
        + (f",{cut_filter}" if cut_filter else "")
        + "[song];"
        + bait_chain + ";"
        "[bait][song]join=inputs=2:channel_layout=stereo,"
        "alimiter=limit=0.98[out]"
    )
    if fmt == "flac":
        codec_args = ["-c:a", "flac", "-compression_level", "8", "-ar", "48000"]
        codec = "flac"
    elif fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000"]
        codec = "libmp3lame"
    else:
        raise ValueError(f"bait supports flac or mp3, got {fmt!r}")
    duration_args = ["-t", str(duration)] if duration is not None else []
    cmd = (
        [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        + ["-i", input_path]
        + bait_input
        + ["-filter_complex", graph, "-map", "[out]"]
        + duration_args
        + codec_args
        + [output_path]
    )
    log(f"ffmpeg: {' '.join(_quote(arg) for arg in cmd)}")
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to convert the input file:\n" + result.stderr.strip()
        )
    return codec


def probe_mean_volume(exe: str, input_args: Sequence[str], audio_filter: str) -> Optional[float]:
    """Mean (RMS) volume in dB of an input (file or lavfi source) after
    applying *audio_filter*.  Returns None if volumedetect reports nothing."""
    cmd = (
        [exe, "-hide_banner", "-nostdin"]
        + list(input_args)
        + ["-af", audio_filter + ",volumedetect", "-f", "null", "-"]
    )
    result = _run(cmd)
    for line in (result.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except (ValueError, IndexError):
                return None
    return None


def convert_mono_gate(
    exe: str,
    input_path: str,
    output_path: str,
    fmt: str = "flac",
    duration: Optional[float] = None,
    mask_depth_db: float = 18.0,
    noise_amplitude: float = 0.5,
    seed: int = 42,
    log=print,
) -> str:
    """Encode *input_path* so it is masked in STEREO but clean in MONO.

    Construction:  L = music + noise,  R = music - noise
      - Stereo playback (Roblox web preview / moderation): each ear hears the
        loud noise with the music buried *mask_depth_db* beneath it
        (psychoacoustic masking) -> sounds like plain noise.
      - Mono downmix (Roblox 3D sounds parented to a Part/Attachment):
        the anti-correlated noise cancels in L+R -> only the music remains.

    Returns the codec used.
    """
    # Measure the music's mono RMS so the mask depth is input-independent.
    music_af = "aformat=channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1"
    music_rms = probe_mean_volume(exe, ["-i", input_path], music_af)
    if music_rms is None:
        music_rms = -20.0
    # Measure the pink-noise masker's RMS at the chosen amplitude.
    noise_src = (
        f"anoisesrc=color=pink:sample_rate=48000:amplitude={noise_amplitude}"
        f":seed={seed}:duration=2"
    )
    noise_rms = probe_mean_volume(exe, ["-f", "lavfi", "-i", noise_src],
                                  "aformat=channel_layouts=mono")
    if noise_rms is None:
        noise_rms = -12.0
    music_gain = (noise_rms - mask_depth_db) - music_rms

    noise_dur = (duration + 1.0) if duration is not None else 720.0
    graph = (
        "[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        "pan=mono|c0=0.5*c0+0.5*c1"
        f",volume={music_gain:.2f}dB[m0];"
        "[m0]asplit=2[m1][m2];"
        f"anoisesrc=color=pink:sample_rate=48000:amplitude={noise_amplitude}"
        f":seed={seed}:duration={noise_dur},"
        "aformat=sample_fmts=fltp:channel_layouts=mono[n0];"
        "[n0]asplit=2[na][nb];"
        "[nb]pan=mono|c0=-1*c0[ninv];"
        "[m1][na]amix=inputs=2:duration=first:normalize=0[L];"
        "[m2][ninv]amix=inputs=2:duration=first:normalize=0[R];"
        "[L][R]amerge=inputs=2,alimiter=limit=0.98[out]"
    )
    if fmt == "flac":
        codec_args = ["-c:a", "flac", "-compression_level", "8", "-ar", "48000"]
        codec = "flac"
    elif fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000"]
        codec = "libmp3lame"
    else:
        raise ValueError(f"mono-gate supports flac or mp3, got {fmt!r}")
    duration_args = ["-t", str(duration)] if duration is not None else []
    cmd = (
        [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        + ["-i", input_path]
        + ["-filter_complex", graph, "-map", "[out]"]
        + duration_args
        + codec_args
        + [output_path]
    )
    log(f"ffmpeg: {' '.join(_quote(arg) for arg in cmd)}")
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to convert the input file:\n" + result.stderr.strip()
        )
    return codec


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


def decode_duration(exe: str, path: str) -> float:
    """Fully decode *path* (forced to 48 kHz stereo s16le) and return the
    length in seconds of the audio that is *really* inside the file.

    Unlike probe_duration (which reads metadata), this decodes every packet,
    so it sees through declared-duration tricks.
    """
    result = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-ar", "48000", "-ac", "2", "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg could not fully decode the file:\n"
            + result.stderr.decode("utf-8", "replace").strip()
        )
    return len(result.stdout) / (48000 * 2 * 2)


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg
