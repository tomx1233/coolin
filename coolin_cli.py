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
            "Convert audio files into OGG files whose declared duration is "
            "rewritten (default: 2 seconds). Discord's audio player stops at "
            "the declared duration, VLC plays the whole song, and FMOD / the "
            "default Windows players refuse to play the file."
        ),
    )
    parser.add_argument("inputs", nargs="*", help="audio files to convert")
    parser.add_argument(
        "-o", "--output",
        help="output .ogg path (only valid with a single input file)",
    )
    parser.add_argument(
        "-d", "--seconds", type=float, default=pipeline.DEFAULT_FAKE_SECONDS,
        help=f"duration Discord should see, in seconds "
             f"(default: {pipeline.DEFAULT_FAKE_SECONDS})",
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
    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")

    failures = 0
    for index, input_path in enumerate(args.inputs, 1):
        if len(args.inputs) > 1:
            print(f"=== [{index}/{len(args.inputs)}] {input_path} ===")
        try:
            pipeline.craft(
                input_path,
                output_path=args.output,
                fake_seconds=args.seconds,
                codec=args.codec,
            )
        except Exception as exc:
            failures += 1
            print(f"error: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
