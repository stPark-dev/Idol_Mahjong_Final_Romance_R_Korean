import pytest

from fromance import disc

CHD_CUE = """FILE "Idol Mahjong - Final Romance R (Japan).bin" BINARY
  TRACK 01 MODE1/2352
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    INDEX 00 14:07:53
    INDEX 01 14:09:53
"""

REDUMP_CUE = """CATALOG 0000000000000
FILE "Idol Mahjong - Final Romance R (Japan) (Track 01).bin" BINARY
  TRACK 01 MODE1/2352
    INDEX 01 00:00:00
FILE "Idol Mahjong - Final Romance R (Japan) (Track 02).bin" BINARY
  TRACK 02 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:02:00
FILE "Idol Mahjong - Final Romance R (Japan) (Track 03).bin" BINARY
  TRACK 03 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:01:74
"""


def test_single_file_cue_track1_ends_at_track2_index0():
    t = disc.parse_cue(CHD_CUE)
    assert t.file == "Idol Mahjong - Final Romance R (Japan).bin"
    assert t.offset == 0
    assert t.sectors == (14 * 60 + 7) * 75 + 53 == 63578 == disc.TRACK1_SECTORS


def test_split_cue_track1_is_its_own_file():
    t = disc.parse_cue(REDUMP_CUE)
    assert t.file == "Idol Mahjong - Final Romance R (Japan) (Track 01).bin"
    assert t.sectors is None          # whole file


def test_rejects_non_mode1_first_track():
    with pytest.raises(disc.DiscError):
        disc.parse_cue(CHD_CUE.replace("MODE1/2352", "MODE2/2352"))


def test_msf_parse():
    assert disc.msf("00:02:00") == 150
    with pytest.raises(disc.DiscError):
        disc.msf("00:60:00")


def test_verify_fails_on_truncated_bin_instead_of_looping(tmp_path):
    (tmp_path / "Idol Mahjong - Final Romance R (Japan).cue").write_text(CHD_CUE)
    (tmp_path / "Idol Mahjong - Final Romance R (Japan).bin").write_bytes(bytes(2352 * 3))
    d = disc.SourceDisc(tmp_path / "Idol Mahjong - Final Romance R (Japan).cue")
    with pytest.raises(disc.DiscError, match="truncated"):
        d.verify()


def test_supported_revision_constants():
    assert disc.TRACK1_SHA1 == "2d12e26747034b4385e0b61164c2e715cc403cd1"
    assert disc.TRACK1_SECTORS == 63578


def test_wrong_sector_count_is_rejected(tmp_path):
    (tmp_path / "x.cue").write_text(REDUMP_CUE.replace("Idol Mahjong - Final Romance R (Japan) (Track 01)", "x"))
    (tmp_path / "x.bin").write_bytes(bytes(2352 * 5))
    with pytest.raises(disc.DiscError, match="sectors"):
        disc.SourceDisc(tmp_path / "x.cue")
