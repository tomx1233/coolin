#!/usr/bin/env python3
"""Coolin CLI - turn audio files into "2 seconds on Discord" OGG files.

Examples:
    python coolin_cli.py song.mp3
    python coolin_cli.py song.mp3 -o output.ogg -d 2
    python coolin_cli.py a.mp3 b.flac -d 3 --codec vorbis
    python coolin_cli.py --verify some_discord.ogg
"""

from __future__ import annotations

import argparse
import sys

from coolin import __version__, pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coolin",
        description=(
            "Convert audio into Roblox-ready assets. Default method 'vorbis': "
            "replicates the field-tested gtiiii.ogg profile (plain Ogg Vorbis "
            "44.1 kHz stereo ~160 kbps) - Roblox's native audio format, accepted "
            "immediately on upload. Other methods: 'single' (lossless FLAC), "
            "'chunked' (any-length songs as upload-safe chunks + playlist), and "
            "hiding methods 'eqmask'/'bait'."
        ),
    )
    parser.add_argument("inputs", nargs="*", help="audio files to convert")
    parser.add_argument(
        "-o", "--output",
        help="output .ogg path (only valid with a single input file)",
    )
    parser.add_argument(
        "-n", "--name", "--asset-name", dest="asset_name",
        help="custom asset name (max 50 chars, auto-shortened if needed)",
    )
    parser.add_argument(
        "-d", "--seconds", default=None,
        help=f"duration in seconds or MM:SS (for 'chunked': per-chunk upload length, "
             f"default {pipeline.DEFAULT_CHUNK_SECONDS}s / {pipeline.MAX_DURATION_STR} = fewest "
             f"uploads; for the other methods: Discord preview duration, default "
             f"{pipeline.DEFAULT_FAKE_SECONDS}. Max: {pipeline.MAX_DURATION_STR} / {pipeline.MAX_SECONDS}s)",
    )
    parser.add_argument(
        "-m", "--method",
        choices=("vorbis", "single", "chunked", "eqmask", "bait", "monogate", "invert", "speed", "invert_speed", "multistream"),
        default=pipeline.METHOD_VORBIS,
        help=(
            "conversion method: 'vorbis' (RECOMMENDED: replicates the field-tested gtiiii.ogg "
            "profile - plain Ogg Vorbis 44.1 kHz stereo ~160 kbps, no metadata - the format "
            "Roblox's audio engine natively speaks, so uploads are accepted immediately; the "
            "generated script includes the official fixes for the plays-on-web-but-not-in-game "
            "bug), "
            "'single' (ONE clean asset at original pitch & speed - lossless "
            "FLAC or 320k MP3, capped at 6:58 to dodge Roblox's near-limit upload bug; longer "
            "songs are trimmed), 'chunked' (splits the full song - any length, even over 6:59 - "
            "into lossless upload-safe chunk files and generates an in-game Script that plays "
            "them as one continuous song at original quality), "
            "'invert' (phase inversion: cancels to silence in mono/preview, plays in-game), "
            "'eqmask' (RECOMMENDED for hiding: everything below 4 kHz is cut 40 dB, so the "
            "preview hears only faint sizzle - in game a chained AudioEqualizer script restores "
            "the song; works for any channel handling, unlike monogate), "
            "'bait' (decoy: LEFT channel plays a clean bait sound - your own file via --bait or "
            "a generated chime - while the RIGHT channel hides the spectrally masked song; the "
            "generated in-game script drops the bait channel and restores the song), "
            "'monogate' (masked in STEREO via anti-correlated noise; relies on Roblox's 3D "
            "mono downmix summing channels, which newer engine versions may not do - prefer "
            "'eqmask'), "
            "'speed' (playback speed invert: short physical file, plays full song in-game via "
            "Sound.PlaybackSpeed), 'invert_speed' (both), 'multistream' (chained OGG for the "
            "Discord preview trick). Default: single"
        ),
    )
    parser.add_argument(
        "--pan-hz", type=float, default=None, metavar="HZ",
        help="bake an ear-to-ear panning effect at this rate into the output "
             "(e.g. 25 for 25 Hz). Applied by 'single', 'eqmask', 'chunked' and the "
             "Discord methods; for 'bait' the generated script pans in game instead; "
             "not applied for 'monogate' (it would break the noise cancellation).",
    )
    parser.add_argument(
        "--bait", metavar="AUDIO",
        help="audio file to use as the bait/decoy for the 'bait' method (looped to cover "
             "the song; default: a generated soft chime)",
    )
    parser.add_argument(
        "--mask-depth", type=float, default=18.0, metavar="DB",
        help="mask depth in dB for the 'monogate' method: how far the music sits under "
             "the noise in the stereo preview (default 18). Higher = stronger masking "
             "but quieter in-game playback.",
    )
    parser.add_argument(
        "-f", "--chunk-format", choices=pipeline.CHUNK_FORMATS, default="auto",
        help="chunk audio format for the 'chunked' method: 'auto' picks the best quality that "
             "fits the 20 MB upload limit (lossless WAV -> lossless FLAC -> max-bitrate OGG), "
             "or force 'wav'/'flac'/'ogg'. Default: auto",
    )
    parser.add_argument(
        "-s", "--speed-factor", type=float, default=None,
        help="speed multiplier for 'speed' / 'invert_speed' method (e.g. 4.0 for 4x). "
             "Default: auto-calculated from --seconds",
    )
    parser.add_argument(
        "--codec", choices=("auto", "opus", "vorbis"), default="auto",
        help="Ogg codec to encode with (default: auto -> prefers Opus)",
    )
    parser.add_argument(
        "--verify", metavar="OGG",
        help="do not convert; print a diagnostic report for an OGG file",
    )
    parser.add_argument(
        "--version", action="version", version=f"coolin {__version__}",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verify:
        try:
            pipeline.verify(args.verify)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if not args.inputs:
        parser.error("give at least one input file (or use --verify)")
    if args.output and len(args.inputs) > 1:
        parser.error("-o/--output can only be used with a single input file")
    if args.asset_name and len(args.inputs) > 1:
        parser.error("-n/--name can only be used with a single input file")
    try:
        if args.seconds is None:
            if args.method == pipeline.METHOD_CHUNKED:
                raw_seconds = pipeline.DEFAULT_CHUNK_SECONDS
            elif args.method in (pipeline.METHOD_SINGLE, pipeline.METHOD_MONOGATE,
                                 pipeline.METHOD_EQMASK, pipeline.METHOD_BAIT,
                                 pipeline.METHOD_VORBIS):
                raw_seconds = pipeline.SINGLE_MAX_SECONDS
            else:
                raw_seconds = pipeline.DEFAULT_FAKE_SECONDS
        else:
            raw_seconds = args.seconds
        seconds = pipeline.parse_duration(raw_seconds)
    except ValueError as exc:
        parser.error(str(exc))

    failures = 0
    for index, input_path in enumerate(args.inputs, 1):
        if len(args.inputs) > 1:
            print(f"=== [{index}/{len(args.inputs)}] {input_path} ===")
        try:
            pipeline.craft(
                input_path,
                output_path=args.output,
                fake_seconds=seconds,
                codec=args.codec,
                method=args.method,
                speed_factor=args.speed_factor,
                chunk_format=args.chunk_format,
                mask_depth=args.mask_depth,
                bait_path=args.bait,
                pan_hz=args.pan_hz,
                asset_name=args.asset_name,
            )
        except Exception as exc:
            failures += 1
            print(f"error: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
