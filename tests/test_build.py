"""원본 디스크가 있을 때만 도는 빌드 통합 검사."""
import hashlib
import json
import os
import pathlib
import struct

import numpy as np
import pytest

from fromance import build, iso9660, subtitles as st
from fromance.disc import SourceDisc, TRACK1_SHA1

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
pytestmark = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


def _sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def test_build_without_components_reproduces_the_source(tmp_path):
    m = build.build(CUE, tmp_path, {"subtitles": None})
    assert m["sectors_changed"] == 0
    assert _sha1(tmp_path / m["output_bin"]) == TRACK1_SHA1
    cue = (tmp_path / build.OUT_CUE).read_text()
    assert cue.count("FILE") == 16 and "(Track 16)" in cue


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("ja")
    m = build.build(CUE, out, {"subtitles": "ja"})
    return out, m


def _reader(path):
    f = open(path, "rb")
    def user(lba):
        f.seek(lba * 2352 + 16)
        return f.read(2048)
    return user


def test_subtitle_build_lays_out_files_and_hooks(built):
    out, m = built
    user = _reader(out / m["output_bin"])
    files = iso9660.list_files(user)
    src = SourceDisc(CUE)
    old = iso9660.list_files(src.user)
    # existing files keep place and order; new ones follow YAKU.BIN
    assert [n for n in files if n in old] == list(old)
    new = [n for n in files if n not in old]
    rows = st.movie_rows(src.read_file(22, 344184))
    subtitled = {rows[m][0] for m in st.voice_movies(ROOT / "translation/voice", text="ja")}
    names = [st.pack_name(r) for r in sorted(subtitled)]
    assert new == names and files[names[0]].lba == 63428
    assert files[names[-1]].lba + files[names[-1]].sectors <= 63578
    first = files["1ST.BIN"]
    assert first.lba == 22 and first.sectors == 169
    data = b"".join(user(first.lba + i) for i in range(first.sectors))[:first.size]
    orig = src.read_file(22, 344184)
    sizes = [files[st.pack_name(r)].size if st.pack_name(r) in files else 0 for r in range(st.ROWS)]
    sub = st.fill_pack_sizes((ROOT / "assets/subtitle/SUB.BIN").read_bytes(), sizes)
    assert data == st.patch_first(orig, sub)
    for n in names:
        r = int(n[4])
        pe = files[n]
        pack = b"".join(user(pe.lba + i) for i in range(pe.sectors))[:pe.size]
        magic, nmovies = struct.unpack_from(">IH", pack)
        assert magic == st.MAGIC
        movies = {pack[12 + 12 * i:16 + 12 * i].decode() for i in range(nmovies)}
        assert movies and {rows[m][0] for m in movies} == {r}
    assert m["subtitles"] == "ja" and m["distribution"] is False
    assert json.loads((out / "manifest.json").read_text())["output_sha1"] == _sha1(out / m["output_bin"])


