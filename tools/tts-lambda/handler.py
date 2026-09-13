"""heft-tts — on-demand German speech for the Deutsch-Heft.

Behind an API Gateway REST API (x-api-key required, usage plan with throttle + monthly quota).
POST {"text": "...", "engine": "neural", "voice": "Vicki"}  (or the same as GET query params)
  → audio/mpeg. The MP3 is cached in S3 under <PREFIX>/tts/<engine>/<voice>/<sha256>.mp3, so
    every sentence is paid for once; the page tries that S3 file first and only calls here on a miss.
GET ?stats=1 → usage as JSON (this month + all time). The same snapshot is written to
<PREFIX>/tts/stats.json after every synthesis, so the page can show it without an API call.

Guards: text ≤ MAX_CHARS, an allow-list of engines/voices, an Origin/Referer allow-list, and a
monthly Polly budget kept as an atomic counter in DynamoDB (MONTHLY_BUDGET_USD). When the budget
is reached the function answers 429 and the page falls back to its built-in Piper clips.
"""
import base64
import hashlib
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET"]
PREFIX = os.environ.get("PREFIX", "").strip("/")
TABLE = os.environ.get("USAGE_TABLE", "")
BUDGET_USD = float(os.environ.get("MONTHLY_BUDGET_USD", "8"))
MAX_CHARS = int(os.environ.get("MAX_CHARS", "300"))
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
POLLY_REGION = os.environ.get("POLLY_REGION", "us-east-1")

VOICES = {
    "standard": ["Vicki", "Marlene", "Hans"],
    "neural": ["Vicki", "Daniel"],
    "generative": ["Vicki", "Daniel", "Lennart"],
}
RATE_MICRO = {"standard": 4, "neural": 16, "generative": 30}  # micro-USD per character (list price)
BITRATE = 48000  # Polly MP3 is 48 kbps CBR, so seconds ≈ bytes * 8 / 48000
TOTAL_ROW = "total"  # the all-time row in the usage table (the others are keyed by YYYY-MM)

polly = boto3.client("polly", region_name=POLLY_REGION)
s3 = boto3.client("s3")
ddb = boto3.client("dynamodb") if TABLE else None


class BudgetReached(Exception):
    pass


def norm(t):
    t = unicodedata.normalize("NFC", str(t)).replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def s3_key(rel):
    return f"{PREFIX + '/' if PREFIX else ''}tts/{rel}"


def cache_key(engine, voice, text):
    h = hashlib.sha256(f"{engine}|{voice}|{text}".encode("utf-8")).hexdigest()
    return s3_key(f"{engine}/{voice}/{h}.mp3")


def month():
    return time.strftime("%Y-%m", time.gmtime())


def origin_ok(headers):
    if not ALLOWED_ORIGINS:
        return True
    o = headers.get("origin") or ""
    if not o:
        m = re.match(r"^(https?://[^/]+)", headers.get("referer") or "")
        o = m.group(1) if m else ""
    return o in ALLOWED_ORIGINS


def _add(row, fields, condition=None, extra_values=None):
    """ADD numeric fields to one row of the usage table; returns the row's numbers afterwards."""
    names = {f"#f{i}": k for i, k in enumerate(fields)}
    values = {f":v{i}": {"N": str(v)} for i, v in enumerate(fields.values())}
    values.update(extra_values or {})
    kw = dict(TableName=TABLE, Key={"month": {"S": row}},
              UpdateExpression="ADD " + ", ".join(f"#f{i} :v{i}" for i in range(len(fields))),
              ExpressionAttributeNames=names, ExpressionAttributeValues=values, ReturnValues="ALL_NEW")
    if condition:
        kw["ConditionExpression"] = condition
    return _nums(ddb.update_item(**kw)["Attributes"])


def _nums(item):
    return {k: int(float(v["N"])) for k, v in (item or {}).items() if "N" in v}


def _get(row):
    return _nums(ddb.get_item(TableName=TABLE, Key={"month": {"S": row}}).get("Item"))


def charge(engine, chars):
    """Add this request to the month's counter; refuse if it would exceed the budget. Returns month USD."""
    if not ddb:
        return None
    cost = chars * RATE_MICRO[engine]
    cap = int(BUDGET_USD * 1_000_000)
    try:
        row = _add(month(), {"micro": cost, "chars": chars, "chars_" + engine: chars, "requests": 1},
                   condition="attribute_not_exists(micro) OR micro <= :room",
                   extra_values={":room": {"N": str(cap - cost)}})
        return row["micro"] / 1e6
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise BudgetReached()
        raise


