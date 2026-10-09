"""Coolin - convert audio into Discord-compatible OGG files.

Outputs an OGG file that actually stops playing at the specified song length
(default: 2 seconds, maximum: 6 minutes and 59 seconds / 419 seconds).
"""

__version__ = "1.2.0"

__all__ = ["ffmpeg", "ogg", "pipeline", "__version__"]
