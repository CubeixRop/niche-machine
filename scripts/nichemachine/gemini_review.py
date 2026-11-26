"""Optional Gemini visual review for locally detected gameplay moments.

Only short candidate excerpts are sent. Credentials may come from GEMINI_API_KEY
or an explicitly selected session key; quota errors stop the run without switching keys.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from typing import Callable

from .util import ffmpeg, say
from . import media

MAX_INLINE_REQUEST_BYTES = 20_000_000
# Leave room for base64 expansion, JSON framing, instructions, and transcript text.
MAX_INLINE_DATA_BYTES = (MAX_INLINE_REQUEST_BYTES - 1_000_000) * 3 // 4


def _transcript(segs: list[dict], start: float, end: float) -> str:
    return " ".join(s["text"] for s in segs if s["s"] < end and s["e"] > start)[:1800]


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Gemini returned no JSON object")
    return json.loads(text[start:end + 1])


def review(video: Path, out_dir: Path, candidates: list[dict], segs: list[dict], model: str | None = None,
           api_key: str | None = None, api_keys: list[dict] | None = None,
           on_request: Callable[[str, dict], None] | None = None) -> list[dict]:
    keys = [str(item.get("key", "")).strip() for item in (api_keys or []) if item.get("key")]
    if not keys and api_key:
        keys = [api_key.strip()]
    if not keys:
        env_key = os.environ.get("GEMINI_API_KEY", "").strip()
        keys = [env_key] if env_key else []
    if not keys:
        raise SystemExit("Gemini review needs an explicitly configured API key.")
    model = model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    source_duration = media.probe(video)["dur"]
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "gemini_review.json"
    st = video.stat()
    review_identity = {"video": str(video.resolve()), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                       "candidates": candidates, "transcript": segs}
    candidate_hash = hashlib.sha256(json.dumps(review_identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    existing = {}
    try:
        saved = json.loads(result_path.read_text(encoding="utf-8"))
        if saved.get("model") == model and saved.get("candidate_hash") == candidate_hash:
            existing = {int(r["candidate"]): r for r in saved.get("reviews", [])}
    except (OSError, ValueError, TypeError, KeyError):
        pass
    results = list(existing.values())
    # Partition candidates into one balanced, contiguous key chunk per selected key.
    # Oversized chunks are halved and retried on that same key to stay within inline limits.
    key_chunks = [(key_index, list(range(len(candidates) * key_index // len(keys),
                                         len(candidates) * (key_index + 1) // len(keys))))
                  for key_index in range(len(keys))]
    pending = deque((key_index, [i for i in ids if i not in existing])
                    for key_index, ids in key_chunks if any(i not in existing for i in ids))
    while pending:
        key_index, batch_ids = pending.popleft()
        if not batch_ids:
            continue
        parts = [{"text": (
            "Review these gameplay candidate video excerpts and transcript. "
            "For each id judge whether the excerpt is understandable with setup and payoff, or has a strong "
            "standalone funny, surprising, exciting, angry, or genuine shout/laugh reaction. Loudness alone "
            "is not a keep. Do not invent events. Return only JSON: {\"reviews\":[{\"id\":0,\"keep\":true,"
            "\"reason\":\"...\",\"context\":\"...\"}]}. "
            "Each video is a short continuous excerpt; timestamps in the text are source seconds."
        )}]
        total_bytes = 0
        oversized = False
        with tempfile.TemporaryDirectory(prefix="nm-gemini-") as tmp:
            for cid in batch_ids:
                c = candidates[cid]
                a, b = float(c["a"]), float(c["b"])
                excerpt = Path(tmp) / f"candidate-{cid}.mp4"
                duration = min(45.0, b - a, max(0.0, source_duration - a))
                if duration <= 0:
                    raise RuntimeError(f"Candidate at {a:.1f}s falls outside the source recording.")
                cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-i", str(video),
                       "-t", f"{duration:.3f}", "-vf", "scale=-2:480", "-r", "2", "-c:v", "libx264",
                       "-preset", "ultrafast", "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-movflags",
                       "+faststart", str(excerpt)]
                p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
                if p.returncode or not excerpt.exists():
                    raise RuntimeError(f"Could not prepare Gemini excerpt at {a:.1f}s: {p.stderr[-1000:]}")
                blob = excerpt.read_bytes()
                if len(blob) > MAX_INLINE_DATA_BYTES or total_bytes + len(blob) > MAX_INLINE_DATA_BYTES:
                    oversized = True
                    break
                total_bytes += len(blob)
                parts.append({"text": f"\nCandidate id={cid}, source={a:.2f}-{a+duration:.2f}s, peak={c['peak']:.2f}s. Transcript: {_transcript(segs, a, b) or '[no speech recognized]'}"})
                parts.append({"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(blob).decode("ascii")}})
        if oversized:
            if len(batch_ids) == 1:
                raise RuntimeError("A Gemini excerpt exceeds the safe 20 MB request limit; reduce the clip duration or resolution.")
            midpoint = len(batch_ids) // 2
            pending.appendleft((key_index, batch_ids[midpoint:]))
            pending.appendleft((key_index, batch_ids[:midpoint]))
            continue
        body = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json"}}).encode()
        req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": keys[key_index]}, method="POST")
        request_started = time.monotonic()
        request_status = "failed"
        http_status = None
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                http_status = response.status
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            request_status = f"HTTP {exc.code}"
            details = exc.read().decode("utf-8", "replace")[:1500]
            if exc.code == 429:
                raise RuntimeError("Gemini quota/rate limit reached (429). Scan stopped; wait for reset or use an authorized paid tier. Rotating keys to bypass quota is disabled. " + details) from exc
            raise RuntimeError(f"Gemini API returned HTTP {exc.code}: {details}") from exc
        finally:
            if http_status is not None and 200 <= http_status < 300:
                request_status = "success"
            if on_request and api_keys and key_index < len(api_keys):
                selected_key = api_keys[key_index]
                try:
                    on_request(str(selected_key.get("id", "")), {
                        "key_name": str(selected_key.get("name", "Gemini key")),
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "model": model, "candidate_count": len(batch_ids),
                        "candidate_ids": batch_ids, "status": request_status,
                        "duration_ms": round((time.monotonic() - request_started) * 1000)})
                except Exception as exc:
                    say(f"  Could not save Gemini request history: {exc}")
        try:
            answer = payload["candidates"][0]["content"]["parts"][0]["text"]
            obj = _parse_json(answer)
            items = obj.get("reviews")
            if not isinstance(items, list):
                raise ValueError("reviews is not a JSON list")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("review entry is not an object")
                if not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool):
                    raise ValueError("candidate id is not an integer")
                idx = item["id"]
                if idx not in batch_ids:
                    raise ValueError(f"Gemini returned unexpected candidate id {idx}")
                if not isinstance(item.get("keep"), bool):
                    raise ValueError(f"Gemini returned a non-boolean keep value for candidate {idx}")
                if idx not in existing:
                    result = {"candidate": idx, "keep": bool(item.get("keep")),
                              "reason": str(item.get("reason", ""))[:600],
                              "context": str(item.get("context", ""))[:600]}
                    results.append(result)
                    existing[idx] = result
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Could not parse Gemini's candidate review: {exc}") from exc
        tmp = result_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"model": model, "candidate_hash": candidate_hash,
                                   "reviews": sorted(results, key=lambda r: r["candidate"])}, indent=2), encoding="utf-8")
        tmp.replace(result_path)
        say(f"  Gemini reviewed {len(existing)}/{len(candidates)} candidate moments")
        missing = set(batch_ids) - set(existing)
        if missing:
            raise RuntimeError(f"Gemini omitted review results for candidate id(s): {', '.join(map(str, sorted(missing)))}. Completed reviews were saved and can resume.")
    return results
