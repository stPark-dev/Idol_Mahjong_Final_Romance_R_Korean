import hashlib
import json
import os
import pathlib
import subprocess

import pytest

from fromance import build, release
from fromance.disc import TRACK1_SHA1

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
NO_CUE = ROOT / "no-such-source.cue"            # the gate runs before the source is opened


def _entries(folder, name, entries):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    return p


def _glossary(tmp_path, states):
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps({"decisions": [{"id": f"G-{i}", "state": s} for i, s in enumerate(states)]}),
                 encoding="utf-8")
    return p


def test_only_approved_entries_are_eligible(tmp_path):
    voice, gfx = tmp_path / "voice", tmp_path / "graphics"
    ok = [_entries(voice, "ok.json", [{"id": "a", "status": "distribution_eligible"}]),
          _entries(gfx, "ok.json", [{"id": "b", "state": "approved"}])]
    gl = _glossary(tmp_path, ["approved"])
    release.check_eligible(ok, gl)
    bad = [_entries(voice, "bad.json", [{"id": "c", "status": "reviewed"}, {"id": "d", "status": "approved"},
                                        {"id": "e", "state": "approved"}]),
           _entries(gfx, "bad.json", [{"id": "f", "state": "proposed"}, {"id": "g"},
                                      {"id": "h", "status": "distribution_eligible"},
                                      {"id": "i", "state": "approved", "status": "distribution_eligible"}])]
    with pytest.raises(release.ReleaseError, match="bad.json") as ex:
        release.check_eligible(ok + bad, gl)
    for s in ("c=reviewed", "d=approved", "e=None", "f=proposed", "g=None", "h=None", "i=both"):
        assert s in str(ex.value)


def test_a_pending_glossary_decision_blocks_the_release(tmp_path):
    ok = [_entries(tmp_path / "voice", "ok.json", [{"id": "a", "status": "distribution_eligible"}])]
    with pytest.raises(release.ReleaseError, match="G-1"):
        release.check_eligible(ok, _glossary(tmp_path, ["approved", "proposed"]))


def test_a_malformed_file_is_a_release_error(tmp_path):
    p = tmp_path / "graphics" / "x.json"
    p.parent.mkdir()
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="x.json"):
        release.check_eligible([p], _glossary(tmp_path, []))


def test_release_inputs_are_every_translation_the_build_reads():
    names = {p.name for p in release.inputs()}
    assert {"as.json", "ke.json", "mi.json", "ri.json", "ry.json"} <= names
    graphics = {p.name for p in (ROOT / "translation/graphics").glob("*.json")}
    assert graphics <= names
    release.check_eligible(release.inputs(), release.GLOSSARY)      # the current translations are all eligible


def test_release_build_refuses_an_unapproved_input(tmp_path, monkeypatch):
    bad = _entries(tmp_path / "graphics", "bad.json", [{"id": "x", "state": "proposed"}])
    monkeypatch.setattr(release, "inputs", lambda: [bad])
    with pytest.raises(build.BuildError, match="not approved"):
        build.build(NO_CUE, tmp_path / "out", dict(release.COMPONENTS, release=True))
    assert not (tmp_path / "out").exists()


def test_release_build_needs_every_component(tmp_path):
    with pytest.raises(build.BuildError, match="every component"):
        build.build(NO_CUE, tmp_path / "out", {"subtitles": "ko", "shop": True, "release": True})


@pytest.mark.parametrize("v", ["1.0", "v1.0.0", "1.0.0\n", "١.٠.٠", ""])
def test_bad_versions_are_refused(tmp_path, v):
    with pytest.raises(release.ReleaseError, match="version"):
        release.make(NO_CUE, tmp_path, v)


def test_a_missing_xdelta3_is_refused_before_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "find_xdelta3", lambda: None)
    with pytest.raises(release.ReleaseError, match="xdelta3"):
        release.make(NO_CUE, tmp_path, "0.9.0")


def test_an_explicit_xdelta3_must_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("XDELTA3", str(tmp_path / "nothing"))
    with pytest.raises(release.ReleaseError, match="XDELTA3"):
        release.find_xdelta3()


