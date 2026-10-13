#!/usr/bin/env python3
"""조사용: 구간만 잘라 여러 설정으로 다시 받아쓰기. usage: asr_slice.py NAME:start-end ...  (work/audio16 기준)"""
import os
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "work"))
import sys, wave, numpy as np
from faster_whisper import WhisperModel
def load(n):
    w = wave.open(f"audio16/{n}.wav"); return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
for k, path in (("lv3", "large-v3"), ("kotoba", "kotoba-tech/kotoba-whisper-v2.0-faster")):
  m = WhisperModel(path, device="cuda", compute_type="float16")
  for arg in sys.argv[1:]:
        n, r = arg.split(":"); a, b = map(float, r.split("-"))
        x = load(n)[int(a*16000):int(b*16000)]
        for t in (0.0, 0.4):
            s, _ = m.transcribe(x, language="ja", beam_size=5, temperature=t, condition_on_previous_text=False)
            print(f"{arg} {k} t={t}: {''.join(y.text for y in s)}", flush=True)
