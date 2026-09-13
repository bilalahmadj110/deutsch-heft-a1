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

# On-demand Polly voices (tools/tts-lambda/)

The Hören panel's **Stimme** select offers the built-in Piper clips (default, free) or an Amazon
Polly engine + voice (standard / neural / generative). Nothing is pre-rendered: with Polly on,
each sentence is synthesised the first time someone listens to it and cached in S3, so it is
paid for exactly once. Diktat always uses the Piper parts.

How a play works (`script#tts-js` in index.html, `HeftTTS.resolve(text)`):

1. normalise the text (NFC, collapse whitespace, trim) and hash `engine|voice|text` with SHA-256;
2. `GET tts/<engine>/<voice>/<hash>.mp3` next to index.html — a cache hit costs nothing but an S3 GET;
3. on a miss, `POST {text, engine, voice}` (header `x-api-key`) to the `heft-tts` Lambda, which does the same hash,
   checks S3 again, calls Polly only if the file is missing, stores the MP3 under that key and
   returns it. If Polly fails or the budget is reached, the page falls back to the Piper clip.

`handler.py` is the Lambda (plain boto3, no dependencies), behind an API Gateway REST API
(`/v1/tts`) that requires an `x-api-key` header on GET and POST; the key belongs to a usage plan
with a throttle (1 req/s, burst 2) and a monthly request quota (5,000), so nobody can run up the bill even
with the key. Further guards in the Lambda: text ≤ 300 characters, an engine/voice allow-list, an
Origin allow-list (the S3 site and localhost:8765), reserved concurrency 3, and a monthly Polly
budget kept as an atomic counter in the DynamoDB table `heft-tts-usage` (`MONTHLY_BUDGET_USD`,
default 8 → HTTP 429 beyond it).

Permanent statistics: the same table keeps an all-time row (`total`) next to the per-month rows —
sentences generated, characters, estimated cost at list price, MP3 bytes and approximate audio
length (48 kbps → seconds ≈ bytes × 8 / 48000). After every synthesis the Lambda writes a snapshot
to `tts/stats.json` next to index.html; the Hören panel reads that file (a plain S3 GET, no API
call) and shows totals plus this month's spend against the budget. `GET ?stats=1` returns the same
JSON from the API.

```sh
tools/tts-lambda/deploy.sh             # create or update everything with the AWS CLI (profile nursepal, us-east-2)
BUDGET_USD=5 QUOTA=2000 tools/tts-lambda/deploy.sh   # change the caps
tools/tts-lambda/deploy.sh --destroy   # remove the API, key, Lambda, role and table; cached MP3s stay
```

Endpoint and key are **not** in index.html. They are read from `tts.json` next to it (see
`tts.json.example`; `cache` is optional and lets a locally served copy read the S3 cache
directly). The key is only as secret as the site's unguessable S3 folder — that is the point:
the page is public, so the quota and budget are what actually protect the bill. `tts.json` is git-ignored — upload it to the S3 folder alongside index.html, and keep a
local one for http://127.0.0.1:8765. Without it the Polly options are disabled.

Polly list prices per million characters: standard 4 $, neural 16 $, generative 30 $. The whole
audio list is ~277k characters, so even reading every line once on generative stays under 9 $.
