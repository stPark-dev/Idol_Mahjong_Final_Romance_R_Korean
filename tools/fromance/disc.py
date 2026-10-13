"""Source disc access: CUE parsing, data-track identity, user-data reads.

The supported revision is identified by the SHA-1 of data track 1 (raw 2352-byte sectors),
whether it comes as a split Redump set (16 tracks) or as one BIN extracted from a CHD.
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import cdsector

TRACK1_SECTORS = 63578
TRACK1_SHA1 = "2d12e26747034b4385e0b61164c2e715cc403cd1"


class DiscError(ValueError):
    pass


def msf(text: str) -> int:
    m, s, f = (int(x) for x in text.split(":"))
    if not (0 <= s < 60 and 0 <= f < 75):
        raise DiscError(f"bad MSF {text}")
    return (m * 60 + s) * 75 + f


@dataclass(frozen=True)
class Track1:
    file: str
    offset: int              # byte offset of track 1 in its file
    sectors: int | None      # None: track 1 fills its file


def parse_cue(text: str) -> Track1:
    cur_file = None
    tracks: list[tuple[str, int, str, dict]] = []
    for line in text.splitlines():
        line = line.strip()
        if m := re.match(r'FILE\s+"(.+)"\s+BINARY', line):
            cur_file = m.group(1)
        elif m := re.match(r"TRACK\s+(\d+)\s+(\S+)", line):
            tracks.append((cur_file, int(m.group(1)), m.group(2), {}))
        elif m := re.match(r"INDEX\s+(\d+)\s+(\S+)", line):
            if not tracks:
                raise DiscError("INDEX before TRACK")
            tracks[-1][3][int(m.group(1))] = msf(m.group(2))
    if not tracks or tracks[0][1] != 1 or tracks[0][2] != "MODE1/2352":
        raise DiscError("first track must be TRACK 01 MODE1/2352")
    f1, _, _, idx1 = tracks[0]
    if idx1.get(1) != 0:
        raise DiscError("track 1 must start at 00:00:00 of its file")
    if len(tracks) > 1 and tracks[1][0] == f1:
        nxt = tracks[1][3]
        return Track1(f1, 0, nxt.get(0, nxt.get(1)))
    return Track1(f1, 0, None)


class SourceDisc:
    def __init__(self, cue: Path):
        self.cue = Path(cue)
        t = parse_cue(self.cue.read_text(encoding="utf-8", errors="replace"))
        self.bin = self.cue.with_name(t.file)
        if not self.bin.exists():
            raise DiscError(f"missing {self.bin}")
        self.offset = t.offset
        self.sectors = t.sectors if t.sectors is not None else self.bin.stat().st_size // cdsector.RAW
        if self.sectors != TRACK1_SECTORS:
            raise DiscError(f"track 1 has {self.sectors} sectors, expected {TRACK1_SECTORS}")
        self._f = open(self.bin, "rb")

    def verify(self) -> None:
        h = hashlib.sha1()
        self._f.seek(self.offset)
        left = self.sectors * cdsector.RAW
        while left:
            b = self._f.read(min(left, 1 << 22))
            if not b:
                raise DiscError(f"{self.bin.name} is truncated: track 1 ends early")
            h.update(b)
            left -= len(b)
        if h.hexdigest() != TRACK1_SHA1:
            raise DiscError(f"track 1 SHA-1 {h.hexdigest()} is not the supported revision")

    def raw(self, lba: int) -> bytes:
        if not 0 <= lba < self.sectors:
            raise DiscError(f"LBA {lba} outside track 1")
        self._f.seek(self.offset + lba * cdsector.RAW)
        return self._f.read(cdsector.RAW)

    def user(self, lba: int) -> bytes:
        return self.raw(lba)[16:16 + cdsector.USER]

    def read_file(self, lba: int, size: int) -> bytes:
        return b"".join(self.user(lba + i) for i in range((size + 2047) // 2048))[:size]

    def close(self) -> None:
        self._f.close()
