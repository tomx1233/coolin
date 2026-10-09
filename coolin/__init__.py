"""Coolin - convert any audio into a "Discord 2-second" OGG file.

The resulting OGG carries the *full* song, but its declared duration is
rewritten (last-page Ogg granule position + CRC fix-up), so:

* Discord's built-in audio player stops after ~2 seconds.
* VLC plays the whole song.
* FMOD and the default Windows media players refuse to play it.
"""

__version__ = "1.0.0"

__all__ = ["ffmpeg", "ogg", "pipeline", "__version__"]
