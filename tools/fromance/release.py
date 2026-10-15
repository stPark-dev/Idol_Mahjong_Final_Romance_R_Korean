"""Release: the eligibility gate and the xdelta3 patch for track 1.

Only track 1 changes; the audio tracks 02-16 and the CUE are the user's own, kept as they are. The
patch is checked by applying it to the source and comparing the result with the product build.
out_dir/build holds the product build (the whole disc, never published); out_dir/dist holds what is
published: the patch, the guide and release.json.

The gate covers the translation JSON and the glossary. The title logo title_ko.png has no entry of
its own; it was decided by the project owner (D-3).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import build
from .disc import TRACK1_SHA1, parse_cue

ROOT = build.ROOT
GRAPHICS_DIR = ROOT / "translation/graphics"
GLOSSARY = ROOT / "translation/glossary.json"
COMPONENTS = {"subtitles": "ko", "shop": True, "mahjong": True, "profile": True, "kisekae": True, "title": True}
LOCAL_XDELTA3 = ROOT / "work/tools/xdelta3/usr/bin/xdelta3"
DIST = "dist"
GUIDE = "적용 방법.txt"
PATCH = "Idol Mahjong - Final Romance R 한글 패치 v{version}.xdelta"
WINDOW = 1 << 28                # source window larger than track 1, so every match is found


class ReleaseError(RuntimeError):
    pass


def inputs() -> list[Path]:
    """Every translation file the full build reads."""
    return sorted(build.VOICE_DIR.glob("*.json")) + sorted(GRAPHICS_DIR.glob("*.json"))


def _entry_state(e: dict, voice: bool):
    """(eligible, shown state): subtitle lines carry status only, graphics entries state only."""
    if "status" in e and "state" in e:
        return False, "both"
    if voice:
        return e.get("status") == "distribution_eligible", e.get("status")
    return e.get("state") == "approved", e.get("state")


def check_eligible(paths, glossary: Path) -> None:
    """Every subtitle line distribution_eligible, every graphics entry and glossary decision approved."""
    bad = []
    for p in map(Path, paths):
        try:
            entries = json.loads(p.read_text(encoding="utf-8"))["entries"]
        except (OSError, ValueError, KeyError, TypeError) as ex:
            raise ReleaseError(f"{p.name}: not a translation file ({ex!r})") from ex
        for e in entries:
            ok, state = _entry_state(e, p.parent.name == "voice")
            if not ok:
                bad.append(f"{p.name}:{e.get('id')}={state}")
    try:
        decisions = json.loads(Path(glossary).read_text(encoding="utf-8"))["decisions"]
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise ReleaseError(f"{Path(glossary).name}: not a glossary ({ex!r})") from ex
    bad += [f"glossary:{d.get('id')}={d.get('state')}" for d in decisions if d.get("state") != "approved"]
    if bad:
        raise ReleaseError(f"not approved for release ({len(bad)}): " + ", ".join(bad[:20]))


def find_xdelta3():
    """XDELTA3 from the environment (must exist), else the PATH or the local tools folder; None if none."""
    env = os.environ.get("XDELTA3")
    if env:
        if not (Path(env).is_file() and os.access(env, os.X_OK)):
            raise ReleaseError(f"XDELTA3={env} is not an executable file")
        return env
    for c in (shutil.which("xdelta3"), LOCAL_XDELTA3):
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def _run(args) -> None:
    try:
        r = subprocess.run([str(a) for a in args], capture_output=True, text=True, errors="replace")
    except OSError as ex:
        raise ReleaseError(f"xdelta3 failed: {ex}") from ex
    if r.returncode:
        raise ReleaseError(f"xdelta3 failed ({r.returncode}): {r.stderr.strip()[:300]}")


def make(source_cue: Path, out_dir: Path, version: str) -> dict:
    """out_dir/build (the product build) and out_dir/dist (patch, guide, release.json)."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ReleaseError(f"version {version!r} is not N.N.N")
    xd = find_xdelta3()
    if not xd:
        raise ReleaseError("xdelta3 not found (set XDELTA3 or install it)")
    source_cue, out_dir = Path(source_cue).resolve(), Path(out_dir).resolve()
    dist = out_dir / DIST
    dist.mkdir(parents=True, exist_ok=True)
    for old in [dist / GUIDE, dist / "release.json", *dist.glob(PATCH.format(version="*"))]:
        old.unlink(missing_ok=True)               # nothing from an earlier run may look like this release
    m = build.build(source_cue, out_dir / "build", dict(COMPONENTS, release=True))
    cue_text = source_cue.read_text(encoding="utf-8", errors="replace")
    track1 = parse_cue(cue_text).file
    files = re.findall(r'FILE\s+"(.+)"\s+BINARY', cue_text)
    tracks = [{"file": f, "sha1": TRACK1_SHA1 if f == track1 else _sha1(source_cue.with_name(f))} for f in files]
    src, built = source_cue.with_name(track1), out_dir / "build" / m["output_bin"]
    patch = dist / PATCH.format(version=version)
    fd, part = tempfile.mkstemp(suffix=".part", dir=out_dir)
    os.close(fd)
    fd, check = tempfile.mkstemp(suffix=".check", dir=out_dir)
    os.close(fd)
    part, check = Path(part), Path(check)
    try:
        _run([xd, "-e", "-9", "-f", "-B", WINDOW, "-s", src, built, part])
        _run([xd, "-d", "-f", "-B", WINDOW, "-s", src, part, check])
        if _sha1(check) != m["output_sha1"]:
            raise ReleaseError("the applied patch differs from the product build")
        os.chmod(part, 0o644)                    # mkstemp makes it private
        os.replace(part, patch)
    finally:
        part.unlink(missing_ok=True)
        check.unlink(missing_ok=True)
    rel = {"version": version, "patch": patch.name, "source_track1": track1, "source_track1_sha1": TRACK1_SHA1,
           "output_bin": m["output_bin"], "output_track1_sha1": m["output_sha1"], "tracks": tracks,
           "distribution": m["distribution"]}
    (dist / GUIDE).write_text(guide(rel), encoding="utf-8")
    (dist / "release.json").write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")
    return rel


