#!/usr/bin/env python3
"""piper_shard.py LIST OUTDIR SHARD NSHARDS — renders lines where (i-1)%NSHARDS==SHARD"""
import sys, os, wave, time
os.environ.setdefault("OMP_NUM_THREADS", "1")
from pathlib import Path
from piper import PiperVoice
lst, out, shard, n = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
out.mkdir(parents=True, exist_ok=True)
lines = [l.rstrip("\n") for l in lst.read_text(encoding="utf-8").splitlines() if l.strip()]
width = max(4, len(str(len(lines))))
voice = PiperVoice.load(str(Path(__file__).parent / "voices" / "de_DE-thorsten-high.onnx"))
t0 = time.time(); done = 0
for i, text in enumerate(lines, 1):
    if (i - 1) % n != shard: continue
    p = out / f"{i:0{width}d}.wav"
    if p.exists() and p.stat().st_size > 100: continue
    with wave.open(str(p), "wb") as w: voice.synthesize_wav(text, w)
    done += 1
    if done % 100 == 0: print(f"  shard{shard}: {done} done, {time.time()-t0:.0f}s", flush=True)
print(f"shard{shard} finished {done} files in {time.time()-t0:.0f}s", flush=True)
