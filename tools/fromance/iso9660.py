"""Minimal ISO 9660 root-directory reader/rewriter for this disc (it has no subdirectories)."""
import struct
from dataclasses import dataclass
from typing import Callable

ReadUser = Callable[[int], bytes]   # lba -> 2048 bytes of user data


class IsoError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    lba: int
    size: int

    @property
    def sectors(self) -> int:
        return (self.size + 2047) // 2048


def _records(read: ReadUser):
    """Yield (sector LBA, offset in sector, record) for every root-directory record."""
    pvd = read(16)
    if pvd[:6] != b"\x01CD001":
        raise IsoError("no primary volume descriptor at LBA 16")
    root = pvd[156:190]
    lba, size = struct.unpack("<I", root[2:6])[0], struct.unpack("<I", root[10:14])[0]
    data = b"".join(read(lba + i) for i in range((size + 2047) // 2048))
    p = 0
    while p < len(data):
        ln = data[p]
        if ln == 0:
            p = (p // 2048 + 1) * 2048
            continue
        yield lba + p // 2048, p % 2048, data[p:p + ln]
        p += ln


def _name(rec: bytes) -> bytes:
    return rec[33:33 + rec[32]]


def size_field(size: int) -> bytes:
    """Both-endian 32-bit data length (record bytes 10..17)."""
    return struct.pack("<I", size) + struct.pack(">I", size)


def record_location(read: ReadUser, name: str) -> tuple[int, int]:
    for lba, off, rec in _records(read):
        if _name(rec).decode("latin1").split(";")[0] == name:
            return lba, off
    raise IsoError(f"{name} not in root directory")


def list_files(read: ReadUser) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for _, _, rec in _records(read):
        nm = _name(rec)
        if nm in (b"\x00", b"\x01"):
            continue
        name = nm.decode("latin1").split(";")[0]
        if rec[25] & 2:
            raise IsoError(f"unexpected subdirectory {name}")
        out[name] = Entry(name, struct.unpack("<I", rec[2:6])[0], struct.unpack("<I", rec[10:14])[0])
    return out


def iso_key(name: str) -> tuple[str, str]:
    """ISO 9660 ordering: name and extension compared space-padded."""
    stem, _, ext = name.partition(".")
    return stem.ljust(8), ext.ljust(3)


def _new_record(template: bytes, name: str, lba: int, size: int) -> bytes:
    ident = (name + ";1").encode()
    rec = bytearray(template[:33]) + ident
    if len(ident) % 2 == 0:
        rec += b"\0"
    rec[0] = len(rec)
    rec[2:10] = struct.pack("<I", lba) + struct.pack(">I", lba)
    rec[10:18] = size_field(size)
    rec[25] = 0
    rec[32] = len(ident)
    return bytes(rec)


def repack_root(read: ReadUser, sizes: dict[str, int], new_files: list[tuple[str, int, int]]) -> dict[int, bytes]:
    """Root directory sectors {lba: user data}: sizes updated and new (name, lba, size) records
    appended after every existing file, so existing directory indexes (GFS file ids) stay put.
    Records never straddle a sector; the directory keeps its sectors."""
    records = list(_records(read))
    root = read(16)[156:190]
    first, size = struct.unpack("<I", root[2:6])[0], struct.unpack("<I", root[10:14])[0]
    dir_lbas = list(range(first, first + (size + 2047) // 2048))
    dots, plain = [], []
    for _, _, rec in records:
        (dots if rec[32] == 1 and rec[33] in (0, 1) else plain).append(rec)
    names = [_name(r).decode("latin1").split(";")[0] for r in plain]
    if names != sorted(names, key=iso_key):
        raise IsoError("root directory is not in ISO order")
    out = []
    for name, rec in zip(names, plain):
        rec = bytearray(rec)
        if name in sizes:
            rec[10:18] = size_field(sizes[name])
        out.append(bytes(rec))
    have = set(names)
    last = max(names, key=iso_key)
    for name, lba, size in sorted(new_files, key=lambda f: iso_key(f[0])):
        if name in have:
            raise IsoError(f"{name} is already on the disc")
        if iso_key(name) <= iso_key(last):
            raise IsoError(f"{name} must sort after {last} so existing file ids do not move")
        out.append(_new_record(plain[-1], name, lba, size))
        have.add(name)
        last = name
    blob, cur = bytearray(), bytearray()
    for rec in dots + out:
        if len(cur) + len(rec) > 2048:
            blob += cur.ljust(2048, b"\0")
            cur = bytearray()
        cur += rec
    blob += cur.ljust(2048, b"\0")
    if len(blob) > 2048 * len(dir_lbas):
        raise IsoError("root directory would need another sector")
    blob = blob.ljust(2048 * len(dir_lbas), b"\0")
    return {lba: bytes(blob[i * 2048:(i + 1) * 2048]) for i, lba in enumerate(dir_lbas)}
