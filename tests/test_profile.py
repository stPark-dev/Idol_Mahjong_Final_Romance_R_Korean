import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, profile as pf
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/profile.json"


def test_body_lines_wrap_the_quote_with_an_indent():
    lines = pf.layout_body(["3월 3일생", "155cm 42kg", "B78 W56 H80", "O형", "귀가부",
                            "\"리나, 멋진 사람한테 마작 배우고 싶어요.\""])
    assert lines[:5] == [(0, "3월 3일생"), (0, "155cm 42kg"), (0, "B78 W56 H80"), (0, "O형"), (0, "귀가부")]
    quote = lines[5:]
    assert len(quote) >= 2 and quote[0][0] == 0 and all(x == pf.INDENT for x, _ in quote[1:])
    assert " ".join(t for _, t in quote) == "\"리나, 멋진 사람한테 마작 배우고 싶어요.\""
    assert len(lines) <= pf.LINES
    with pytest.raises(pf.ProfileError, match="lines"):
        pf.layout_body(["가"] * 5 + ["아주 " * 60])
    with pytest.raises(pf.ProfileError, match="wider"):
        pf.layout_body(["아주아주아주아주긴생일줄입니다"] + ["가"] * 5)


def test_body_image_uses_only_text_colours():
    img = pf.render_body(pf.layout_body(["1월 15일생", "162cm 45kg", "B83 W57 H85", "A형", "음악부", "\"짧은 말\""]))
    assert img.shape == (pf.BODY_H * 8, pf.BODY_W * 8)
    assert set(np.unique(img)) <= {0, *pf.TEXT_INK_RAMP} and (img == pf.TEXT_INK_RAMP[0]).any()


def test_texts_load_for_five_people(tmp_path):
    names, bodies = pf.load_texts(TEXTS)
    assert names[0] == "고이즈미 리나" and len(names) == len(bodies) == 5 and all(len(b) == 6 for b in bodies)
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "profile.ri.name", "ko": "x"}]}', encoding="utf-8")
    with pytest.raises(pf.ProfileError, match="id"):
        pf.load_texts(bad)
    import json
    d = json.loads(TEXTS.read_text(encoding="utf-8"))
    d["entries"][1]["ko"] = "한 줄 문자열"
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pf.ProfileError, match="six"):
        pf.load_texts(bad)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        e = iso9660.list_files(d.user)
        return {n: d.read_file(e[n].lba, e[n].size) for n in ("1ST.BIN", "SL.BIN", "PROFDAT.BIN")}
    finally:
        d.close()


@source
def test_layout_is_five_people(files):
    lay = pf.Layout.read(files["1ST.BIN"], files["SL.BIN"], files["PROFDAT.BIN"])
    assert len(lay.people) == 5
    assert [p.piece for p in lay.people][:1] == [(0x3B400, 0x2A40)]
    assert [p.name_off for p in lay.people] == [0x33900 + 0x1200 * k for k in range(5)]


@source
def test_original_bodies_round_trip(files):
    lay = pf.Layout.read(files["1ST.BIN"], files["SL.BIN"], files["PROFDAT.BIN"])
    imgs = [pf.decode_body(lay, k, files["SL.BIN"], files["PROFDAT.BIN"]) for k in range(5)]
    sl, pd = pf.apply_bodies(lay, files["SL.BIN"], files["PROFDAT.BIN"], imgs)
    assert all(np.array_equal(pf.decode_body(lay, k, sl, pd), imgs[k]) for k in range(5))


@source
def test_korean_profiles_change_only_their_bytes(files):
    first, sl0, pd0 = files["1ST.BIN"], files["SL.BIN"], files["PROFDAT.BIN"]
    names, bodies = pf.load_texts(TEXTS)
    sl, pd = pf.build_files(first, sl0, pd0, names, bodies)
    lay = pf.Layout.read(first, sl0, pd0)
    sl_mask, pd_mask = pf.editable(lay, len(sl0), len(pd0))
    for a, b, m in ((sl0, sl, sl_mask), (pd0, pd, pd_mask)):
        diff = np.frombuffer(a, np.uint8) != np.frombuffer(b, np.uint8)
        assert diff.any() and not (diff & ~m).any()
    for k in range(5):
        body = pf.decode_body(lay, k, sl, pd)
        assert np.array_equal(body, pf.render_body(pf.layout_body(bodies[k])))
        name = pf.read_name(lay, k, pd)
        assert np.array_equal(name, pf.render_name(names[k], lay.name_face[k], lay.name_side[k]))
        assert set(np.unique(name).tolist()) <= set(lay.people[k].name_colours) and (name != 0).any()
    bad = bytearray(pd); bad[lay.people[0].name_off - 1] ^= 1
    with pytest.raises(pf.ProfileError, match="byte"):
        pf.check_protected(lay, sl0, pd0, sl, bytes(bad))
    foreign = next(c for c in range(1, 256) if c not in lay.people[0].name_colours)
    bad = bytearray(pd); bad[lay.people[0].name_off + 100] = foreign
    with pytest.raises(pf.ProfileError, match="name"):
        pf.check_protected(lay, sl0, pd0, sl, bytes(bad))
    bad = bytearray(pd); bad[lay.people[0].piece[0] + 64 + 3] = 7                # body cell, not a text colour
    with pytest.raises(pf.ProfileError, match="body"):
        pf.check_protected(lay, sl0, pd0, sl, bytes(bad))


@source
def test_a_body_with_too_many_distinct_cells_is_refused(files):
    lay = pf.Layout.read(files["1ST.BIN"], files["SL.BIN"], files["PROFDAT.BIN"])
    rng = np.random.default_rng(3)
    noisy = [rng.choice([0, *pf.TEXT_INK_RAMP], (pf.BODY_H * 8, pf.BODY_W * 8)).astype(np.uint8) for _ in range(5)]
    with pytest.raises(pf.ProfileError, match="cells"):
        pf.apply_bodies(lay, files["SL.BIN"], files["PROFDAT.BIN"], noisy)
