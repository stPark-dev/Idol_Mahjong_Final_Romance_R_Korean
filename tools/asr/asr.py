#!/usr/bin/env python3
"""조사용: 동영상 음성 받아쓰기 후보. work/audio16/*.wav → work/asr/<name>.<model>.json
usage: asr.py lv3|kotoba"""
import glob, json, os, sys, wave
import numpy as np
from faster_whisper import WhisperModel

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "work")
PROMPT = os.environ.get("ASR_PROMPT", "アイドル麻雀ファイナルロマンスR。") or None
MODELS = {"lv3": "large-v3", "lv3np": "large-v3", "kotoba": "kotoba-tech/kotoba-whisper-v2.0-faster"}


def load_wav(path):
    w = wave.open(path)
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def main(which):
    m = WhisperModel(MODELS[which], device="cuda", compute_type="float16")
    os.makedirs(f"{ROOT}/asr", exist_ok=True)
    for f in sorted(glob.glob(f"{ROOT}/audio16/*.wav")):
        name = os.path.basename(f)[:-4]
        out = f"{ROOT}/asr/{name}.{which}.json"
        if os.path.exists(out):
            continue
        segs, _ = m.transcribe(load_wav(f), language="ja", beam_size=5, initial_prompt=PROMPT,
                               vad_filter=False, word_timestamps=which.startswith("lv3"), condition_on_previous_text=False)
        full = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text,
                 "words": [[round(w.start, 2), round(w.end, 2), w.word] for w in (s.words or [])]} for s in segs]
        json.dump({"full": full}, open(out, "w"), ensure_ascii=False, indent=1)
        print(which, name, " / ".join(s["text"] for s in full), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
