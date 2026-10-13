"""원본 디스크가 있을 때만 도는 빌드 통합 검사."""
import hashlib
import json
import os
import pathlib
import struct

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
    assert new == [st.PACK_NAME] and files[st.PACK_NAME].lba == 63428
    assert files[st.PACK_NAME].lba + files[st.PACK_NAME].sectors <= 63578
    first = files["1ST.BIN"]
    assert first.lba == 22 and first.sectors == 169
    data = b"".join(user(first.lba + i) for i in range(first.sectors))[:first.size]
    orig = src.read_file(22, 344184)
    sub = st.fill_pack_size((ROOT / "assets/subtitle/SUB.BIN").read_bytes(), files[st.PACK_NAME].size)
    assert data == st.patch_first(orig, sub)
    pe = files[st.PACK_NAME]
    pack = b"".join(user(pe.lba + i) for i in range(pe.sectors))[:pe.size]
    assert struct.unpack_from(">I", pack)[0] == st.MAGIC
    assert m["subtitles"] == "ja" and m["distribution"] is False
    assert json.loads((out / "manifest.json").read_text())["output_sha1"] == _sha1(out / m["output_bin"])
