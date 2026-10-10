# Coolin 🎧

**One asset, original pitch & speed — the closest a full song can get to Roblox, plus Discord preview tricks.**

Roblox's hard import limits (per the [official docs](https://create.roblox.com/docs/en-us/audio/assets)): single stream, `.mp3`/`.ogg`/`.wav`/`.flac`, **< 20 MB**, **< 7 minutes**, ≤ 48 kHz, mono/stereo — and Studio **transcodes every upload** (so it measures the *decoded* duration, not metadata).

### What the research found (the "secret" is: no trick was needed)

- **7 minutes applies to everyone** — there is no 10-second tier for private uploads (that limit is only for marketplace/public distribution). 100 uploads/month unverified, 2,000 verified.
- **Opus-in-Ogg uploads are fragile on Roblox** — *"the audio engine expects ogg-vorbis"* (devforum); many "duration too long" / "asset creation failed" reports are actually codec/container handling issues, and **MP3 is the most upload-reliable format** per multiple community threads.
- **Uploads near the 7:00 limit are a known bug zone** ("Cannot upload audio despite meeting requirements" — official bug thread, with random rejections). Stay at ≤ 6:58 and use Studio's Asset Manager; retry if the website flakes.
- **Metadata tricks cannot work, ever**: Roblox re-encodes every upload and stores *their* transcode — the game plays their file, and the duration check decodes. Anything hidden inside a container is either measured (rejected) or discarded.
- **Video assets are worse** for audio: 5-minute cap, 2,000 Robux each, 13+ ID-verified only.
- Therefore for songs **≤ 6:58** the optimal upload is a *plain, clean, maximally-compatible file*: **lossless FLAC** (falls back to **320 kbps MP3** if the song won't fit 20 MB losslessly) — which is exactly what the `single` method produces.

## The `vorbis` method — replicating `gtiiii.ogg` (default)

Forensic analysis of the uploaded `gtiiii.ogg` (4.37 MB, 3:48): it is a **structurally perfect, completely ordinary Ogg Vorbis file** — single stream, 44.1 kHz stereo, nominal 160 kbps in the Vorbis ID header, encoded by *Xiph.Org libVorbis I 20120203*, zero metadata tags, ~4 KB pages, all CRCs valid, monotonic granules, BOS+EOS, nothing hidden. There is **no container trick in it at all** — and that is precisely why it works:

1. **Instant accept.** Vorbis is the format Roblox's own audio engine natively speaks (a Roblox engineer on devforum: *"the audio engine expects ogg-vorbis"*). A plain Vorbis file at mid-range parameters (nothing near the 7 min / 20 MB / 48 kHz limits) parses and transcodes with zero edge cases — no moderation roulette, no "asset creation failed".
2. **Plays on web but not in-game** is *not a property of the file* — it matches Roblox's **documented private-audio bug** (devforum: private audio shows a **0:00 length** and stays silent when you *paste the sound ID*, but plays when **inserted from Toolbox → Inventory**). Two official fixes:
   - **Studio → Toolbox → Inventory (Creator) → find the audio → Insert** (never paste the ID by hand), or
   - **create.roblox.com → your audio asset → '···' → Asset Permissions → add your experience** to the allowed list.

The `vorbis` method replicates the profile for **any** song you feed it — Ogg Vorbis, 44.1 kHz stereo, ~160 kbps (nominal 160000, verified from the encoded ID header), input metadata stripped, clean single-stream container — and prints the in-game script with both fixes above baked in:

```bat
python coolin_cli.py song.mp3            :: default method is vorbis
python coolin_cli.py song.mp3 -m vorbis  :: same, explicit
```

### The `audiocode` method — a code embedded IN the audio file that plays different files in-game

Audio can't execute scripts, but it can *carry data* — and the new Audio API can read it back in real time. `audiocode` embeds a command stream inside the audio file itself:

```
RIGHT channel = the music (normal, full-band)
LEFT  channel = near-silence + short quiet marker tones at your cue times
                (one frequency per command: file 2 → 1500 Hz, file 3 → 1750 Hz, …)
```

The generated **LocalScript** (client-side — audio analysis is disabled on the server) wires the asset's left channel into an `AudioAnalyzer`, reads `GetSpectrum()` every frame, and the moment a marker frequency appears it switches the audible `AudioPlayer` to the corresponding file in `PLAYLIST`. Because the tones are *audible content*, they **survive Roblox's transcode** — the code genuinely travels with the uploaded file, and the same generic script plays any coded asset.

```bat
:: at 30s switch to file 2, at 90s to file 3, at 150s back to the coded file's music
python coolin_cli.py song.mp3 -m audiocode --cue 30:2 --cue 90:3 --cue 150:1
```

Workflow: convert → upload this file **and** the other audio files → paste all their asset IDs into `PLAYLIST` in the generated script → put the LocalScript in SoundService. Notes: the preview hears the music in the right ear with faint ticks (the markers) in the left — that's the code; up to 16 files per coded asset; encoded in the instant-accept Vorbis profile.

### The `turbo` method — sped up in every viewer, normal in Roblox

The file is **physically sped up** (default 2×: speed *and* pitch together — a pure resample), so every plain audio viewer — Discord's preview, browsers, media players, and the Roblox website itself — plays it fast and chipmunked. The speed is real audio content, so Roblox's transcode keeps it. In game, **one property** on a classic `Sound` is the exact mathematical inverse:

```lua
sound.PlaybackSpeed = 0.5  -- 1/factor: restores the ORIGINAL speed and pitch
```

Why not a codec-level trick? Roblox re-encodes every upload, so a container/sample-rate *lie* gets normalized away in the transcode — and Roblox's 48 kHz rate ceiling blocks declaring a 2× rate anyway. Content-level speed plus one property achieves exactly the requested behavior: sped up in viewers, normal in game.

Notes:

- Factor: `-s/--speed-factor` (default 2; must be 1 < factor ≤ 4). Higher = sillier preview and more length headroom, but the in-game treble ceiling is 22050/factor Hz (11 kHz at 2×, 14.7 kHz at 1.5×).
- Bonus: the file is factor-times shorter, so one ≤ 6:58 asset holds up to **13:56 of real music at 2×** (`-d` caps the FILE duration, as with the other single-asset methods).
- No script is needed at all — you can just set `PlaybackSpeed` in the Properties panel of a Sound. The generated script additionally embeds the official fixes for the *plays-on-web-but-not-in-game* private-audio bug.
- The new `AudioPlayer` API has no speed control; the classic `Sound` does.

```bat
:: 2x chipmunk in every viewer, normal speed + pitch in game
python coolin_cli.py song.mp3 -m turbo

:: 3x instead (up to 20:54 of real music in one 6:58 asset)
python coolin_cli.py song.mp3 -m turbo -s 3
```

## Every known method, compared

| # | Method | Quality | Beats duration limit? | Notes |
|---|---|---|---|---|
| 1 | **`vorbis` — the gtiiii.ogg instant-accept profile** ⭐ (default) | ~160 kbps Vorbis | ✅ up to 6:58 | Replicates the field-tested file: plain Ogg Vorbis 44.1 kHz stereo, nominal 160 kbps, metadata stripped — the format Roblox's audio engine natively speaks. Uploads are accepted immediately. Script includes the official fixes for the *plays-on-web-but-not-in-game* bug. |
| 2 | **`audiocode` — a code embedded in the audio that plays different files in-game** | Original + code | ✅ up to 6:58 | Marker tones on the LEFT channel (music on the RIGHT) form an embedded command stream. The generated LocalScript decodes them via `AudioAnalyzer:GetSpectrum()` and switches between the files in a `PLAYLIST` at exactly the coded moments. The code travels *inside* the audio file and survives Roblox's transcode. |
| 3 | **`turbo` — sped up in every viewer, normal in Roblox** ⭐ (decoy) | Original speed & pitch in game (treble ≤ 11 kHz at 2×) | ✅ up to 6:58 file = **13:56 real at 2×** | The file is physically 2× faster (pitch + speed, a pure resample): Discord, browsers, media players and the Roblox website all play chipmunk. In game ONE property — `Sound.PlaybackSpeed = 0.5` — is the exact inverse: original speed and pitch. Factor 1–4 via `-s`; encoded in the instant-accept Vorbis profile. |
| 4 | **`single` — one clean lossless asset** | **Original (lossless FLAC)** | ✅ up to 6:58 | One asset, original pitch & speed, no tricks. FLAC → 320k MP3 fallback; 6:58 cap dodges the near-limit upload bug. |
| 5 | **`chunked` — lossless chunk split + in-game playlist** | **Original (lossless source)** | ✅ Any song length | Each chunk is a *genuinely* valid short file — nothing to detect. Roblox's own transcode is the only lossy step. Use this for songs longer than 6:58. |
| 6 | **`eqmask` — spectral mask + in-game EQ restore** ⭐ (for hiding) | Original (restored in-game) | ✅ up to 6:58 | **The robust hiding method.** Everything below 4 kHz is cut 40 dB at encode → the preview/moderation hears only faint sizzle (no vocals, no melody). The generated script chains 4 `AudioEqualizer`s (+10 dB Low+Mid each) to restore the song in-game. Channel-independent — works for 2D, 3D, mono, stereo, left-only, volumetric. |
| 7 | **`bait` — decoy channel + in-game channel-select** ⭐ (decoy) | Original (restored in-game) | ✅ up to 6:58 | **Plays a bait in the preview, the song in game.** LEFT channel = a clean bait (your own file via `--bait`, or a generated soft chime); RIGHT channel = the spectrally masked song. The generated script selects only the RIGHT channel (`AudioChannelSplitter` → `AudioChannelMixer` → EQ chain). Deterministic — no reliance on legacy 3D downmix behavior. |
| 8 | **`monogate` — masked in stereo, clean in mono** | Original (in-game) | ✅ up to 6:58 | The **inverse of phase-inversion**: `L = music + noise, R = music − noise`. The stereo web preview/moderation hears only pink noise (music masked ~18 dB beneath); in-game 3D sounds play as **mono** (L+R), cancelling the noise → clean music. Sound must be parented to a Part/Attachment. **Unreliable: newer engines keep 3D sounds directional/stereo — prefer `eqmask`.** |
| 9 | `speed` — speed-up + `Sound.PlaybackSpeed = 1/N` | Degraded (N× narrower audio band, chipmunk preview) | ✅ | Extreme factors may be clamped by Roblox; long songs sound bad. |
| 10 | `invert` — phase inversion (L = +, R = −) | Original stereo | ❌ (hides from *mono moderation*, not duration) | Cancels to −91 dB in mono downmix; plays in stereo in-game. |
| 11 | `invert_speed` — 2 + 3 combined | Degraded | ✅ | Same limits as `speed`. |
| 12 | Metadata/granule spoofing (fake declared duration) | Original | ❌ **REJECTED** | Roblox decodes audio on import — measured duration is the real one. Coolin removed this method after it failed in practice. |
| 13 | `multistream` — chained OGG streams | Original | ❌ Roblox rejects multi-stream containers | Great for the Discord 2-second preview trick (Chromium stops at the first EOS); useless for Roblox. |
| 14 | One-file packing + `PlaybackRegion` (community) | Original | ❌ | Pack many sounds into one ≤7-min file and play a region per track — doesn't beat the 7-min wall. |
| 15 | `Ended → Play` chaining (community) | Original | ✅ | Audible gaps between parts unless preloaded and pre-switched — Coolin's generated script does both (preload + 0.05s early switch). |
| 16 | New Audio API (`AudioPlayer` + `Wire`) | Original | ❌ (same per-asset limits) | Modern playback graph; `AudioPlayer:Play()` resumes instead of restarting, so `Sound` remains simpler for gapless playlists. |
| 17 | Sample-rate/bitrate reduction (community) | Degraded | ❌ (only helps the 20 MB *size* limit) | Never needed with Coolin — the quality ladder auto-fits size losslessly first. |
| 18 | Alt accounts / group uploads (community) | n/a | ❌ (upload *quota* workaround only) | Tedious, ToS-gray; not a converter method. |

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
:: default: the gtiiii.ogg instant-accept profile (Ogg Vorbis 44.1 kHz 160 kbps)
python coolin_cli.py song.mp3

:: lossless single asset instead (FLAC, or 320k MP3 if too big)
python coolin_cli.py song.mp3 -m single

:: hidden in the preview, restored in-game by the EQ script (RECOMMENDED)
python coolin_cli.py song.mp3 -m eqmask

:: channel-based alternative (only if your game's 3D audio sums to mono)
python coolin_cli.py song.mp3 -m monogate

:: stronger masking (quieter in-game), or weaker (louder in-game)
python coolin_cli.py song.mp3 -m monogate --mask-depth 24

:: sped up (chipmunk) in every viewer, normal speed + pitch in game
python coolin_cli.py song.mp3 -m turbo

:: keep the WHOLE song when it's longer than 6:58: chunked split instead
python coolin_cli.py song.mp3 -m chunked -d 6:59

:: force a chunk format (auto is recommended)
python coolin_cli.py song.mp3 -m chunked -f flac
```

Then: upload every chunk → paste the returned asset IDs into the script's `CHUNK_IDS` table (in order) → put the Script in a Part or SoundService.

If your account is under stricter duration limits than 7 minutes, set a smaller chunk length, e.g. `-d 10`.

### The `bait` method — preview plays a decoy, in-game plays the song

The closest thing to the "notepad++ bait file" idea that actually survives Roblox's transcode: **the difference must live in the audio itself, and the game must be able to deterministically select it.** The new Audio API's `AudioChannelSplitter` makes that possible — it exposes the asset's **Left** and **Right** channels as separate wireable pins:

```
LEFT  channel = bait (clean, full-band, ~-20 dB)   ← what the preview plays
RIGHT channel = song with the eqmask 40 dB cut     ← inaudible under the bait
```

The generated in-game script then does, deterministically:

```lua
AudioPlayer → AudioChannelSplitter (take "Right" only)
            → AudioChannelMixer    (spread to both ears)
            → 4 × AudioEqualizer   (+40 dB restore)
            → AudioDeviceOutput
```

- **Preview / moderation / any plain player**: hears the bait — a soft chime by default, or your own audio file (`--bait funnyvoice.mp3` / GUI "Bait audio" row), looped to cover the whole song and level-normalized.
- **In game**: the script drops the bait channel entirely and restores the song.
- Because channel selection happens through the Audio API wire graph (not the legacy 3D downmix), it works the same for 2D and 3D setups.
- **Proof files before uploading:** play the output file normally = what the preview hears (bait); play `<song>_test_ingame.wav` = what the game hears (restored song).
- Measured on a 30s song: bait at −19.8 dB on the left, masked song 37 dB beneath it, restored song at −17.7 dB in-game.

```bat
:: default: generated soft chime as the bait
python coolin_cli.py song.mp3 -m bait

:: use your own decoy audio
python coolin_cli.py song.mp3 -m bait --bait innocent_clip.mp3

:: 25 Hz ear-to-ear panning on any method
python coolin_cli.py song.mp3 --pan-hz 25
```

### The `eqmask` method — inaudible in the preview, restored in-game (recommended for hiding)

`monogate` relied on Roblox's 3D mono downmix *summing* the stereo channels — but newer engine versions keep 3D sounds **directional/stereo** (per devforum: "stereo audio seems to abruptly swap between the left and right channels... roblox may be using a dot product"), which breaks channel-cancellation tricks. `eqmask` depends on **no channel behavior at all**:

1. **At encode time**, everything below ~4 kHz is cut by **40 dB** (`lowshelf` at 200 Hz + a wide peaking cut up to 4 kHz). The uploaded asset contains only faint >4 kHz sizzle — no vocals, no melody, unrecognizable in the preview and to moderation.
2. **In game**, the generated script chains **4 `AudioEqualizer` instances** (new Audio API), each with `MidGain = 10` and `LowGain = 10` (`MidRange = NumberRange.new(200, 4000)`), wired `AudioPlayer → eq×4 → AudioDeviceOutput`. Each instance boosts at most +10 dB per band, so four layers restore the full +40 dB.
3. Because the transform is a **pure linear EQ**, it survives Roblox's transcode (loudness normalization is a uniform gain — the *relative* spectrum is untouched) and works identically whether the game plays the asset in mono, stereo, left-only, or volumetric 3D.

- **Verify before uploading:** Coolin writes `<song>_test_restored.wav` — the inverse EQ applied locally, i.e. approximately what your game will hear. Play the output file itself to hear the preview (faint sizzle).
- The in-game script uses the **new Audio API** (`AudioPlayer`/`Wire`/`AudioEqualizer`) — classic `Sound` instances have no EQ. Place the Script anywhere; 2D and 3D both work.
- Caveat: Roblox's equalizer filter shapes won't match ffmpeg's cut *exactly*, so the restored audio keeps slight tonal coloration near the 200 Hz / 4 kHz crossovers — the proof file shows the ideal case; expect something very close in-game.

```bat
python coolin_cli.py song.mp3 -m eqmask
```

### The `monogate` method — silent in the stereo preview, audible in-game

The **inverse** of the classic phase-inversion trick. Roblox 3D sounds (a `Sound` parented to a Part or Attachment) are converted to **mono** for spatial playback, while the website preview plays **stereo**. `monogate` exploits that asymmetry:

```
L = music + noise        stereo (preview):  each ear hears loud pink noise,
R = music − noise                          the music buried ~18 dB beneath it
L + R = 2 × music        mono (in-game):    the noise cancels — clean music
```

- **Verify before uploading:** Coolin writes `<song>_test_mono.wav` — play it to hear exactly what your game will hear (clean music). Play the output file normally to hear what the preview hears (noise only).
- **In-game requirements (generated script does this):** the Sound must be parented to a **Part or Attachment** (3D) — a 2D Sound (SoundService) plays stereo = noise. `Volume = 10` compensates the mask depth.
- `--mask-depth` (default 18 dB): higher = stronger masking but quieter in-game; lower = louder in-game but more audible in the preview.
- Caveat: this relies on Roblox's 3D mono conversion *summing* the channels (standard downmix). Test in Studio with your own ears first — the proof file makes that a 10-second check.

### Ear-to-ear panning (`--pan-hz`)

Bakes a rapid left↔right panning effect into the output (ffmpeg `apulsator`). `--pan-hz 25` sweeps the song between your ears 25 times per second — measured: +11 dB left-dominant in the first 10 ms, +11 dB right-dominant 20 ms later.

- Applied by `single`, `eqmask`, `chunked` (each chunk pans continuously) and the Discord methods.
- `bait`: the pan cannot be baked (the game plays only one channel of the asset), so the generated script pans **in game** instead — two `AudioFader`s between the splitter and mixer, oppositely modulated on `Heartbeat`.
- `monogate`: skipped with a warning (panning would break the stereo noise cancellation).
- GUI: **Pan Hz** spinbox (0 = off).

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