def guide(rel: dict) -> str:
    t1 = rel["source_track1"]
    lines = [f"아이돌 마작 파이널 로맨스 R 한글 패치 v{rel['version']}",
             "",
             "대상: 「Idol Mahjong - Final Romance R (Japan)」 (세가 새턴, T-16703G V1.000) BIN/CUE 16트랙",
             "확인 환경: 에뮬레이터(Mednafen). 실기는 확인하지 않았습니다.",
             "",
             "적용 방법",
             "1. 원본 BIN/CUE 폴더를 통째로 복사합니다(원본은 남겨 두세요).",
             "2. 복사한 폴더의 Track 01 SHA-1이 아래 값과 같은지 먼저 확인합니다.",
             f"3. xdelta3 또는 Delta Patcher로 「{rel['patch']}」를 「{t1}」에 적용합니다.",
             f"4. 결과 파일 이름을 「{t1}」로 바꿔 복사한 폴더의 원본 Track 01을 대신합니다.",
             "   오디오 트랙(02-16)과 CUE는 그대로 씁니다.",
             f"   명령 예) xdelta3 -d -s \"{t1}\" \"{rel['patch']}\" \"patched.bin\"",
             "",
             "원본 확인(SHA-1). Track 01이 다르면 적용이 실패하거나 잘못된 결과가 나옵니다."]
    lines += [f"  {t['sha1']}  {t['file']}" for t in rel["tracks"]]
    lines += ["",
              "적용 결과 Track 01 (SHA-1)",
              f"  {rel['output_track1_sha1']}",
              "",
              "이 패치만으로는 게임을 할 수 없습니다. 원본 디스크 이미지는 직접 가지고 있어야 합니다.",
              ""]
    return "\n".join(lines)