def test_shop_build_changes_only_the_two_shop_files(tmp_path):
    from fromance import shopdesc as sd, shopmenu as sm
    m = build.build(CUE, tmp_path, {"subtitles": None, "shop": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
        data = [n for n in old if old[n].lba < src.sectors]          # CDDA entries point at audio tracks
        changed = {n for n in data if n in files and read(user, files[n]) != read(src.user, old[n])}
        assert changed == {"SL.BIN", "SHOPDAT.BIN"}
        first = read(src.user, old["1ST.BIN"])
        sl0 = read(src.user, old["SL.BIN"])
        shop0 = read(src.user, old["SHOPDAT.BIN"])
        lay = sm.Layout.read(first, sl0)
    finally:
        src.close()
    texts = sm.load_texts(ROOT / "translation/graphics/shop_menu.json")
    got = sm.decode_items(lay, read(user, files["SL.BIN"]), read(user, files["SHOPDAT.BIN"]))
    want = [sm.render_text(t, it.cells * 8, it.ramp) for t, it in zip(texts, lay.items)]
    assert len(got) == len(want) == 5 and all((a == b).all() for a, b in zip(got, want))
    shop = read(user, files["SHOPDAT.BIN"])
    colours = sd.panel_colours(first, sl0)
    descs = sd.load_texts(ROOT / "translation/graphics/shop_desc.json")
    offs = sd.picture_offsets(sl0, len(shop))
    assert all((sd.read_panel(shop, o) == sd.render_panel(t, colours)).all() for o, t in zip(offs, descs))
    build.check_shop(first, sl0, shop0, read(user, files["SL.BIN"]), shop)      # final vs original
    from fromance import shopmark as mk
    off = mk.mark_offset(sl0, len(shop))
    assert shop[off:off + 256] == mk.arrow_icon().tobytes()
    from fromance import shopmsg as sg
    msg = sg.Layout.read(first, sl0)
    lines = sg.load_texts(ROOT / "translation/graphics/shop_msg.json")
    got = sg.decode_lines(msg, read(user, files["SL.BIN"]), shop)
    assert all((g == sg.render_line(t, ln.cols * 8, ln.x, ln.align, msg.colours)).all()
               for g, t, ln in zip(got, lines, msg.lines))
    button = sg.decode_button(msg, read(user, files["SL.BIN"]), shop)
    assert (button == sg.render_button(lines[-1], sg.decode_button(msg, sl0, shop0))).all()
    assert m["components"]["shop"] is True and m["distribution"] is False


def test_shop_check_refuses_overlapping_or_stray_changes():
    from fromance import shopmenu as sm
    src = SourceDisc(CUE)
    try:
        files = iso9660.list_files(src.user)
        read = lambda n: src.read_file(files[n].lba, files[n].size)
        first, sl0, shop0 = read("1ST.BIN"), read("SL.BIN"), read("SHOPDAT.BIN")
    finally:
        src.close()
    sl, shop = build.shop_files(first, sl0, shop0)
    build.check_shop(first, sl0, shop0, sl, shop)
    mods = build.shop_masks(first, sl0, shop0)
    for a in mods:                    # no module writes what another writes or keeps
        for b in mods:
            if a is not b:
                assert not (a.write_sl & (b.write_sl | b.keep_sl)).any()
                assert not (a.write_shop & (b.write_shop | b.keep_shop)).any()
    frame = sm.cell_offset(4)         # a menu frame cell: kept by the menu, written by nobody
    assert not any(m.write_shop[frame] for m in mods)
    with pytest.raises(build.BuildError, match="SHOPDAT.BIN byte"):
        build.check_shop(first, sl0, shop0, sl, shop[:frame] + bytes([shop[frame] ^ 1]) + shop[frame + 1:])
    with pytest.raises(build.BuildError, match="SL.BIN byte"):
        build.check_shop(first, sl0, shop0, sl[:5] + bytes([sl[5] ^ 1]) + sl[6:], shop)
    with pytest.raises(build.BuildError, match="size"):
        build.check_shop(first, sl0, shop0, sl, shop + b"\0")
    w = next(i for i, m in enumerate(mods[0].write_sl) if m)        # a menu text word -> frame cell
    bad = sl[:w] + (4 - 2).to_bytes(2, "big") + sl[w + 2:]
    with pytest.raises(build.BuildError, match="points at"):
        build.check_shop(first, sl0, shop0, bad, shop)


def test_mahjong_build_redraws_the_call_window_alongside_the_shop(tmp_path):
    from fromance import mjmenu as mm
    m = build.build(CUE, tmp_path, {"subtitles": None, "shop": True, "mahjong": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
        first, sl0, shop0 = read(src.user, old["1ST.BIN"]), read(src.user, old["SL.BIN"]), read(src.user, old["SHOPDAT.BIN"])
    finally:
        src.close()
    sl, shop = read(user, files["SL.BIN"]), read(user, files["SHOPDAT.BIN"])
    want = mm.build_file(first, sl0, mm.load_texts(ROOT / "translation/graphics/mj_call.json"))
    lay = mm.Layout.read(first, sl0)
    assert (mm.read_image(sl, lay) == mm.read_image(want, lay)).all()
    from fromance import mjdeclare as md
    frames = md.read_frames(first, sl0)
    want = md.build_file(first, sl0, md.load_texts(ROOT / "translation/graphics/mj_declare.json"))
    assert all(sl[f.off:f.off + f.w * f.h] == want[f.off:f.off + f.w * f.h] for k in frames for f in k)
    build.check_graphics(first, sl0, shop0, sl, shop, {"shop": True, "mahjong": True})
    assert m["components"]["mahjong"] is True


def test_mahjong_alone_leaves_the_shop_file_untouched(tmp_path):
    m = build.build(CUE, tmp_path, {"subtitles": None, "mahjong": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
        changed = {n for n in old if old[n].lba < src.sectors and read(user, files[n]) != read(src.user, old[n])}
    finally:
        src.close()
    # draw words live in 1ST.BIN, the result panel's yaku names and glyphs in MJDAT2.BIN
    assert changed == {"SL.BIN", "1ST.BIN", "MJDAT2.BIN"} and "SHOPDAT.BIN" not in m["files"]


@pytest.mark.parametrize("text", [None, "ko"])
def test_mahjong_draw_words_live_in_1st_bin_with_or_without_subtitles(tmp_path, text):
    from fromance import mjdraw as mw
    m = build.build(CUE, tmp_path, {"subtitles": text, "mahjong": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    src = SourceDisc(CUE)
    try:
        first0 = src.read_file(22, 344184)
    finally:
        src.close()
    e = files["1ST.BIN"]
    first = b"".join(user(e.lba + i) for i in range(e.sectors))[:e.size]
    from fromance import mjlabel as ml
    want = ml.build_file(mw.build_file(first0, mw.load_texts(ROOT / "translation/graphics/mj_draw.json")),
                         ml.load_text(ROOT / "translation/graphics/mj_label.json"))
    if text:                          # the subtitle hooks sit on top of the redrawn draw words
        assert len(first) > st.FIRST_SIZE
        assert [struct.unpack_from(">I", first, lit - st.FIRST_BASE)[0] for lit, _, _ in st.HOOKS] == \
            [st.SUB_BASE + entry for _, _, entry in st.HOOKS]
    else:
        assert first == want
    lay = mw.Layout.read(first0)
    got, ref = mw.decode_maps(lay, first[:st.FIRST_SIZE]), mw.decode_maps(lay, want)
    assert len(got) == len(ref) == 5 and all((a == b).all() for a, b in zip(got, ref))
    mw.check_protected(lay, first0, mw.apply(lay, first0, got))      # the draw words alone


def test_mahjong_build_writes_the_redrawn_result_panel(tmp_path):
    from fromance import mjyaku as my
    m = build.build(CUE, tmp_path, {"subtitles": "ko", "mahjong": True})
    user = _reader(tmp_path / m["output_bin"])
    e = iso9660.list_files(user)["MJDAT2.BIN"]
    got = b"".join(user(e.lba + i) for i in range(e.sectors))[:e.size]
    src = SourceDisc(CUE)
    try:
        files = iso9660.list_files(src.user)
        first0 = src.read_file(files["1ST.BIN"].lba, files["1ST.BIN"].size)
        mj2 = src.read_file(files["MJDAT2.BIN"].lba, files["MJDAT2.BIN"].size)
    finally:
        src.close()
    want = my.build_file(first0, mj2, my.load_yaku(ROOT / "translation/graphics/mj_yaku.json"),
                         my.load_score(ROOT / "translation/graphics/mj_score.json"),
                         my.load_titles(ROOT / "translation/graphics/mj_title.json"))
    assert got == want and len(got) == len(mj2)


def test_profile_build_with_shop_and_mahjong(tmp_path):
    from fromance import profile as pf
    comps = {"subtitles": None, "shop": True, "mahjong": True, "profile": True}
    m = build.build(CUE, tmp_path, comps)
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        first0, sl0, shop0, pd0 = (read(src.user, old[n]) for n in ("1ST.BIN", "SL.BIN", "SHOPDAT.BIN", "PROFDAT.BIN"))
    finally:
        src.close()
    sl, shop, pd = (read(user, files[n]) for n in ("SL.BIN", "SHOPDAT.BIN", "PROFDAT.BIN"))
    build.check_graphics(first0, sl0, shop0, sl, shop, comps, pd0, pd)
    off = {k: v for k, v in comps.items() if k != "profile"}
    with pytest.raises(build.BuildError, match="PROFDAT"):              # profile off: PROFDAT must stay
        build.check_graphics(first0, sl0, shop0, sl0, shop0, off, pd0, pd)
    with pytest.raises(build.BuildError, match="PROFDAT"):              # profile on: both files needed
        build.check_graphics(first0, sl0, shop0, sl, shop, comps)
    names, bodies = pf.load_texts(ROOT / "translation/graphics/profile.json")
    lay = pf.Layout.read(first0, sl0, pd0)
    assert all(np.array_equal(pf.decode_body(lay, k, sl, pd), pf.render_body(pf.layout_body(bodies[k])))
               for k in range(5))


def test_kisekae_build_writes_only_the_dress_up_buttons(tmp_path):
    from fromance import kisebtn as kb
    m = build.build(CUE, tmp_path, {"subtitles": None, "kisekae": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        changed = {n for n in old if old[n].lba < src.sectors and read(user, files[n]) != read(src.user, old[n])}
        first0, kd0 = read(src.user, old["1ST.BIN"]), read(src.user, old["KISEDAT.BIN"])
    finally:
        src.close()
    assert changed == {"KISEDAT.BIN"}
    assert read(user, files["KISEDAT.BIN"]) == kb.build_file(first0, kd0, kb.load_texts(ROOT / "translation/graphics/kise_buttons.json"))


@pytest.mark.parametrize("text", [None, "ko"])
def test_title_build_writes_the_korean_logo(tmp_path, text):
    from PIL import Image
    from fromance import title as tt
    m = build.build(CUE, tmp_path, {"subtitles": text, "mahjong": True, "title": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        first0, mj30 = read(src.user, old["1ST.BIN"]), read(src.user, old["MJDAT3.BIN"])
    finally:
        src.close()
    first, mj3 = read(user, files["1ST.BIN"]), read(user, files["MJDAT3.BIN"])
    want_first, want_mj3 = tt.build_files(first0, mj30, Image.open(ROOT / "title_ko.png"))
    assert mj3 == want_mj3
    mask = tt.editable_first(len(first0))
    assert (np.frombuffer(first[:len(first0)], np.uint8)[mask] == np.frombuffer(want_first, np.uint8)[mask]).all()


def test_title_alone_changes_only_its_files(tmp_path):
    m = build.build(CUE, tmp_path, {"subtitles": None, "title": True})
    user = _reader(tmp_path / m["output_bin"])
    files = iso9660.list_files(user)
    read = lambda f, n: b"".join(f(n.lba + i) for i in range(n.sectors))[:n.size]
    src = SourceDisc(CUE)
    try:
        old = iso9660.list_files(src.user)
        changed = {n for n in old if old[n].lba < src.sectors and read(user, files[n]) != read(src.user, old[n])}
    finally:
        src.close()
    assert changed == {"1ST.BIN", "MJDAT3.BIN"}
