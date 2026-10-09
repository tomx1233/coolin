# Coolin 🎧

**Audio file inserter → OGG converter with a rewritten declared duration.**

Coolin takes any audio file you insert (MP3, WAV, FLAC, M4A, …), converts it
to OGG, and then performs a tiny piece of Ogg container surgery: it rewrites
the file's **declared duration** (default: **2 seconds**) while leaving every
single audio packet inside the file untouched.

The result behaves differently depending on who plays it:

| Player                        | What happens                                                        |
| ----------------------------- | ------------------------------------------------------------------- |
| **Discord's audio player**    | Plays only the first ~2 seconds, then stops.                        |
| **VLC**                       | Plays the whole song normally.                                      |
| **FMOD**                      | Refuses to play the file (broken/non-monotonic granule positions + no native OGG/Opus decoding). |
| **Windows default players**   | Refuse the file (no native OGG/Opus codec in WMP/Groove/Media Player). |

It is a Python application with both a **GUI** and a **CLI**, and it runs on
Windows out of the box.

## How the trick works

An Ogg file is a chain of *pages*. The length of the audio is derived from
the **granule position** stored in the **last page** of the stream. Coolin:

1. transcodes your input to a clean OGG (Opus preferred — that is what
   Discord is happiest with, and it is undecodable by FMOD / the default
   Windows players),
2. overwrites the last page's granule position with
   `fake_seconds × sample_rate` (Opus granules are always 48 kHz),
3. recomputes that page's Ogg CRC-32 so the container stays "valid".

The timeline stored in all the *other* pages still runs to the end of the
song, so:

- players that trust the declared duration and stop at it (Discord runs on
  Chromium/Electron, which does exactly that) cut off after ~2 seconds,
- players that simply demux packets until the real end of file (VLC) play
  the whole song,
- strict demuxers (FMOD) choke on the now non-monotonic granule positions.

## Requirements

- Python 3.8+ (tkinter ships with the standard Windows installer)
- Nothing else — the dependency below bundles ffmpeg:

```bat
pip install -r requirements.txt
```

`requirements.txt` installs [`imageio-ffmpeg`](https://pypi.org/project/imageio-ffmpeg/),
which ships a static ffmpeg binary, so **you do not need to install ffmpeg
yourself on Windows**. If you already have ffmpeg on your PATH, Coolin uses
that one instead.

## Using the GUI (Windows)

Double-click **`run_gui.bat`** (or run `python coolin_gui.py`):

1. **Insert audio file(s)** — add one or more songs (any format ffmpeg understands).
2. Pick the output folder (or keep *"write next to each input file"*).
3. Set the **Discord duration** in seconds (default `2.0`) and the codec
   (`auto` keeps Opus, the recommended choice).
4. Press **Convert to Discord OGG**.

Outputs are named `<original name>_discord.ogg`.

## Using the CLI

```bat
:: convert with defaults (2.0s declared duration, Opus)
python coolin_cli.py song.mp3

:: choose output path and duration
python coolin_cli.py song.mp3 -o output.ogg -d 2

:: batch + force Vorbis codec
python coolin_cli.py a.mp3 b.flac -d 3 --codec vorbis

:: inspect an OGG file (is it a Coolin-crafted one?)
python coolin_cli.py --verify song_discord.ogg
```

On Windows you can use `run_cli.bat` instead of `python coolin_cli.py`.

Example `--verify` output:

```
File:                song_discord.ogg
Codec:               opus @ 48000 Hz granule base
Ogg pages:           28 (serial 3990938213)
Declared duration:   0:02.00
Audio really inside: 0:25.00
Last page EOS flag:  True
Granules monotonic:  False
All page CRCs valid: True
Verdict:             Coolin-crafted file (fake short duration).
```

## Building a standalone Coolin.exe

```bat
build_exe.bat
```

This uses PyInstaller and bundles the static ffmpeg binary; the result lands
in `dist\Coolin.exe`.

## Project layout

```
coolin_gui.py        tkinter GUI application (Windows-friendly)
coolin_cli.py        command line interface
coolin/
  pipeline.py        high-level convert + duration-rewrite workflow
  ffmpeg.py          ffmpeg discovery (PATH or imageio-ffmpeg) + conversion
  ogg.py             Ogg page parser, Ogg CRC-32, granule-position surgery
tests/test_ogg.py    unit tests (CRC, patching, full-decode integrity)
```

## Notes & disclaimer

- The fake duration must be **shorter** than the real song; Coolin rejects
  anything else.
- Opus is the default on purpose: OGG/Opus is what Discord expects, and it
  maximizes the "won't play anywhere strict" behavior (FMOD, WMP).
- Run the tests with `python -m unittest discover -s tests -v`.
- This is a novelty tool for your own files. Don't use it to confuse other
  people's players in ways they didn't sign up for.
