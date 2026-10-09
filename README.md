# Coolin 🎧

**The best possible way to get a full song into Roblox at original quality — plus Discord preview tricks.**

Roblox's hard import limits (per the [official docs](https://create.roblox.com/docs/en-us/audio/assets)): single stream, `.mp3`/`.ogg`/`.wav`/`.flac`, **< 20 MB**, **< 7 minutes**, ≤ 48 kHz, mono/stereo — and Studio **transcodes every upload** (so it measures the *decoded* duration, not metadata).

## Every known method, compared

| # | Method | Quality | Beats duration limit? | Notes |
|---|---|---|---|---|
| 1 | **`chunked` — lossless chunk split + in-game playlist** ⭐ | **Original (lossless source)** | ✅ Any song length | What Coolin does by default. Each chunk is a *genuinely* valid short file — nothing to detect. Roblox's own transcode is the only lossy step. |
| 2 | `speed` — speed-up + `Sound.PlaybackSpeed = 1/N` | Degraded (N× narrower audio band, chipmunk preview) | ✅ | Extreme factors may be clamped by Roblox; long songs sound bad. |
| 3 | `invert` — phase inversion (L = +, R = −) | Original stereo | ❌ (hides from *mono moderation*, not duration) | Cancels to −91 dB in mono downmix; plays in stereo in-game. |
| 4 | `invert_speed` — 2 + 3 combined | Degraded | ✅ | Same limits as `speed`. |
| 5 | Metadata/granule spoofing (fake declared duration) | Original | ❌ **REJECTED** | Roblox decodes audio on import — measured duration is the real one. Coolin removed this method after it failed in practice. |
| 6 | `multistream` — chained OGG streams | Original | ❌ Roblox rejects multi-stream containers | Great for the Discord 2-second preview trick (Chromium stops at the first EOS); useless for Roblox. |
| 7 | One-file packing + `PlaybackRegion` (community) | Original | ❌ | Pack many sounds into one ≤7-min file and play a region per track — doesn't beat the 7-min wall. |
| 8 | `Ended → Play` chaining (community) | Original | ✅ | Audible gaps between parts unless preloaded and pre-switched — Coolin's generated script does both (preload + 0.05s early switch). |
| 9 | New Audio API (`AudioPlayer` + `Wire`) | Original | ❌ (same per-asset limits) | Modern playback graph; `AudioPlayer:Play()` resumes instead of restarting, so `Sound` remains simpler for gapless playlists. |
| 10 | Sample-rate/bitrate reduction (community) | Degraded | ❌ (only helps the 20 MB *size* limit) | Never needed with Coolin — the quality ladder auto-fits size losslessly first. |
| 11 | Alt accounts / group uploads (community) | n/a | ❌ (upload *quota* workaround only) | Tedious, ToS-gray; not a converter method. |

## The `chunked` method (default) — how it gets closest to the original

1. **No length cap.** The full song — any length, even 20 minutes — goes in.
2. **Fewest uploads.** Chunks default to the maximum legal length (6:59): a 3-minute song is *one* upload, a 10-minute song is *two*.
3. **Quality ladder per chunk** (auto, size-checked against the 20 MB limit after encoding):
   - **WAV** (lossless PCM 48 kHz) when the chunk is ≤ ~95 s
   - **FLAC** (lossless) whenever it fits under 20 MB
   - **OGG/Opus at up to 256 kbps** (transparent) as the guaranteed-fit fallback
4. **Nothing to detect.** Every chunk is a clean, single-stream file with real duration and real metadata — it passes import validation *on its own merits*.
5. **Gapless in-game playlist.** The generated Lua Script preloads every chunk (`ContentProvider:PreloadAsync`), switches 0.05 s early via `Heartbeat` (with an `Ended` fallback), and supports `LOOP`/`VOLUME`.

Since Roblox transcodes every upload anyway, feeding it **lossless** chunks means Roblox's own transcode is the *only* lossy step — i.e. **as close to the original song as the platform physically allows**.

### Usage

```bat
:: default: 6:59 chunks, auto quality (lossless when possible)
python coolin_cli.py song.mp3

:: same, explicitly
python coolin_cli.py song.mp3 -m chunked -d 6:59

:: force a chunk format (auto is recommended)
python coolin_cli.py song.mp3 -m chunked -f flac
```

Then: upload every chunk → paste the returned asset IDs into the script's `CHUNK_IDS` table (in order) → put the Script in a Part or SoundService.

If your account is under stricter duration limits than 7 minutes, set a smaller chunk length, e.g. `-d 10`.

## Other methods

```bat
:: phase inversion: silent in mono previews/moderation, plays in-game
python coolin_cli.py song.mp3 -m invert

:: speed inversion: physically short file, restored in-game via PlaybackSpeed
python coolin_cli.py song.mp3 -m speed -d 2

:: Discord preview trick: stops at 2s in Discord, VLC plays the full song
python coolin_cli.py song.mp3 -m multistream -d 2

:: inspect any crafted OGG (declared vs really-inside duration)
python coolin_cli.py --verify song_discord.ogg
```

## Requirements

- Python 3.8+ (tkinter ships with the standard Windows installer)
- `pip install -r requirements.txt` — bundles a static ffmpeg via [`imageio-ffmpeg`](https://pypi.org/project/imageio-ffmpeg/)

## Using the GUI (Windows)

Double-click **`run_gui.bat`** (or run `python coolin_gui.py`):

1. **Insert audio file(s)** — add one or more songs.
2. **Method** defaults to `chunked` (best). Set the per-chunk length (default 6:59).
3. Press **Convert** — chunk files and the playlist script appear in the log.

## Project layout

```
coolin_gui.py        tkinter GUI application
coolin_cli.py        command line interface
coolin/
  pipeline.py        high-level convert + methods (chunked/invert/speed/multistream)
  ffmpeg.py          ffmpeg discovery + conversion (ogg/wav/flac segments)
  ogg.py             Ogg page parser, Ogg CRC-32, container utilities
tests/test_ogg.py    unit tests (CRC, duration limits, chunking integrity)
```

## Notes & disclaimer

- Import limits per the [Roblox audio assets docs](https://create.roblox.com/docs/en-us/audio/assets): < 7 min, < 20 MB, ≤ 48 kHz, mono/stereo, single stream, mp3/ogg/wav/flac. ID-verified accounts: 2,000 free uploads / 30 days; unverified: 100.
- Asset (file) names are kept within the 1–50 character limit automatically.
- Only upload audio you have the rights to use.
- Run the tests with `python -m unittest discover -s tests -v`.