def record(engine, chars, nbytes):
    """After a stored synthesis: length/bytes on the month row, everything on the all-time row,
    then a fresh tts/stats.json in S3 for the page."""
    if not ddb:
        return
    ms = int(nbytes * 8 * 1000 / BITRATE)
    m = _add(month(), {"clips": 1, "ms": ms, "bytes": nbytes})
    t = _add(TOTAL_ROW, {"micro": chars * RATE_MICRO[engine], "chars": chars, "chars_" + engine: chars,
                         "requests": 1, "clips": 1, "ms": ms, "bytes": nbytes})
    s3.put_object(Bucket=BUCKET, Key=s3_key("stats.json"), ContentType="application/json; charset=utf-8",
                  CacheControl="no-store", Body=json.dumps(snapshot(m, t), ensure_ascii=False).encode("utf-8"))


def count_hit():
    if not ddb:
        return
    try:
        _add(month(), {"hits": 1})
    except ClientError:
        pass


def view(row):
    n = lambda k: row.get(k, 0)
    out = {"usd": round(n("micro") / 1e6, 6), "chars": n("chars"), "clips": n("clips"),
           "seconds": round(n("ms") / 1000, 1), "bytes": n("bytes"), "requests": n("requests"), "hits": n("hits")}
    for e in VOICES:
        out["chars_" + e] = n("chars_" + e)
    return out


def snapshot(month_row, total_row):
    return {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "budget_usd": BUDGET_USD,
            "month": dict(view(month_row), month=month()), "total": view(total_row)}


def stats():
    if not ddb:
        return snapshot({}, {})
    return snapshot(_get(month()), _get(TOTAL_ROW))


def cors(headers):
    """REST API Gateway adds no CORS headers itself, so every answer (preflight included) carries them."""
    o = headers.get("origin") or ""
    allow = o if o and (not ALLOWED_ORIGINS or o in ALLOWED_ORIGINS) else (ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else "*")
    return {
        "access-control-allow-origin": allow,
        "vary": "Origin",
        "access-control-allow-methods": "GET,POST,OPTIONS",
        "access-control-allow-headers": "content-type,x-api-key",
        "access-control-expose-headers": "x-heft-cache,x-heft-chars,x-heft-month-usd",
        "access-control-max-age": "86400",
    }


def reply(status, body, c):
    h = {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}
    h.update(c)
    return {"statusCode": status, "headers": h, "body": json.dumps(body, ensure_ascii=False)}


def audio(data, hit, chars, month_usd, c):
    h = dict(c)
    h.update({
            "content-type": "audio/mpeg",
            "cache-control": "public, max-age=31536000, immutable",
            "x-heft-cache": "hit" if hit else "miss",
            "x-heft-chars": str(chars),
            "x-heft-month-usd": "" if month_usd is None else f"{month_usd:.4f}",
    })
    return {"statusCode": 200, "isBase64Encoded": True, "headers": h,
            "body": base64.b64encode(data).decode("ascii")}


def handler(event, _ctx):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    method = event.get("httpMethod") or (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    c = cors(headers)
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": c, "body": ""}
    if not origin_ok(headers):
        return reply(403, {"error": "origin not allowed"}, c)

    if method == "POST":
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8", "replace")
        try:
            params = json.loads(body or "{}")
        except ValueError:
            return reply(400, {"error": "body must be JSON"}, c)
    else:
        params = event.get("queryStringParameters") or {}
    if not isinstance(params, dict):
        return reply(400, {"error": "bad request"}, c)

    if params.get("stats"):
        return reply(200, stats(), c)

    text = norm(params.get("text", ""))
    engine = str(params.get("engine", "neural")).lower()
    if engine not in VOICES:
        return reply(400, {"error": "engine must be one of " + ", ".join(VOICES)}, c)
    voice = str(params.get("voice", "")).strip() or VOICES[engine][0]
    if voice not in VOICES[engine]:
        return reply(400, {"error": f"voice {voice} is not available on {engine}"}, c)
    if not text or len(text) > MAX_CHARS:
        return reply(400, {"error": f"text must be 1 to {MAX_CHARS} characters"}, c)

    key = cache_key(engine, voice, text)
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        data = obj["Body"].read()
        count_hit()
        return audio(data, True, 0, None, c)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
            raise

    try:
        month_usd = charge(engine, len(text))
    except BudgetReached:
        return reply(429, {"error": "monthly budget reached", "budget_usd": BUDGET_USD}, c)

    r = polly.synthesize_speech(Engine=engine, VoiceId=voice, LanguageCode="de-DE",
                                OutputFormat="mp3", Text=text)
    data = r["AudioStream"].read()
    s3.put_object(
        Bucket=BUCKET, Key=key, Body=data, ContentType="audio/mpeg",
        CacheControl="public, max-age=31536000, immutable",
        Metadata={"text": quote(text), "engine": engine, "voice": voice},
    )
    record(engine, len(text), len(data))
    return audio(data, False, len(text), month_usd, c)
