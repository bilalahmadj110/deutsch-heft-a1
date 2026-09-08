# Regenerating the audio

`german-audio/` ships as MP3 (48 kbps mono) so the Heft has sound straight out of a
clone. The WAV masters are not committed — they are ~294 MB. To rebuild them:

```sh
python3 -m venv .venv
./.venv/bin/pip install piper-tts
mkdir -p voices && curl -L -o voices/de_DE-thorsten-high.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx
curl -L -o voices/de_DE-thorsten-high.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json

# one line of .audio-list.txt per clip, numbered in order
./.venv/bin/python tools/piper_batch.py .audio-list.txt german-audio
./.venv/bin/python tools/piper_batch.py tools/parts-list.txt german-audio/parts
```

`piper_shard.py` does the same across N processes (`… LIST OUTDIR SHARD NSHARDS`);
six shards render the full list in about 35 minutes on 8 cores.

`.audio-list.txt` is the authority for numbering: clip *n* is `german-audio/000n.mp3`,
and `index.html` embeds the same list as `MANIFEST`. Append to the list, never reorder
it, or every existing file points at the wrong text.
