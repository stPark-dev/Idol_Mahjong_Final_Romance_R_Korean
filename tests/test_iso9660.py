import struct

import pytest

from fromance import iso9660


def _rec(name: bytes, lba: int, size: int, flags: int = 0) -> bytes:
    body = bytearray(33 + len(name) + (1 - len(name) % 2))
    body[0] = len(body)
    body[2:10] = struct.pack("<I", lba) + struct.pack(">I", lba)
    body[10:18] = struct.pack("<I", size) + struct.pack(">I", size)
    body[25] = flags
    body[32] = len(name)
    body[33:33 + len(name)] = name
    return bytes(body)


def _image(records: list[bytes]) -> dict[int, bytes]:
    pvd = bytearray(2048)
    pvd[:6] = b"\x01CD001"
    pvd[156:190] = _rec(b"\x00", 20, 2048, 2)[:34]
    root = bytearray(2048)
    data = _rec(b"\x00", 20, 2048, 2) + _rec(b"\x01", 20, 2048, 2) + b"".join(records)
    root[:len(data)] = data
    return {16: bytes(pvd), 20: bytes(root)}


def test_lists_root_files_without_version_suffix():
    sec = _image([_rec(b"MAIN.BIN;1", 314, 372588), _rec(b"YESNO.SA;1", 66052, 1202)])
    files = iso9660.list_files(lambda lba: sec.get(lba, bytes(2048)))
    assert files["MAIN.BIN"] == iso9660.Entry("MAIN.BIN", 314, 372588)
    assert files["YESNO.SA"].lba == 66052


def test_entry_sector_count():
    assert iso9660.Entry("A", 0, 2049).sectors == 2
    assert iso9660.Entry("A", 0, 2048).sectors == 1


def test_rejects_missing_pvd():
    with pytest.raises(iso9660.IsoError):
        iso9660.list_files(lambda lba: bytes(2048))


def test_rejects_subdirectories():
    sec = _image([_rec(b"SUB", 30, 2048, 2)])
    with pytest.raises(iso9660.IsoError):
        iso9660.list_files(lambda lba: sec.get(lba, bytes(2048)))


def test_record_location_points_at_the_directory_record():
    sec = _image([_rec(b"MAIN.BIN;1", 314, 372588), _rec(b"YESNO.SA;1", 66052, 1202)])
    lba, off = iso9660.record_location(lambda lba: sec.get(lba, bytes(2048)), "YESNO.SA")
    rec = sec[lba][off:]
    assert rec[33:41] == b"YESNO.SA"
    assert struct.unpack_from("<I", rec, 10)[0] == 1202


def test_size_field_bytes_are_both_endian():
    assert iso9660.size_field(0x01020304) == bytes.fromhex("04030201" "01020304")


def _read(sec):
    return lambda lba: sec.get(lba, bytes(2048))


def _names(read):
    return [iso9660._name(r).decode() for _, _, r in iso9660._records(read)][2:]


def test_iso_key_matches_the_disc_order():
    names = ["CDDA1", "CDDA10", "CDDA15", "CDDA2", "KISEDAT.BIN", "KISEKAE.BIN", "KISE_KA.BIN", "YAKU.BIN"]
    assert sorted(names, key=iso9660.iso_key) == names


def test_repack_root_appends_new_files_and_updates_sizes():
    read = _read(_image([_rec(b"A.BIN;1", 30, 10), _rec(b"YAKU.BIN;1", 40, 20)]))
    secs = iso9660.repack_root(read, {"A.BIN": 99}, [("ZRI01.DAT", 100, 744), ("ZRI02.DAT", 101, 5000)])
    new = lambda lba: secs.get(lba, read(lba))
    files = iso9660.list_files(new)
    assert files["ZRI02.DAT"] == iso9660.Entry("ZRI02.DAT", 101, 5000)
    assert files["A.BIN"].size == 99
    assert _names(new) == ["A.BIN;1", "YAKU.BIN;1", "ZRI01.DAT;1", "ZRI02.DAT;1"]


def test_repack_root_refuses_new_names_that_would_shift_existing_file_ids():
    read = _read(_image([_rec(b"A.BIN;1", 30, 10), _rec(b"YAKU.BIN;1", 40, 20)]))
    with pytest.raises(iso9660.IsoError, match="after"):
        iso9660.repack_root(read, {}, [("B.DAT", 100, 1)])


def test_repack_root_refuses_duplicates_and_overflow():
    read = _read(_image([_rec(b"A.BIN;1", 30, 10)]))
    with pytest.raises(iso9660.IsoError, match="already"):
        iso9660.repack_root(read, {}, [("A.BIN", 100, 1)])
    many = [(f"Z{i:04d}.DAT", 100 + i, 1) for i in range(60)]
    with pytest.raises(iso9660.IsoError, match="sector"):
        iso9660.repack_root(read, {}, many)


def test_repack_root_keeps_records_whole_inside_sectors():
    recs = [_rec(f"F{i:03d}.BIN;1".encode(), 30 + i, 1) for i in range(50)]
    sec = _image(recs[:40])
    sec[16] = sec[16][:156] + _rec(b"\x00", 20, 4096, 2)[:34] + sec[16][190:]
    sec[21] = bytes(2048)
    read = _read(sec)
    secs = iso9660.repack_root(read, {}, [(f"Z{i:03d}.DAT", 200 + i, 1) for i in range(30)])
    assert set(secs) == {20, 21}
    files = iso9660.list_files(lambda lba: secs.get(lba, read(lba)))
    assert len(files) == 70
