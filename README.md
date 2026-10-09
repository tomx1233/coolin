# Coolin 🎧

**Audio file inserter → Multi-stream OGG converter for Discord and VLC.**

Coolin takes any audio file you insert (MP3, WAV, FLAC, M4A, …) and converts it
into a chained multi-stream OGG file designed so that:

| Player | What happens |
| --- | --- |
| **Discord's audio player** | Plays only the specified duration (default: **2.0s**), then stops at the stream boundary. |
| **VLC / full demuxers** | Play the entire song (up to the **6 minutes and 59 seconds** max limit). |

## How it works

1. **Stream 1 (Discord Preview)**: The first part of the audio is encoded for the specified duration (default: 2.0s, up to 6:59 / 419s) and explicitly terminates with an OGG `EOS` (end-of-stream) page boundary.
2. **Stream 2 (Full Song Remainder)**: The remaining audio of the track is encoded into a chained secondary logical bitstream (up to a maximum total length of 6 minutes and 59 seconds).
3. **Container Surgery**: The container's declared duration is matched to the proclaimed duration. Discord's embedded Chromium player stops at the Stream 1 EOS boundary, while VLC seamlessly continues into Stream 2 to play the entire song.
4. **Limits & Format Support**: Accepts duration inputs in seconds (`2.0`, `419`) or `MM:SS` (`6:59`). The maximum allowed duration and total song length is **6 minutes and 59 seconds** (419.0s).

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
3. Set the **Discord duration** in seconds (default `2.0`, max `6:59` / `419.0s`)
   and the codec (`auto` keeps Opus, the recommended choice).
4. Press **Convert to Discord OGG**.

Outputs are named `<original name>_discord.ogg`.

## Using the CLI

```bat
:: convert with defaults (2.0s duration, Opus)
python coolin_cli.py song.mp3

:: choose output path and duration (supports seconds or MM:SS up to 6:59)
python coolin_cli.py song.mp3 -o output.ogg -d 6:59

:: batch + force Vorbis codec
python coolin_cli.py a.mp3 b.flac -d 3 --codec vorbis

:: inspect an OGG file
python coolin_cli.py --verify song_discord.ogg
```

On Windows you can use `run_cli.bat` instead of `python coolin_cli.py`.

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
  pipeline.py        high-level convert + duration trimming workflow
  ffmpeg.py          ffmpeg discovery (PATH or imageio-ffmpeg) + conversion
  ogg.py             Ogg page parser, Ogg CRC-32, container utilities
tests/test_ogg.py    unit tests (CRC, duration limits, trimming integrity)
```

## Notes & disclaimer

- The maximum duration for any song is **6 minutes and 59 seconds** (419 seconds).
- Asset names (file stems) are automatically kept within **50 characters** (the 1–50 character limit for Roblox / Discord asset creation) to prevent "Asset name length is invalid" errors.
- Opus is the default on purpose: OGG/Opus is what Discord expects.
- Run the tests with `python -m unittest discover -s tests -v`.
