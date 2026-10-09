# Coolin 🎧

**Audio file inserter → Game-compatible & Discord OGG converter.**

Coolin takes any audio file you insert (MP3, WAV, FLAC, M4A, …) and converts it
using specialized game-compatible and preview methods:

| Method | What happens |
| --- | --- |
| **`invert` (Phase Invert)** | Stereo channels are placed 180° out of phase (`L = +audio, R = -audio`). When played in mono (e.g. web previews, moderation checks), the channels cancel to complete silence (-91 dB). Plays normally in-game / stereo. |
| **`speed` (Speed Invert)** | The audio is sped up so the physical file duration matches the proclaimed duration (e.g. 2.0s). Cannot play past the proclaimed seconds in Discord, and cannot trigger platform "audio duration too long" errors. In game, `Sound.PlaybackSpeed = 1/factor` plays the full song at 100% normal pitch. |
| **`invert_speed` (Combined)** | Both phase-inverted (silent in mono preview) AND sped up (short physical duration, cannot exceed length caps). |
| **`spoof` (Fake Duration)** | Makes Roblox (and any metadata scanner) **think the file is a short song when it's not**. Every Ogg granule position is proportionally rescaled so the file *declares* only the target duration (e.g. 10s or 6:59), while the complete song — even one longer than the 7-minute upload limit — stays inside, untouched and fully decodable. Upload validation sees the short duration; in game the full song plays at normal speed with no special setup. |
| **`multistream`** | Chained multi-stream OGG (Stream 1 stops at Discord 2s EOS; Stream 2 carries remainder for VLC). |

## How the Invert & Game Methods Work

### 1. Phase Inversion Method (`invert`)
- Audio channels are configured with opposite polarity (`c0=c0`, `c1=-1*c0`).
- When downmixed to mono by moderation scanners, web previewers, or single-speaker previews, `L + R` sums to zero (-91 dB attenuation).
- In-game (Roblox / 3D audio space or stereo), both channels are rendered distinctly without cancellation.

### 2. Speed Invert Method (`speed`)
- When uploading to platforms with short duration caps (e.g. 2s, 6s, 10s), the track is sped up by a known factor $N$ (e.g. $4\times, 8\times$, or matched to target duration).
- The exported file duration physically stops at the proclaimed seconds (e.g. 2.0s), making it impossible for Discord to play past 2 seconds or for uploaders to flag "audio duration too long".
- In game, paste the one-line Lua script into your Sound object:
  ```lua
  local sound = script.Parent
  sound.PlaybackSpeed = 0.25 -- (where 0.25 = 1 / 4x speed)
  sound:Play()
  ```
  The game's audio engine stretches the track back out to its full duration (up to 6:59) at 100% normal pitch and speed.

### 3. Spoof Method (`spoof`) — *Roblox thinks it's a short song when it's not*
- The complete song is encoded with **no length cap** (it can be longer than the 6:59 / 7-minute upload limit).
- Every Ogg page's granule position is then **proportionally rescaled** so the container *declares* only the target duration (e.g. `10s`, or `6:59` to disguise a 10-minute song). The declared value is kept just under the target so hard limit checks never see it rounded up.
- Because the granules stay perfectly monotonic, every parser — including ones that compute duration from granule deltas with unsigned arithmetic (the ones that previously produced "audio duration too long") — sees a short, clean, well-formed file.
- The audio packets themselves are never touched: a demuxer that decodes to EOF (and a server-side transcode) still gets the **entire song**, so it plays in full at normal speed in game — no `PlaybackSpeed` or special script needed:
  ```lua
  local sound = script.Parent
  sound.Volume = 1.0
  sound:Play()
  ```
- Verify what's really inside any crafted file with `python coolin_cli.py --verify file.ogg` — it reports both the declared (scanner) duration and the full-decode length.

## Requirements

- Python 3.8+ (tkinter ships with the standard Windows installer)
- Bundles ffmpeg via `imageio-ffmpeg`:

```bat
pip install -r requirements.txt
```

## Using the GUI (Windows)

Double-click **`run_gui.bat`** (or run `python coolin_gui.py`):

1. **Insert audio file(s)** — add one or more songs.
2. Select your **Method** (`invert`, `speed`, `invert_speed`, `spoof`, or `multistream`).
3. Set the target duration or speed multiplier (max `6:59` / `419.0s`).
4. Press **Convert to Discord OGG**.
5. Copy the generated in-game playback script directly from the log output!

## Using the CLI

```bat
:: Invert method (inaudible/cancels in mono, plays in game)
python coolin_cli.py song.mp3 -m invert

:: Speed method (physically 2 seconds long, full playback in game)
python coolin_cli.py song.mp3 -m speed -d 2

:: Invert + Speed combined
python coolin_cli.py song.mp3 -m invert_speed -d 2

:: Spoof method: Roblox thinks it's a 10 second song, full song inside & plays in game
:: (works even for songs longer than the 7 minute upload limit)
python coolin_cli.py song.mp3 -m spoof -d 10

:: Custom speed multiplier (e.g. 4x speed -> in-game PlaybackSpeed 0.25)
python coolin_cli.py song.mp3 -m speed -s 4.0

:: Inspect an OGG file
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
