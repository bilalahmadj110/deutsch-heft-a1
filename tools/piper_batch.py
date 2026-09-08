#!/usr/bin/env python3
"""Batch German TTS with Piper (neural). Usage: piper_batch.py LIST OUTDIR [--start N]"""
import sys, csv, wave, time
from pathlib import Path
from piper import PiperVoice

MODEL = Path(__file__).parent / "voices" / "de_DE-thorsten-high.onnx"

def main():
    lst, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    lines = [l.rstrip("\n") for l in lst.read_text(encoding="utf-8").splitlines() if l.strip()]
    voice = PiperVoice.load(str(MODEL))
    width = max(4, len(str(len(lines))))
    rows, t0 = [], time.time()
    for i, text in enumerate(lines, 1):
        fn = f"{i:0{width}d}.wav"
        p = out / fn
        if not p.exists() or p.stat().st_size < 100:
            with wave.open(str(p), "wb") as w:
                voice.synthesize_wav(text, w)
        rows.append((i, text, fn))
        if i % 200 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(lines)}  {el:.0f}s elapsed  eta {el/i*(len(lines)-i):.0f}s", flush=True)
    with (out / "manifest.csv").open("w", encoding="utf-8", newline="") as f:
        wtr = csv.writer(f); wtr.writerow(("number", "text", "filename")); wtr.writerows(rows)
    print(f"DONE {len(rows)} files in {time.time()-t0:.0f}s -> {out}")

if __name__ == "__main__":
    main()
