"""Low-level Ogg container utilities.

Everything Coolin needs to pull off the "2 seconds on Discord" trick lives
here: page parsing, the Ogg CRC-32, codec detection and the granule-position
rewrite that fakes the declared duration of an Ogg Opus/Vorbis file.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple

CAPTURE_PATTERN = b"OggS"
PAGE_HEADER_SIZE = 27
BOS_FLAG = 0x02  # beginning of stream
EOS_FLAG = 0x04  # end of stream

# ---------------------------------------------------------------------------
# Ogg CRC-32 (polynomial 0x04C11DB7, MSB-first, init 0, no final XOR).
# This is *not* the common zlib/ethernet CRC - Ogg uses its own variant.
# ---------------------------------------------------------------------------


def _build_crc_table() -> List[int]:
    table: List[int] = []
    for i in range(256):
        crc = i << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def ogg_crc(data: bytes) -> int:
    """Compute the Ogg CRC-32 of *data*."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) & 0xFF) ^ byte]
    return crc


# ---------------------------------------------------------------------------
# Page model / parser
# ---------------------------------------------------------------------------


@dataclass
class OggPage:
    offset: int
    header_type: int
    granule: int          # signed; -1 means "no packet ends on this page"
    serial: int
    sequence: int
    num_segments: int
    lacing: bytes
    body_size: int

    @property
    def size(self) -> int:
        return PAGE_HEADER_SIZE + self.num_segments + self.body_size

    @property
    def is_bos(self) -> bool:
        return bool(self.header_type & BOS_FLAG)

    @property
    def is_eos(self) -> bool:
        return bool(self.header_type & EOS_FLAG)


def parse_pages(data: bytes) -> List[OggPage]:
    """Parse every Ogg page in *data*; raise ValueError on corruption."""
    pages: List[OggPage] = []
    pos = 0
    total = len(data)
    while pos < total:
        if pos + PAGE_HEADER_SIZE > total:
            raise ValueError(f"Truncated Ogg page header at offset {pos}")
        if data[pos : pos + 4] != CAPTURE_PATTERN:
            raise ValueError(f"Missing OggS capture pattern at offset {pos}")
        header_type = data[pos + 5]
        granule = struct.unpack_from("<q", data, pos + 6)[0]
        serial, sequence = struct.unpack_from("<II", data, pos + 14)
        num_segments = data[pos + 26]
        lacing_end = PAGE_HEADER_SIZE + num_segments
        if pos + lacing_end > total:
            raise ValueError(f"Truncated lacing table at offset {pos}")
        lacing = data[pos + PAGE_HEADER_SIZE : pos + lacing_end]
        body_size = sum(lacing)
        if pos + lacing_end + body_size > total:
            raise ValueError(f"Truncated page body at offset {pos}")
        pages.append(
            OggPage(
                offset=pos,
                header_type=header_type,
                granule=granule,
                serial=serial,
                sequence=sequence,
                num_segments=num_segments,
                lacing=lacing,
                body_size=body_size,
            )
        )
        pos += lacing_end + body_size
    if not pages:
        raise ValueError("File contains no Ogg pages")
    return pages


def stored_page_crc(data: bytes, page: OggPage) -> int:
    """Return the CRC that is currently stored in the page header."""
    return struct.unpack_from("<I", data, page.offset + 22)[0]


def page_crc_valid(data: bytes, page: OggPage) -> bool:
    """Re-compute a page's CRC the way every Ogg demuxer does."""
    start = page.offset
    view = bytearray(data[start : start + page.size])
    view[22:26] = b"\x00\x00\x00\x00"  # CRC field is zeroed while computing
    return ogg_crc(bytes(view)) == stored_page_crc(data, page)


# ---------------------------------------------------------------------------
# Codec detection (Opus vs Vorbis) + granule timebase
# ---------------------------------------------------------------------------


def detect_codec(data: bytes, pages: List[OggPage]) -> Tuple[str, int]:
    """Return (codec, granule_rate) for the first audio stream found.

    Opus granule positions are *always* expressed at 48 kHz; Vorbis uses the
    stream's own sample rate from its identification header.
    """
    for page in pages:
        if not page.is_bos:
            continue
        body_start = page.offset + PAGE_HEADER_SIZE + page.num_segments
        packet = data[body_start : body_start + page.body_size]
        if packet.startswith(b"OpusHead"):  # RFC 7845 magic signature
            return "opus", 48000
        if packet.startswith(b"\x01vorbis") and len(packet) >= 16:
            rate = struct.unpack_from("<I", packet, 12)[0]
            if rate == 0:
                raise ValueError("Vorbis identification header has rate 0")
            return "vorbis", rate
    raise ValueError("No Ogg Opus or Vorbis stream found in file")


