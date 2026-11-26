"""Local-first Windows dashboard for Niche Machine scans.

The server binds only to loopback. Windows stores Gemini secrets in Credential
Manager; only non-secret profile labels and ids go in the app manifest. Runtime
copies are cleared when the app exits.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import find, key_store, listen
from .util import APP, PROJECTS, SHORTS, VIDEO_EXT, read_json, slug, today, write_json

_lock = threading.Lock()
_key_lock = threading.RLock()
_job = {"status": "idle", "message": "Ready", "project": None, "started": None, "error": None}
_config = {"keys": {}, "active_key_id": None, "model": "gemini-3.8-flash"}
_key_store_error = None
_KEY_ACTIVITY = APP / "gemini-key-requests.json"
_static = Path(__file__).with_name("app")
_UPLOADS = Path(os.environ.get("NICHE_WORK", Path.home() / "NicheMachine")) / "uploads"
_MAX_UPLOAD = 50 * 1024 * 1024 * 1024
_STREAM_DOMAINS = ("youtube.com", "youtu.be", "tiktok.com", "twitch.tv", "kick.com")


class DuplicateKeyError(ValueError):
    pass


def _restore_saved_keys() -> None:
    global _key_store_error
    if not key_store.persistent_supported():
        return
    try:
        profiles, secrets_by_id = key_store.load_profiles()
        for profile in profiles:
            kid = str(profile.get("id", ""))
            secret = secrets_by_id.get(kid)
            if kid and secret:
                _config["keys"][kid] = {"name": str(profile.get("name", kid)),
                                        "tier": profile.get("tier", "free"), "key": secret}
        if _config["keys"]:
            _config["active_key_id"] = next(iter(_config["keys"]))
    except (OSError, RuntimeError) as exc:
        _key_store_error = str(exc)


def _save_key_manifest() -> None:
    profiles = [{"id": kid, "name": item["name"], "tier": item["tier"]}
                for kid, item in _config["keys"].items()]
    key_store.save_profiles(profiles)


def _parse_key_block(text: str) -> tuple[list[tuple[str, str]], list[int]]:
    parsed: list[tuple[str, str]] = []
    errors: list[int] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^export\s+", "", line, flags=re.I)
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not match or not re.fullmatch(r"GEMINI_(?:KEY(?:_[A-Za-z0-9_-]+)?|API_KEY)", match.group(1), re.I):
            errors.append(number)
            continue
        name, value = match.group(1), match.group(2).strip()
        # Accept shell-style trailing continuation slashes from pasted env blocks.
        value = value.rstrip().rstrip("\\").rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1].strip()
        if not value or len(value) > 2560 or any(ch.isspace() for ch in value) or name.casefold() in seen:
            errors.append(number)
            continue
        seen.add(name.casefold())
        parsed.append((name, value))
    return parsed, errors


def _store_key(name: str, value: str, tier: str) -> str:
    with _key_lock:
        if any(hmac.compare_digest(value, item["key"]) for item in _config["keys"].values()):
            raise DuplicateKeyError("This Gemini key is already configured in Niche Machine.")
        for env_name, env_value in os.environ.items():
            if re.fullmatch(r"GEMINI_(?:API_KEY|KEY(?:_\d+)?)", env_name, re.I) and hmac.compare_digest(value, env_value.strip()):
                raise DuplicateKeyError("This Gemini key already exists in the process environment.")
        if key_store.persistent_supported() and key_store.contains_secret(value):
            raise DuplicateKeyError("This Gemini key already exists in Windows Credential Manager.")
        existing_id = next((kid for kid, item in _config["keys"].items()
                            if item["name"].casefold() == name.casefold()), None)
        kid = existing_id or secrets.token_urlsafe(9)
        previous = _config["keys"].get(kid)
        if key_store.persistent_supported():
            key_store.save_secret(kid, value)
        _config["keys"][kid] = {"name": name, "key": value, "tier": tier}
        try:
            if key_store.persistent_supported():
                _save_key_manifest()
        except Exception:
            if previous is None:
                _config["keys"].pop(kid, None)
            else:
                _config["keys"][kid] = previous
            if key_store.persistent_supported():
                if previous is None:
                    key_store.delete_secret(kid)
                else:
                    key_store.save_secret(kid, previous["key"])
            raise
        if not _config["active_key_id"] or _config["active_key_id"] not in _config["keys"]:
            _config["active_key_id"] = kid
        return kid


_restore_saved_keys()


def _snapshot() -> dict:
    with _lock:
        out = dict(_job)
    # The dashboard is served by a threaded HTTP server. Take a stable copy
    # before iterating key profiles while another request may add/remove one.
    with _key_lock:
        key_profiles = [(kid, dict(item)) for kid, item in _config["keys"].items()]
        active_key_id = _config["active_key_id"]
        model = _config["model"]
    activity = read_json(_KEY_ACTIVITY, {})
    if not isinstance(activity, dict):
        activity = {}
    out["gemini_keys"] = []
    for kid, item in key_profiles:
        usage = activity.get(kid, {})
        if not isinstance(usage, dict):
            usage = {}
        requests = usage.get("requests", [])
        out["gemini_keys"].append({"id": kid, "name": item["name"], "tier": item["tier"],
                                   "request_count": int(usage.get("request_count", 0)),
                                   "last_request_at": usage.get("last_request_at"),
                                   "requests": requests if isinstance(requests, list) else []})
    out["active_key_id"] = active_key_id
    out["gemini_configured"] = active_key_id in dict(key_profiles)
    out["persistent_key_storage"] = key_store.persistent_supported()
    out["key_store_error"] = _key_store_error
    out["model"] = model
    out["projects"] = []
    if PROJECTS.exists():
        for p in sorted(PROJECTS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            scan = read_json(p / "scan" / "scan.json", {})
            reviews = read_json(p / "scan" / "gemini_review.json", {}).get("reviews", [])
            candidates = read_json(p / "scan" / "candidates.json", [])
            by_candidate = {r.get("candidate"): r for r in reviews if isinstance(r, dict)}
            selected_candidates = read_json(p / "scan" / "selected_candidates.json", [])
            selected_set = set(selected_candidates if isinstance(selected_candidates, list) else [])
            out["projects"].append({"name": p.name, "duration": scan.get("duration", 0),
                                     "candidates": len(candidates), "reviewed": len(reviews),
                                     "moments": [{"index": i, "peak": c.get("peak"), "a": c.get("a"), "b": c.get("b"),
                                                  "duration": c.get("duration", max(0, float(c.get("b", 0)) - float(c.get("a", 0)))),
                                                  "score": c.get("hype", 0), "selected": i in selected_set,
                                                  "keep": by_candidate.get(i, {}).get("keep"),
                                                  "reason": by_candidate.get(i, {}).get("reason", "")}
                                                 for i, c in enumerate(candidates)],
                                     "path": str(p)})
    out["exports"] = []
    if SHORTS.exists():
        media = sorted((p for p in SHORTS.rglob("*.mp4") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        out["exports"] = [{"name": p.name, "path": str(p), "size": p.stat().st_size,
                           "modified": p.stat().st_mtime} for p in media]
    return out


def _record_gemini_request(key_id: str, event: dict) -> None:
    """Persist request metadata only; never write API key values to disk."""
    if not key_id:
        return
    with _key_lock:
        activity = read_json(_KEY_ACTIVITY, {})
        if not isinstance(activity, dict):
            activity = {}
        entry = activity.get(key_id, {})
        if not isinstance(entry, dict):
            entry = {}
        requests = entry.get("requests", [])
        if not isinstance(requests, list):
            requests = []
        requests.append({"at": str(event.get("at", "")), "model": str(event.get("model", "")),
                         "candidate_count": int(event.get("candidate_count", 0)),
                         "candidate_ids": [int(i) for i in event.get("candidate_ids", [])],
                         "status": str(event.get("status", "unknown")),
                         "duration_ms": int(event.get("duration_ms", 0))})
        entry.update(key_name=str(event.get("key_name", entry.get("key_name", key_id))),
                     request_count=int(entry.get("request_count", 0)) + 1,
                     last_request_at=str(event.get("at", "")), requests=requests[-200:])
        activity[key_id] = entry
        write_json(_KEY_ACTIVITY, activity)


def _incomplete_review(video: Path) -> tuple[Path | None, dict]:
    """Find a checkpoint for this exact recording, returning project and key fingerprint."""
    if not PROJECTS.exists():
        return None, {}
    wanted = str(video.resolve()).casefold()
    for project in sorted((p for p in PROJECTS.iterdir() if p.is_dir()),
                          key=lambda p: p.stat().st_mtime, reverse=True):
        plan = read_json(project / "plan.json", {})
        if str(plan.get("file", "")).casefold() != wanted:
            continue
        scan_dir = project / "scan"
        candidates = read_json(scan_dir / "candidates.json", [])
        if not candidates:
            continue
        meta = read_json(scan_dir / "gemini_session.json", {})
        if not meta.get("key_fingerprint"):
            continue
        reviews = read_json(scan_dir / "gemini_review.json", {}).get("reviews", [])
        if len(reviews) < len(candidates):
            return project, meta
    return None, {}


def _validate_stream_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("https", "http") or not host or parsed.username or parsed.password:
        raise ValueError("Enter a full public stream, replay or video URL")
    if not any(host == domain or host.endswith("." + domain) for domain in _STREAM_DOMAINS):
        raise ValueError("Supported URLs are YouTube, TikTok, Twitch and Kick")
    return parsed.geturl()


def _download_stream(url: str) -> Path:
    """Download a public VOD or record a live stream with yt-dlp before scanning."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("Stream support is missing. Run nm setup to install yt-dlp, then reopen the app.") from exc
    folder = _UPLOADS / ("stream-" + secrets.token_hex(6))
    folder.mkdir(parents=True, exist_ok=False)
    opts = {"outtmpl": str(folder / "%(id)s.%(ext)s"), "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4", "noplaylist": True, "quiet": True, "no_warnings": True,
            "restrictfilenames": True}
    with _lock:
        _job.update(status="downloading", message="Downloading the public video or recording the live stream…")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("The source did not return a downloadable video")
            video_id = str(info.get("id", ""))
        choices = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
        if video_id:
            choices.sort(key=lambda p: (p.stem != video_id, p.stat().st_size == 0))
        video = next((p for p in choices if p.stat().st_size > 0), None)
        if video is None:
            raise RuntimeError("The source finished without producing a supported video file")
        return video
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _caption_transcript(url: str, out_dir: Path) -> dict | None:
    """Use published captions first; fall back to local Whisper when none are available."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" or host.endswith("youtube.com"):
        video_id = parsed.path.strip("/").split("/")[-1] if host == "youtu.be" else parse_qs(parsed.query).get("v", [""])[0]
        if video_id:
            try:
                from youtube_transcript_api import YouTubeTranscriptApi
                fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en"])
                snippets = fetched.to_raw_data()
                return {"transcription": [{"offsets": {"from": int(s["start"] * 1000),
                                                               "to": int((s["start"] + s.get("duration", 1)) * 1000)},
                                           "text": s["text"]} for s in snippets]}
            except Exception:
                pass
    try:
        import yt_dlp
        target = out_dir / "source-captions"
        target.mkdir(parents=True, exist_ok=True)
        opts = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                "subtitleslangs": ["en.*", "en"], "subtitlesformat": "vtt/best",
                "outtmpl": str(target / "%(id)s.%(ext)s"), "noplaylist": True,
                "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        vtt_files = sorted(target.glob("*.vtt"), key=lambda p: ("en" not in p.stem.lower(), p.name))
        for vtt in vtt_files:
            rows = []
            for block in re.split(r"\n\s*\n", vtt.read_text(encoding="utf-8-sig", errors="replace")):
                lines = [line.strip() for line in block.splitlines() if line.strip()]
                timing = next((line for line in lines if "-->" in line), None)
                if not timing:
                    continue
                m = re.search(r"(\d{2}:\d{2}:\d{2}[.,]\d+)\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d+)", timing)
                if not m:
                    continue
                def seconds(stamp):
                    hh, mm, ss = stamp.replace(",", ".").split(":")
                    return float(hh) * 3600 + float(mm) * 60 + float(ss)
                text = re.sub(r"<[^>]+>", "", " ".join(lines[lines.index(timing) + 1:])).strip()
                if text:
                    rows.append({"offsets": {"from": int(seconds(m.group(1)) * 1000),
                                              "to": int(seconds(m.group(2)) * 1000)}, "text": text})
            if rows:
                return {"transcription": rows}
    except Exception:
        pass
    return None


def _scan_job(video: Path | None, source_url: str | None, use_gemini: bool, model: str, key_entries: list[dict], max_candidates: int | None,
              clip_min_seconds: int, clip_max_seconds: int,
              analyze_audio: bool, analyze_transcript: bool,
              resume_project: Path | None = None) -> None:
    if source_url:
        try:
            video = _download_stream(source_url)
        except Exception as exc:
            with _lock:
                _job.update(status="failed", message="Could not retrieve the stream", error=str(exc), finished=time.time())
            with _lock:
                _job["started"] = None
            return
    if video is None:
        with _lock:
            _job.update(status="failed", message="No video source was provided", error="Choose a local video or enter a supported URL", finished=time.time(), started=None)
        return
    project = resume_project or PROJECTS / f"{today()}_moments_{slug(video.stem, 36)}"
    suffix = 2
    while resume_project is None and project.exists():
        project = PROJECTS / f"{today()}_moments_{slug(video.stem, 30)}-{suffix}"
        suffix += 1
    scan_dir = project / "scan"
    try:
        if resume_project is None:
            project.mkdir(parents=True)
            write_json(project / "plan.json", {"mode": "moments", "name": video.stem, "file": str(video),
                                                "layout": "raw", "context_required": False,
                                                "candidate_selection_enabled": True})
        with _lock:
            _job.update(status="scanning", message="Finding candidate moments locally…", project=str(project), error=None)
        caption_data = _caption_transcript(source_url, scan_dir) if source_url and analyze_transcript else None
        info = find.scan(video, scan_dir, max_candidates=max_candidates,
                         clip_min_seconds=clip_min_seconds, clip_max_seconds=clip_max_seconds,
                         analyze_audio=analyze_audio, analyze_transcript=analyze_transcript,
                         transcript_data=caption_data)
        if use_gemini:
            key_fingerprints = [hashlib.sha256(entry["key"].encode("utf-8")).hexdigest()
                                for entry in key_entries]
            with _lock:
                _job.update(status="reviewing", message=f"Gemini is reviewing one balanced chunk per key ({len(key_entries)} key(s))…")
            tr = read_json(scan_dir / "transcript.json", {})
            write_json(scan_dir / "gemini_session.json", {
                "key_fingerprint": key_fingerprints[0], "key_fingerprints": key_fingerprints, "model": model,
                "candidate_mode": "all" if max_candidates is None else "top50",
                "clip_min_seconds": clip_min_seconds, "clip_max_seconds": clip_max_seconds})
            from . import gemini_review
            reviews = gemini_review.review(video, scan_dir, read_json(scan_dir / "candidates.json", []),
                                           listen.segments(tr), model, api_keys=key_entries,
                                           on_request=_record_gemini_request)
            info["gemini_reviewed"] = len(reviews)
        with _lock:
            _job.update(status="complete", message=f"Scan complete · {info.get('candidates', 0)} candidates",
                        finished=time.time(), error=None)
    except (Exception, SystemExit) as exc:
        with _lock:
            _job.update(status="failed", message="Scan stopped", error=str(exc) or "Worker exited unexpectedly", finished=time.time())
    finally:
        with _lock:
            _job["started"] = None


class Handler(BaseHTTPRequestHandler):
    server_version = "NicheMachineLocal/0.1"

    def log_message(self, fmt, *args):
        return

    def _local_request(self) -> bool:
        expected_host = f"127.0.0.1:{self.server.server_address[1]}"
        if self.headers.get("Host", "").lower() != expected_host:
            self.send_error(403)
            return False
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_address[1]}"
        if origin and origin != expected:
            self.send_error(403)
            return False
        return True

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        if int(self.headers.get("Content-Length", "0")) > 20_000:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0))

    def _candidate_source(self, project_name: str, candidate_index: int) -> tuple[Path, dict] | None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", project_name) or project_name in (".", ".."):
            return None
        project = (PROJECTS / project_name).resolve()
        if project.parent != PROJECTS.resolve() or not project.is_dir():
            return None
        candidates = read_json(project / "scan" / "candidates.json", [])
        if not isinstance(candidates, list) or not 0 <= candidate_index < len(candidates):
            return None
        plan = read_json(project / "plan.json", {})
        video = Path(os.path.expandvars(os.path.expanduser(str(plan.get("file", ""))))).resolve()
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXT:
            return None
        return video, candidates[candidate_index]

    def _serve_candidate_video(self, project_name: str, candidate_index: int) -> None:
        item = self._candidate_source(project_name, candidate_index)
        if item is None:
            return self.send_error(404)
        video, _candidate = item
        size = video.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range", "")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or size <= 0:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            left, right = match.groups()
            if not left:
                length = int(right or 0)
                if length <= 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                start, end = max(0, size - length), size - 1
            else:
                start = int(left)
                end = min(int(right) if right else size - 1, size - 1)
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(video.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "private, max-age=3600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            with video.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self):
        if not self._local_request():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            return self._json(_snapshot())
        if path == "/api/candidate-video":
            query = parse_qs(parsed.query)
            try:
                project_name = str(query.get("project", [""])[0])
                candidate_index = int(query.get("candidate", [""])[0])
            except (ValueError, TypeError):
                return self.send_error(400)
            return self._serve_candidate_video(project_name, candidate_index)
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        target = (_static / name).resolve()
        if _static.resolve() not in target.parents or not target.is_file():
            return self.send_error(404)
        raw = target.read_bytes()
        ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        global _job
        if not self._local_request():
            return
        try:
            if urlparse(self.path).path == "/api/upload":
                length = int(self.headers.get("Content-Length", "0"))
                filename = Path(unquote(self.headers.get("X-File-Name", ""))).name
                suffix = Path(filename).suffix.lower()
                if not filename or suffix not in VIDEO_EXT:
                    return self._json({"error": "Drop a supported video file (MP4, MOV, MKV, WebM, M4V, AVI or TS)"}, 400)
                if length <= 0 or length > _MAX_UPLOAD:
                    return self._json({"error": "The video must be between 1 byte and 50 GB"}, 413)
                _UPLOADS.mkdir(parents=True, exist_ok=True)
                destination = _UPLOADS / f"{secrets.token_hex(8)}-{filename}"
                remaining = length
                try:
                    with destination.open("wb") as target:
                        while remaining:
                            block = self.rfile.read(min(1024 * 1024, remaining))
                            if not block:
                                raise ValueError("The upload ended before the full video arrived")
                            target.write(block)
                            remaining -= len(block)
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                return self._json({"path": str(destination), "name": filename, "size": length})
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"error": "Request body must be a JSON object"}, 400)
            if urlparse(self.path).path == "/api/pick-file":
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                chosen = filedialog.askopenfilename(title="Choose a gameplay recording", filetypes=[
                    ("Video recordings", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi *.ts"), ("All files", "*.*")])
                root.destroy()
                return self._json({"path": chosen})
            if urlparse(self.path).path == "/api/config":
                model = str(body.get("model", "gemini-3.8-flash")).strip()
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
                    return self._json({"error": "Model names may only contain letters, numbers, dots, underscores and dashes"}, 400)
                with _key_lock:
                    _config["model"] = model
                return self._json({"ok": True, "model": model})
            if urlparse(self.path).path == "/api/candidate-selection":
                project_name = str(body.get("project", ""))
                selected = body.get("selected")
                if not isinstance(selected, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in selected):
                    return self._json({"error": "Choose valid clip selections"}, 400)
                project = (PROJECTS / project_name).resolve() if re.fullmatch(r"[A-Za-z0-9._-]{1,160}", project_name) else None
                if project is None or project.parent != PROJECTS.resolve() or not project.is_dir():
                    return self._json({"error": "Scan not found"}, 404)
                candidates = read_json(project / "scan" / "candidates.json", [])
                clean = sorted(set(selected))
                if not isinstance(candidates, list) or any(i < 0 or i >= len(candidates) for i in clean):
                    return self._json({"error": "A selected clip is no longer available"}, 400)
                write_json(project / "scan" / "selected_candidates.json", clean)
                return self._json({"ok": True, "selected": len(clean)})
            if urlparse(self.path).path == "/api/keys":
                action = body.get("action")
                if action == "add":
                    if _key_store_error:
                        return self._json({"error": "Saved key profile metadata could not be loaded; existing vault entries were left untouched."}, 500)
                    name = str(body.get("name", "")).strip()
                    key = str(body.get("key", "")).strip()
                    tier = body.get("tier", "free")
                    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,32}", name):
                        return self._json({"error": "Key name must be 1–32 letters, numbers, spaces, dots, dashes or underscores"}, 400)
                    if not key or len(key) > 500 or any(ch.isspace() for ch in key):
                        return self._json({"error": "Enter one valid API key without whitespace"}, 400)
                    if tier not in ("free", "paid"):
                        return self._json({"error": "Choose the project's billing type"}, 400)
                    kid = _store_key(name, key, tier)
                    return self._json({"ok": True, "id": kid})
                if action == "select":
                    kid = str(body.get("id", ""))
                    with _key_lock:
                        if kid not in _config["keys"]:
                            return self._json({"error": "Choose a key that is currently configured"}, 404)
                        _config["active_key_id"] = kid
                    return self._json({"ok": True})
                if action == "remove":
                    kid = str(body.get("id", ""))
                    with _key_lock:
                        if kid not in _config["keys"]:
                            return self._json({"error": "Key not found"}, 404)
                        try:
                            if key_store.persistent_supported():
                                key_store.delete_secret(kid)
                            removed = _config["keys"].pop(kid)
                            if key_store.persistent_supported():
                                _save_key_manifest()
                        except Exception:
                            if "removed" in locals():
                                _config["keys"][kid] = removed
                                if key_store.persistent_supported():
                                    key_store.save_secret(kid, removed["key"])
                            return self._json({"error": "Could not remove the saved credential; the key remains configured"}, 500)
                        if _config["active_key_id"] == kid:
                            _config["active_key_id"] = None
                    return self._json({"ok": True})
                return self._json({"error": "Choose add, select or remove"}, 400)
            if urlparse(self.path).path == "/api/keys/import":
                if _key_store_error:
                    return self._json({"error": "Saved key profile metadata could not be loaded; existing vault entries were left untouched."}, 500)
                text = str(body.get("text", ""))
                tier = body.get("tier", "free")
                if tier not in ("free", "paid"):
                    return self._json({"error": "Choose the project's billing type"}, 400)
                parsed, invalid_lines = _parse_key_block(text)
                if not parsed:
                    return self._json({"error": "No valid GEMINI_KEY_n=... lines found", "invalid_lines": invalid_lines}, 400)
                failed_names, duplicate_names = [], []
                for name, value in parsed:
                    try:
                        _store_key(name, value, tier)
                    except DuplicateKeyError:
                        duplicate_names.append(name)
                    except Exception:
                        failed_names.append(name)
                return self._json({"ok": not failed_names, "saved_count": len(parsed) - len(failed_names) - len(duplicate_names),
                                   "invalid_lines": invalid_lines, "failed_names": failed_names,
                                   "duplicate_names": duplicate_names,
                                   "persistent": key_store.persistent_supported()})
            if urlparse(self.path).path == "/api/open-exports":
                SHORTS.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(SHORTS))
                else:
                    webbrowser.open(SHORTS.as_uri())
                return self._json({"ok": True})
            if urlparse(self.path).path == "/api/scan":
                source = str(body.get("video", "")).strip()
                parsed = urlparse(source)
                is_windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", source))
                if parsed.scheme and parsed.scheme.lower() not in ("http", "https") and not is_windows_path:
                    return self._json({"error": "Use a local video path or a YouTube, TikTok, Twitch or Kick URL"}, 400)
                source_url = _validate_stream_url(source) if parsed.scheme.lower() in ("http", "https") else None
                video = None if source_url else Path(os.path.expandvars(os.path.expanduser(source))).resolve()
                if video is not None and (not video.is_file() or video.suffix.lower() not in VIDEO_EXT):
                    return self._json({"error": "Drop a local video or enter a supported YouTube, TikTok, Twitch or Kick URL"}, 400)
                if "gemini" in body and not isinstance(body["gemini"], bool):
                    return self._json({"error": "Gemini option must be true or false"}, 400)
                use_gemini = body.get("gemini", False)
                analyze_audio = body.get("analyze_audio", True)
                analyze_transcript = body.get("analyze_transcript", True)
                if not isinstance(analyze_audio, bool) or not isinstance(analyze_transcript, bool):
                    return self._json({"error": "Audio and transcript options must be true or false"}, 400)
                if not analyze_audio and not analyze_transcript:
                    return self._json({"error": "Select audio analysis, transcript analysis, or both"}, 400)
                candidate_mode = body.get("candidate_mode", "all")
                if candidate_mode not in ("top50", "all"):
                    return self._json({"error": "Choose top 50 or all detected moments"}, 400)
                max_candidates = 50 if candidate_mode == "top50" else None
                clip_min_raw = body.get("clip_min_seconds", 15)
                clip_max_raw = body.get("clip_max_seconds", 45)
                if any(isinstance(value, bool) or not isinstance(value, int)
                       for value in (clip_min_raw, clip_max_raw)):
                    return self._json({"error": "Clip lengths must be whole seconds"}, 400)
                clip_min_seconds, clip_max_seconds = clip_min_raw, clip_max_raw
                if not 5 <= clip_min_seconds <= clip_max_seconds <= 45:
                    return self._json({"error": "Choose clip lengths from 5 to 45 seconds, with minimum no greater than maximum"}, 400)
                raw_key_ids = body.get("key_ids")
                with _key_lock:
                    if raw_key_ids is None:
                        raw_key_ids = [body.get("key_id") or _config["active_key_id"] or ""]
                    if not isinstance(raw_key_ids, list) or any(not isinstance(kid, str) for kid in raw_key_ids):
                        return self._json({"error": "Select one or more configured Gemini keys"}, 400)
                    key_ids = list(dict.fromkeys(kid for kid in raw_key_ids if kid))
                    key_entries = [{"id": kid, **_config["keys"][kid]} for kid in key_ids if kid in _config["keys"]]
                    model = _config["model"]
                if use_gemini and (not key_entries or len(key_entries) != len(key_ids)):
                    return self._json({"error": "Select one or more configured Gemini keys in Settings"}, 400)
                resume_project = None
                if use_gemini:
                    resume_project, prior = _incomplete_review(video) if video is not None else (None, {})
                    if resume_project is not None:
                        fingerprints = [hashlib.sha256(item["key"].encode("utf-8")).hexdigest()
                                        for item in key_entries]
                        saved_fingerprints = prior.get("key_fingerprints") or [prior.get("key_fingerprint")]
                        if saved_fingerprints != fingerprints:
                            return self._json({"error": "An unfinished review checkpoint exists for this recording. Select the same Gemini key set to resume."}, 409)
                        if prior.get("model") != model:
                            return self._json({"error": "This recording has an unfinished review checkpoint. Select the same Gemini model to resume it."}, 409)
                        if prior.get("candidate_mode", "top50") != candidate_mode:
                            return self._json({"error": "This recording has an unfinished review checkpoint. Choose the same candidate result mode to resume it."}, 409)
                        prior_min = prior.get("clip_min_seconds")
                        prior_max = prior.get("clip_max_seconds")
                        if prior_min is not None and (prior_min != clip_min_seconds or prior_max != clip_max_seconds):
                            return self._json({"error": "This recording has an unfinished review checkpoint. Choose the same clip length range to resume it."}, 409)
                with _lock:
                    if _job.get("status") in ("queued", "scanning", "reviewing"):
                        return self._json({"error": "A scan is already running"}, 409)
                    _job = {"status": "queued", "message": "Resuming saved chunks" if resume_project else ("Queued stream download" if source_url else "Queued"),
                            "project": str(resume_project) if resume_project else None,
                            "started": time.time(), "error": None}
                thread = threading.Thread(target=_scan_job,
                    args=(video, source_url, use_gemini, model, key_entries, max_candidates,
                          clip_min_seconds, clip_max_seconds, analyze_audio, analyze_transcript,
                          resume_project), daemon=True)
                thread.start()
                return self._json({"ok": True}, 202)
            return self._json({"error": "Not found"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception:
            return self._json({"error": "The local dashboard could not complete that request"}, 500)


def serve(port: int = 0) -> None:
    """Run a local dashboard; port 0 selects a free loopback port."""
    PROJECTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Niche Machine dashboard: {url}", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _config["keys"].clear()
        _config["active_key_id"] = None
        server.server_close()
