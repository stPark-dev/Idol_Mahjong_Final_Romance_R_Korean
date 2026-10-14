#!/usr/bin/env python3
"""아이돌 마작 파이널 로맨스 R 한글 패치 빌드.

usage: khpatch.py build --source <원본.cue> --out <폴더> [--subtitles ja|ko|none] [--shop] [--mahjong]
  --subtitles ja : 시험용 — 일본어 받아쓰기 원문을 자막으로 띄움(배포 불가)
  --subtitles ko : 한국어 자막
  --shop         : 시험용 — 상점 그림 글자를 한국어로(번역 제안 상태, 배포 불가)
  --mahjong      : 시험용 — 대국 화면 그림 글자를 한국어로(부르기 창, 큰 선언)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fromance import build  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--source", required=True, type=Path)
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--subtitles", choices=("ja", "ko", "none"), default="ko")
    b.add_argument("--shop", action="store_true")
    b.add_argument("--mahjong", action="store_true")
    a = ap.parse_args(argv)
    m = build.build(a.source, a.out, {"subtitles": None if a.subtitles == "none" else a.subtitles,
                                      "shop": a.shop, "mahjong": a.mahjong})
    print(json.dumps({k: m[k] for k in ("subtitles", "components", "sectors_changed", "output_bin", "output_sha1",
                                        "distribution")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