# ---------------------------------------------------------------------------
# The trick itself
# ---------------------------------------------------------------------------

MAX_SECONDS = 419.0  # 6 minutes and 59 seconds (6 * 60 + 59)


def inject_fake_duration(data: bytes, seconds: float) -> Tuple[bytes, Dict]:
    """Rewrite the last page's granule position so the declared duration is
    *seconds* long, while every audio packet stays untouched.

    Returns (new_file_bytes, info_dict).
    """
    if seconds <= 0:
        raise ValueError("Fake duration must be greater than zero")
    if seconds > MAX_SECONDS:
        raise ValueError(
            f"Fake duration cannot exceed 6 minutes and 59 seconds ({MAX_SECONDS} seconds)"
        )

    pages = parse_pages(data)
    codec, rate = detect_codec(data, pages)

    last = pages[-1]
    if last.granule < 0:
        raise ValueError("Last Ogg page carries no granule position; nothing to rewrite")

    actual_seconds = last.granule / rate
    if seconds >= actual_seconds:
        raise ValueError(
            f"Fake duration ({seconds:.2f}s) must be shorter than the real "
            f"audio length ({actual_seconds:.2f}s)"
        )

    new_granule = int(round(rate * seconds))

    buf = bytearray(data)
    page_start = last.offset
    struct.pack_into("<q", buf, page_start + 6, new_granule)   # granule position
    struct.pack_into("<I", buf, page_start + 22, 0)            # zero CRC field
    crc = ogg_crc(bytes(buf[page_start : page_start + last.size]))
    struct.pack_into("<I", buf, page_start + 22, crc)          # write fresh CRC

    info = {
        "codec": codec,
        "granule_rate": rate,
        "pages": len(pages),
        "actual_seconds": actual_seconds,
        "declared_seconds": new_granule / rate,
        "original_last_granule": last.granule,
        "new_last_granule": new_granule,
    }
    return bytes(buf), info


def describe(data: bytes) -> Dict:
    """Analyse an Ogg file - used by the --verify mode."""
    pages = parse_pages(data)
    codec, rate = detect_codec(data, pages)

    # Group pages by serial number to handle both single streams and chained streams
    streams: Dict[int, list] = {}
    for p in pages:
        streams.setdefault(p.serial, []).append(p)

    monotonic = True
    for serial, stream_pages in streams.items():
        prev = -1
        for p in stream_pages:
            if p.granule < 0:
                continue
            if p.granule < prev:
                monotonic = False
                break
            prev = p.granule
        if not monotonic:
            break

    # Stream 1 is what players stopping at the first EOS (Discord/Chromium) play
    s1_pages = streams[pages[0].serial]
    s1_eos = [p for p in s1_pages if p.is_eos]
    s1_last = s1_eos[-1] if s1_eos else s1_pages[-1]
    declared = (s1_last.granule / rate) if s1_last.granule >= 0 else None

    # Total duration across all streams
    if len(streams) > 1:
        total_audio = 0.0
        for s, sp in streams.items():
            eos_p = [p for p in sp if p.is_eos]
            last_p = eos_p[-1] if eos_p else sp[-1]
            if last_p.granule >= 0:
                total_audio += last_p.granule / rate
        full_audio = total_audio
    else:
        real_granules = [p.granule for p in pages[:-1] if p.granule >= 0]
        real_max = max(real_granules) if real_granules else 0
        last = pages[-1]
        if not monotonic and real_max > (last.granule if last.granule >= 0 else 0):
            full_audio = real_max / rate
        else:
            full_audio = declared

    return {
        "codec": codec,
        "granule_rate": rate,
        "pages": len(pages),
        "serial": pages[0].serial,
        "declared_seconds": declared,
        "full_audio_seconds": full_audio,
        "last_page_is_eos": pages[-1].is_eos,
        "granules_monotonic": monotonic,
        "all_crcs_valid": all(page_crc_valid(data, p) for p in pages),
        "chained_streams": len(streams),
    }


def format_seconds(seconds) -> str:
    if seconds is None:
        return "?"
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes}:{secs:05.2f}"
