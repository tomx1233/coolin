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
            "Convert audio files into Discord-compatible OGG files that actually "
            "stop playing at the specified duration (default: 2 seconds, "
            "max: 6 minutes and 59 seconds)."
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
        "-d", "--seconds", default=str(pipeline.DEFAULT_FAKE_SECONDS),
        help=f"duration the song should play for in Discord in seconds or MM:SS "
             f"(default: {pipeline.DEFAULT_FAKE_SECONDS}, max: {pipeline.MAX_DURATION_STR} / {pipeline.MAX_SECONDS}s)",
    )
    parser.add_argument(
        "-m", "--method",
        choices=("invert", "speed", "invert_speed", "multistream"),
        default=pipeline.METHOD_INVERT,
        help=(
            "conversion method: 'invert' (phase inversion: cancels to silence in mono/preview, "
            "plays in-game), 'speed' (playback speed invert: short physical file, plays full song "
            "in-game via Sound.PlaybackSpeed), 'invert_speed' (both), or 'multistream' "
            "(chained OGG). Default: invert"
        ),
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
        seconds = pipeline.parse_duration(args.seconds)
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
                asset_name=args.asset_name,
            )
        except Exception as exc:
            failures += 1
            print(f"error: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
