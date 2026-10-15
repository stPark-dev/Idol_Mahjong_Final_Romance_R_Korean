#!/usr/bin/env python3
"""아이돌 마작 파이널 로맨스 R 한글 패치 빌드.

usage: khpatch.py build --source <원본.cue> --out <폴더> [--subtitles ja|ko|none] [--shop] [--mahjong] [--profile] [--kisekae] [--title]
  --subtitles ja : 시험용 — 일본어 받아쓰기 원문을 자막으로 띄움(배포 불가)
  --subtitles ko : 한국어 자막
  --shop         : 시험용 — 상점 그림 글자를 한국어로(번역 제안 상태, 배포 불가)
  --mahjong      : 시험용 — 대국 화면 그림 글자를 한국어로
  --profile      : 시험용 — 프로필(데모) 이름·본문 그림을 한국어로
  --kisekae      : 시험용 — 옷 갈아입히기 화면 단추 글자를 한국어로
  --title        : 시험용 — 타이틀 로고를 title_ko.png로(D-3)

usage: khpatch.py release --source <원본.cue> --out <폴더> --version N.N.N
  모든 구성 요소를 넣은 배포 빌드. 번역이 모두 승인 상태가 아니면 멈춘다.
  <폴더>/build에 빌드, <폴더>/dist에 Track 01용 xdelta3 패치·적용 방법.txt·release.json을 만들고 패치를 원본에 적용해 빌드와 같은지 확인한다.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fromance import build, release  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--source", required=True, type=Path)
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--subtitles", choices=("ja", "ko", "none"), default="ko")
    b.add_argument("--shop", action="store_true")
    b.add_argument("--mahjong", action="store_true")
    b.add_argument("--profile", action="store_true")
    b.add_argument("--kisekae", action="store_true")
    b.add_argument("--title", action="store_true")
    r = sub.add_parser("release")
    r.add_argument("--source", required=True, type=Path)
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--version", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "release":
        try:
            rel = release.make(a.source, a.out, a.version)
        except (release.ReleaseError, build.BuildError, OSError, ValueError) as ex:
            print(f"오류: {ex}", file=sys.stderr)
            return 1
        print(json.dumps({k: rel[k] for k in ("version", "patch", "output_track1_sha1", "distribution")},
                         ensure_ascii=False, indent=2))
        return 0
    m = build.build(a.source, a.out, {"subtitles": None if a.subtitles == "none" else a.subtitles,
                                      "shop": a.shop, "mahjong": a.mahjong,
                                      "profile": a.profile, "kisekae": a.kisekae, "title": a.title})
    print(json.dumps({k: m[k] for k in ("subtitles", "components", "sectors_changed", "output_bin", "output_sha1",
                                        "distribution")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
