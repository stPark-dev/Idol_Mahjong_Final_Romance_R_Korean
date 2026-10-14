"""translation/voice/*.json 형식·시간 검사. 음성 파일(work/audio16/)이 있으면 말소리 구간 누락도 검사한다."""
import glob, json, os, re, wave
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = sorted(glob.glob(f"{ROOT}/translation/voice/*.json"))
MOVIES = {f"{c}{n}.CPK" for c in ("AS", "KE", "MI", "RI", "RY") for n in ("OP", "01", "02", "03", "04")} | {"RY05.CPK"}
STATUSES = {"ja_draft", "ko_draft", "reviewed", "distribution_eligible"}


def load(f):
    return json.load(open(f, encoding="utf-8"))


def test_all_movies_accounted_once():
    seen = []
    for f in FILES:
        seen += load(f)["movies"]
    assert sorted(seen) == sorted(MOVIES)


@pytest.mark.parametrize("f", FILES, ids=os.path.basename)
def test_entries_well_formed(f):
    d = load(f)
    key = os.path.basename(f)[:-5]
    ids = [e["id"] for e in d["entries"]]
    assert len(ids) == len(set(ids))
    for e in d["entries"]:
        assert re.fullmatch(rf"{key}\.\d{{3}}", e["id"])
        assert e["movie"] in d["movies"]
        assert 0 <= e["start"] < e["end"]
        assert e["ja"].strip()
        assert e["status"] in STATUSES
        if e["status"] != "ja_draft":
            assert e["ko"].strip()


@pytest.mark.parametrize("f", FILES, ids=os.path.basename)
def test_no_overlap_within_movie(f):
    by = {}
    for e in load(f)["entries"]:
        by.setdefault(e["movie"], []).append((e["start"], e["end"], e["id"]))
    for mv, rs in by.items():
        rs.sort()
        for a, b in zip(rs, rs[1:]):
            assert a[1] <= b[0], f"{mv}: {a[2]} overlaps {b[2]}"


def voiced(path, hop=160):
    w = wave.open(path)
    a = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    e = np.array([np.sqrt((a[i:i + 400] ** 2).mean()) for i in range(0, len(a) - 400, hop)])
    thr = max(600, np.percentile(e, 20) * 3)
    regs, s = [], None
    for i, v in enumerate(e > thr):
        t = i * hop / 16000
        if v and s is None:
            s = t
        if not v and s is not None:
            if t - s > 0.15:
                regs.append((s, t))
            s = None
    return regs, len(a) / 16000


@pytest.mark.parametrize("f", FILES, ids=os.path.basename)
def test_voiced_regions_covered(f):
    d = load(f)
    for mv in d["movies"]:
        wav = f"{ROOT}/work/audio16/{mv[:-4]}.wav"
        if not os.path.exists(wav):
            pytest.skip("work/audio16 없음")
        cov = [(e["start"], e["end"]) for e in d["entries"] if e["movie"] == mv]
        cov += [tuple(map(float, x["range"].split("-"))) for x in d["excluded"] if x["movie"] == mv]
        regs, dur = voiced(wav)
        for a, b in cov:
            assert b <= dur + 0.05, f"{mv} cue past end {b} > {dur}"
        for a, b in regs:
            assert any(s < b and e > a for s, e in cov), f"{mv} voiced {a:.2f}-{b:.2f} not covered"


GLOSSARY = f"{ROOT}/translation/glossary.json"


def test_glossary_is_well_formed():
    ds = json.load(open(GLOSSARY, encoding="utf-8"))["decisions"]
    ids = [d["id"] for d in ds]
    assert len(ids) == len(set(ids))
    for d in ds:
        assert d["state"] in {"proposed", "approved", "unresolved", "conflicting"}
        assert d["source"].strip() and d["output"].strip() and d["scope"].strip()


def test_notes_only_cite_known_glossary_decisions():
    ids = {d["id"] for d in json.load(open(GLOSSARY, encoding="utf-8"))["decisions"]}
    for f in FILES:
        for e in load(f)["entries"]:
            for ref in re.findall(r"\b[NTV]-[A-Z]+\b", e.get("ko_note", "")):
                assert ref in ids, (e["id"], ref)


def test_distribution_needs_approved_glossary():
    # a line cannot be distribution_eligible while any decision is still proposed/unresolved
    ds = json.load(open(GLOSSARY, encoding="utf-8"))["decisions"]
    pending = [d["id"] for d in ds if d["state"] != "approved"]
    for f in FILES:
        for e in load(f)["entries"]:
            if e["status"] == "distribution_eligible":
                assert not pending, (e["id"], pending)
