"""원본 디스크가 있을 때만 도는 통합 검사(orig/ 또는 FROMANCE_SOURCE_CUE)."""
import os
import pathlib

import pytest

from fromance import cdsector, iso9660
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
pytestmark = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def disc():
    d = SourceDisc(CUE)
    yield d
    d.close()


def test_source_is_the_supported_revision(disc):
    disc.verify()


def test_root_directory_round_trips_unchanged(disc):
    files = iso9660.list_files(disc.user)
    assert len(files) == 63 and files["1ST.BIN"] == iso9660.Entry("1ST.BIN", 22, 344184)
    secs = iso9660.repack_root(disc.user, {}, [])
    assert secs == {20: disc.user(20), 21: disc.user(21)}


def test_sector_parity_reproduces_every_root_and_program_sector(disc):
    for lba in [16, 20, 21] + list(range(22, 22 + 169)):
        raw = disc.raw(lba)
        assert bytes(cdsector.fix_mode1(bytearray(raw))) == raw, lba


def test_free_space_facts(disc):
    files = iso9660.list_files(disc.user)
    data = [e for e in files.values() if not e.name.startswith("CDDA")]
    assert max(e.lba + e.sectors for e in data) == 63428
    first = files["1ST.BIN"]
    assert first.sectors * 2048 - first.size == 1928
    assert min(e.lba for e in files.values() if e.lba > first.lba) == first.lba + first.sectors