source = pytest.mark.skipif(not CUE.exists() or not release.find_xdelta3(), reason="원본 디스크 또는 xdelta3 없음")


def _sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    out = tmp_path_factory.mktemp("rel")
    return out, release.make(CUE, out, "0.9.0")


@source
def test_patch_turns_the_original_track_into_the_korean_one(made):
    out, m = made
    dist = out / release.DIST
    assert sorted(p.name for p in dist.iterdir()) == sorted([m["patch"], release.GUIDE, "release.json"])
    patch = dist / m["patch"]
    assert patch.name == "Idol Mahjong - Final Romance R 한글 패치 v0.9.0.xdelta"
    assert 0 < patch.stat().st_size < 4 << 20 and patch.stat().st_mode & 0o777 == 0o644
    assert m["distribution"] is True and m["source_track1_sha1"] == TRACK1_SHA1
    applied = out / "applied.bin"
    subprocess.run([release.find_xdelta3(), "-d", "-f", "-s", str(CUE.with_name(m["source_track1"])), str(patch),
                    str(applied)], check=True)
    assert _sha1(applied) == m["output_track1_sha1"] != TRACK1_SHA1
    built = json.loads((out / "build/manifest.json").read_text(encoding="utf-8"))
    assert built["distribution"] is True and built["output_sha1"] == m["output_track1_sha1"]
    assert not list(out.rglob("*.part")) and not list(out.rglob("*check*"))


@source
def test_the_built_audio_tracks_are_the_source_ones(made):
    out, m = made
    assert len(m["tracks"]) == 16
    for t in m["tracks"][1:]:
        built = out / "build" / t["file"].replace("(Japan)", "(Korean)")
        assert _sha1(built) == t["sha1"] == _sha1(CUE.with_name(t["file"]))
    guide = (out / release.DIST / release.GUIDE).read_text(encoding="utf-8")
    assert "v0.9.0" in guide and m["output_track1_sha1"] in guide and m["patch"] in guide
    assert all(t["sha1"] in guide and t["file"] in guide for t in m["tracks"])


@source
def test_patch_refuses_another_track(made, tmp_path):
    out, m = made
    src = CUE.with_name(m["source_track1"])
    data = bytearray(src.read_bytes())
    k = len(data) // 2
    assert data[k] == (out / "build" / m["output_bin"]).read_bytes()[k]       # a byte the patch keeps from the source
    data[k] ^= 0xFF
    wrong = tmp_path / "wrong.bin"
    wrong.write_bytes(bytes(data))
    r = subprocess.run([release.find_xdelta3(), "-d", "-f", "-s", str(wrong), str(out / release.DIST / m["patch"]),
                        str(tmp_path / "o.bin")], capture_output=True)
    assert r.returncode != 0


@pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")
def test_a_failed_patch_leaves_no_release(tmp_path, monkeypatch):
    dist = tmp_path / release.DIST
    dist.mkdir()
    (dist / "release.json").write_text("{}", encoding="utf-8")          # left from an earlier version
    (dist / release.GUIDE).write_text("old", encoding="utf-8")
    monkeypatch.setenv("XDELTA3", "/bin/false")
    with pytest.raises(release.ReleaseError, match="xdelta3 failed"):
        release.make(CUE, tmp_path, "0.9.1")
    assert list(dist.iterdir()) == [] and not list(tmp_path.glob("*.part")) and not list(tmp_path.rglob("*check*"))


@source
def test_cli_release_writes_the_patch(tmp_path, capsys):
    import khpatch
    assert khpatch.main(["release", "--source", str(CUE), "--out", str(tmp_path), "--version", "0.9.0"]) == 0
    out = json.loads(capsys.readouterr().out)
    dist = tmp_path / release.DIST
    assert out["distribution"] is True and (dist / out["patch"]).is_file() and (dist / release.GUIDE).is_file()
    with pytest.raises(SystemExit):
        khpatch.main(["release", "--source", str(CUE), "--out", str(tmp_path)])      # the version is required
    assert khpatch.main(["release", "--source", str(NO_CUE), "--out", str(tmp_path), "--version", "x"]) == 1
    assert "version" in capsys.readouterr().err
