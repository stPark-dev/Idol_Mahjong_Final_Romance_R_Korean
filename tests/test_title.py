import os
import pathlib

import numpy as np
import pytest
from PIL import Image

from fromance import iso9660, title as tt
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
LOGO = ROOT / "title_ko.png"


def test_rgb555_round_trip():
    rgb = np.array([[0, 0, 0], [248, 240, 240], [8, 128, 255]], np.uint8)
    words = tt.to_rgb555(rgb)
    assert words.tolist() == [0x0000, (30 << 10) | (30 << 5) | 31, (31 << 10) | (16 << 5) | 1]
    assert (tt.from_rgb555(words) == rgb & 0xF8).all()


def test_fit_keeps_the_aspect_inside_the_box():
    logo = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    logo.paste((255, 0, 0, 255), (20, 10, 180, 90))          # 160x80 opaque part
    x, y, img = tt.fit(logo, (10, 20, 110, 70))              # box 100x50
    assert img.size == (100, 50) and (x, y) == (10, 20)
    x, y, img = tt.fit(logo, (0, 0, 100, 100))
    assert img.size == (100, 50) and (x, y) == (0, 25)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        e = iso9660.list_files(d.user)
        return {n: d.read_file(e[n].lba, e[n].size) for n in ("1ST.BIN", "MJDAT3.BIN")}
    finally:
        d.close()


@source
def test_original_title_decodes(files):
    img = tt.decode(files["1ST.BIN"], files["MJDAT3.BIN"])
    assert img.shape == (240, 352, 3)
    assert tuple(img[0, 0]) == (248, 240, 240) and tt.logo_box(img) == (20, 17, 333, 168)


@source
def test_korean_logo_changes_only_the_title_bytes(files):
    first, mj3 = files["1ST.BIN"], files["MJDAT3.BIN"]
    first2, mj32 = tt.build_files(first, mj3, Image.open(LOGO))
    tt.check_protected(first, mj3, first2, mj32)
    assert len(first2) == len(first) and len(mj32) == len(mj3) and mj32[tt.CELLS_END:] == mj3[tt.CELLS_END:]
    old, new = tt.decode(first, mj3), tt.decode(first2, mj32)
    assert np.abs(new[200:].astype(int) - old[200:]).max() <= 16           # copyright line and below kept
    assert np.abs(new[17:168].astype(int) - old[17:168]).mean() > 10         # the logo changed
    bad = bytearray(mj32); bad[tt.CELLS_END] ^= 1
    with pytest.raises(tt.TitleError, match="protected"):
        tt.check_protected(first, mj3, first2, bytes(bad))
    bad = bytearray(first2); bad[tt.MAP_OFF - 1] ^= 1
    with pytest.raises(tt.TitleError, match="protected"):
        tt.check_protected(first, mj3, bytes(bad), mj32)


@source
def test_too_big_a_logo_fails(files, monkeypatch):
    noise = Image.fromarray(np.random.default_rng(1).integers(0, 256, (300, 600, 3), np.uint8)).convert("RGBA")
    monkeypatch.setattr(tt, "logo_box", lambda img: (0, 0, 352, 200))
    with pytest.raises(tt.TitleError, match="cells"):
        tt.build_files(files["1ST.BIN"], files["MJDAT3.BIN"], noise)


@source
def test_palette_slots_are_distinct_colours(files):
    first2, mj32 = tt.build_files(files["1ST.BIN"], files["MJDAT3.BIN"], Image.open(LOGO))
    idx = tt._indices(first2, mj32)
    used = sorted(set(np.unique(idx).tolist()))
    assert used[0] >= 1 and len(used) <= 255
    words = np.frombuffer(first2[tt.PAL_OFF:tt.PAL_OFF + 512], ">u2")
    assert len({int(words[i]) for i in used}) == len(used) > 240     # no two used slots hold one colour


def test_empty_logo_fails():
    with pytest.raises(tt.TitleError, match="empty"):
        tt.fit(Image.new("RGBA", (10, 10), (0, 0, 0, 0)), (0, 0, 10, 10))


@source
def test_output_is_the_emulator_checked_one(files):
    """Pillow's resize and quantiser may change between releases: a new result needs a new look."""
    import hashlib
    first2, mj32 = tt.build_files(files["1ST.BIN"], files["MJDAT3.BIN"], Image.open(LOGO))
    assert hashlib.sha1(first2 + mj32).hexdigest() == "3cc33166a0f996425bf17452a93d04491ce4c4e7"
