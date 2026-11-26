#!/usr/bin/env python3
"""Range-only preview, audio extraction, and selected-clip fetch for public MP4 URLs.

All upstream reads pass through a loopback proxy which accepts bounded Range
requests only, requires HTTP 206, validates Content-Range/Content-Length, and
stops at a shared transfer budget. It never stores the source video.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

CHUNK_BYTES = 64 * 1024
USER_AGENT = "NicheMachine-RangePreview/1.0"


class RangeSource:
    def __init__(self, url: str, max_bytes: int):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Source must be a public HTTPS media URL")
        self.url = url
        self.max_bytes = max_bytes
        self.bytes_read = 0
        self.reserved_bytes = 0
        self.lock = threading.Lock()
        self.total_size = self._verify_range_support()

    def _fetch(self, start: int, end: int) -> tuple[bytes, str]:
        reserved = end - start + 1
        with self.lock:
            if self.bytes_read + self.reserved_bytes + reserved > self.max_bytes:
                raise RuntimeError("range transfer budget reached")
            self.reserved_bytes += reserved
        try:
            request = urllib.request.Request(
                self.url,
                headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity", "User-Agent": USER_AGENT},
            )
            try:
                response = urllib.request.urlopen(request, timeout=30)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"upstream range request returned HTTP {exc.code}") from exc
            with response:
                if response.status != 206:
                    # Close without consuming a non-range/full-response body.
                    raise RuntimeError(f"upstream ignored byte ranges (HTTP {response.status}); refusing full download")
                content_range = response.headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if not match or int(match[1]) != start or int(match[2]) > end:
                    raise RuntimeError("upstream returned an invalid or oversized Content-Range")
                expected = int(match[2]) - start + 1
                if int(response.headers.get("Content-Length", -1)) != expected:
                    raise RuntimeError("upstream omitted or misstated the range Content-Length")
                data = response.read(expected + 1)
                if len(data) != expected:
                    raise RuntimeError("upstream range body length did not match Content-Range")
                with self.lock:
                    self.reserved_bytes -= reserved
                    self.bytes_read += len(data)
                reserved = 0
                return data, match[3]
        finally:
            if reserved:
                with self.lock:
                    self.reserved_bytes -= reserved

    def _verify_range_support(self) -> int:
        data, total = self._fetch(0, 0)
        if len(data) != 1 or int(total) < 1:
            raise RuntimeError("Could not verify byte-range access for this media URL")
        return int(total)

    def fetch(self, start: int, end: int) -> tuple[bytes, int]:
        if start < 0 or start >= self.total_size:
            raise RuntimeError("requested range is outside the media object")
        end = min(end, start + CHUNK_BYTES - 1, self.total_size - 1)
        data, total = self._fetch(start, end)
        if int(total) != self.total_size:
            raise RuntimeError("media size changed during preview")
        return data, end


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, source: RangeSource):
        self.source = source
        self.last_error: str | None = None
        super().__init__(("127.0.0.1", 0), ProxyHandler)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ProxyServer

    def log_message(self, *_args) -> None:
        pass

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(self.server.source.total_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if not match:
            self.send_error(416, "A bounded byte range is required")
            return
        start = int(match[1])
        requested_end = int(match[2]) if match[2] else start + CHUNK_BYTES - 1
        try:
            data, end = self.server.source.fetch(start, requested_end)
        except Exception as exc:
            self.server.last_error = str(exc)
            self.send_error(502, str(exc))
            return
        self.send_response(206)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Range", f"bytes {start}-{end}/{self.server.source.total_size}")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def ffmpeg_base(ffmpeg: str, url: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-request_size", str(CHUNK_BYTES),
            "-initial_request_size", str(CHUNK_BYTES), "-i", url]


def run_ffmpeg(command: list[str], server: ProxyServer, timeout: int = 180) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg timed out; no full-source fallback was attempted") from exc
    if result.returncode:
        detail = server.last_error or (result.stderr or "").strip()[-1600:] or "FFmpeg failed"
        raise RuntimeError(f"FFmpeg stopped: {detail}; no full-source fallback was attempted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public direct HTTPS URL to a range-enabled MP4")
    parser.add_argument("--out", type=Path, help="New directory for preview JPEGs")
    parser.add_argument("--interval", type=float, default=15.0, help="Uniform sample interval in seconds (default: 15)")
    parser.add_argument("--times", nargs="+", type=float, help="Optional explicit preview times in seconds")
    parser.add_argument("--height", type=int, default=480, help="Preview frame height (default: 480)")
    parser.add_argument("--audio-out", type=Path, help="Optional new directory for timestamped 5-minute FLAC audio chunks")
    parser.add_argument("--audio-segment-seconds", type=int, default=300, help="Audio chunk length (default: 300 seconds)")
    parser.add_argument("--clip", nargs=2, type=float, metavar=("START", "END"), help="Fetch one approved clip range in source seconds")
    parser.add_argument("--clip-out", type=Path, help="New MP4 output path; required with --clip")
    parser.add_argument("--max-transfer-mb", type=float, default=128.0, help="Shared hard upstream-read ceiling in MiB (default: 128)")
    parser.add_argument("--max-transfer-percent", type=float, help="Shared hard ceiling as percent of source size (preview default: 15; selected-clip default: 85; never 100)")
    args = parser.parse_args()

    if args.max_transfer_mb <= 0 or (args.max_transfer_percent is not None and not 0 < args.max_transfer_percent < 100):
        parser.error("transfer limits must be positive and max-transfer-percent below 100")
    if args.clip:
        if args.out or args.audio_out or args.times:
            parser.error("--clip cannot be combined with preview/audio options")
        if not args.clip_out:
            parser.error("--clip-out is required with --clip")
        if args.clip[0] < 0 or args.clip[1] <= args.clip[0]:
            parser.error("--clip requires 0 <= START < END")
        if args.clip_out.suffix.lower() != ".mp4":
            parser.error("--clip-out must end in .mp4")
    elif not args.out:
        parser.error("--out is required for preview mode")
    if args.out and args.out.exists():
        parser.error(f"Output directory already exists; refusing to overwrite: {args.out}")
    if args.audio_out and args.audio_out.exists():
        parser.error(f"Audio output directory already exists; refusing to overwrite: {args.audio_out}")
    if args.clip_out and args.clip_out.exists():
        parser.error(f"Clip already exists; refusing to overwrite: {args.clip_out}")
    if not args.clip and not (64 <= args.height <= 2160):
        parser.error("--height must be between 64 and 2160")
    if args.audio_segment_seconds <= 0:
        parser.error("--audio-segment-seconds must be positive")

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        parser.error("ffmpeg and ffprobe must be installed and available on PATH")

    # The one-byte initial request verifies Range support before the remaining budget is set.
    source = RangeSource(args.url, max_bytes=1)
    percent_limit = args.max_transfer_percent if args.max_transfer_percent is not None else (85.0 if args.clip else 15.0)
    budget = min(int(args.max_transfer_mb * 1024 * 1024), int(source.total_size * percent_limit / 100))
    if budget <= source.bytes_read:
        parser.error("transfer ceiling is too small to probe this media")
    source.max_bytes = budget
    server = ProxyServer(source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local_url = f"http://127.0.0.1:{server.server_port}/media.mp4"
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-request_size", str(CHUNK_BYTES), "-initial_request_size", str(CHUNK_BYTES),
             "-i", local_url, "-show_entries", "format=duration:stream=codec_type,codec_name", "-of", "json"],
            capture_output=True, text=True, timeout=60,
        )
        if probe.returncode:
            raise RuntimeError("The range-limited source could not be probed: " + (server.last_error or probe.stderr.strip()[-1200:]))
        metadata = json.loads(probe.stdout)
        duration = float(metadata["format"]["duration"])
        if not 0 < duration < 7 * 24 * 60 * 60:
            raise RuntimeError("Source duration is missing or outside the supported 7-day range")
        if args.clip and args.clip[1] > duration:
            raise RuntimeError(f"Selected clip end exceeds source duration ({duration:.3f}s)")

        if args.clip:
            args.clip_out.parent.mkdir(parents=True, exist_ok=True)
            start, end = args.clip
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
                       "-request_size", str(CHUNK_BYTES), "-initial_request_size", str(CHUNK_BYTES), "-i", local_url,
                       "-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                       "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(args.clip_out)]
            try:
                run_ffmpeg(command, server)
            except Exception:
                args.clip_out.unlink(missing_ok=True)
                raise
            result = {"selected_source_start_seconds": start, "selected_source_end_seconds": end,
                      "requested_duration_seconds": end - start, "output": str(args.clip_out),
                      "output_bytes": args.clip_out.stat().st_size}
        else:
            times = sorted(set(args.times)) if args.times else []
            if times and any(t < 0 or t >= duration for t in times):
                raise ValueError(f"Every sample time must be within 0 and {duration:.3f} seconds")
            if not times:
                if args.interval <= 0:
                    raise ValueError("--interval must be greater than zero")
                times = []
                current = 0.0
                while current < duration:
                    times.append(current)
                    current += args.interval

            frame_files: list[str] = []
            frame_error: str | None = None
            audio_files: list[dict[str, float | str]] = []
            audio_error: str | None = None
            audio_covered_seconds = 0.0
            args.out.mkdir(parents=True)

            def extract_frames() -> tuple[list[str], str | None]:
                extracted: list[str] = []
                failure: str | None = None
                for index, timestamp in enumerate(times):
                    target = args.out / f"frame-{timestamp:012.3f}.jpg"
                    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
                               "-request_size", str(CHUNK_BYTES), "-initial_request_size", str(CHUNK_BYTES), "-i", local_url,
                               "-frames:v", "1", "-vf", f"scale=-2:{args.height}", "-q:v", "4", str(target)]
                    try:
                        run_ffmpeg(command, server, timeout=90)
                        if not target.is_file() or target.stat().st_size == 0:
                            raise RuntimeError("FFmpeg produced an empty preview frame")
                    except Exception as exc:
                        target.unlink(missing_ok=True)
                        failure = str(exc)
                        print(f"STOPPED at frame {timestamp:.3f}s: {failure}", file=sys.stderr)
                        break
                    extracted.append(target.name)
                    pct = 100 * source.bytes_read / source.total_size
                    print(f"frame {index + 1}/{len(times)} at {timestamp:.3f}s · {source.bytes_read:,}/{source.total_size:,} bytes ({pct:.2f}%)")
                return extracted, failure

            def extract_audio() -> tuple[list[dict[str, float | str]], float, str | None]:
                if not args.audio_out:
                    return [], 0.0, None
                if not any(s.get("codec_type") == "audio" for s in metadata.get("streams", [])):
                    return [], 0.0, "Source has no audio stream"
                args.audio_out.mkdir(parents=True)
                pattern = args.audio_out / "audio-%03d.flac"
                command = ffmpeg_base(ffmpeg, local_url) + [
                    "-map", "0:a:0", "-vn", "-c:a", "flac", "-f", "segment",
                    "-segment_time", str(args.audio_segment_seconds), "-reset_timestamps", "1", "-segment_format", "flac", str(pattern),
                ]
                failure = None
                try:
                    run_ffmpeg(command, server, timeout=1800)
                except Exception as exc:
                    failure = str(exc)
                files: list[dict[str, float | str]] = []
                covered = 0.0
                for index, audio_file in enumerate(sorted(args.audio_out.glob("audio-*.flac"))):
                    check = subprocess.run(
                        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(audio_file)],
                        capture_output=True, text=True, timeout=30,
                    )
                    if check.returncode:
                        continue
                    chunk_duration = float(json.loads(check.stdout)["format"]["duration"])
                    if chunk_duration <= 0:
                        continue
                    start = index * args.audio_segment_seconds
                    files.append({"file": audio_file.name, "source_start_seconds": start, "duration_seconds": chunk_duration})
                    covered += chunk_duration
                if not files and not failure:
                    failure = "No audio chunks were produced"
                tolerance = max(2.0, duration * 0.01)
                if failure is None and covered < duration - tolerance:
                    failure = f"Only {covered:.3f} of {duration:.3f} source audio seconds were extracted within the shared transfer budget"
                return files, covered, failure

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                frame_future = executor.submit(extract_frames)
                audio_future = executor.submit(extract_audio) if args.audio_out else None
                frame_files, frame_error = frame_future.result()
                if audio_future:
                    audio_files, audio_covered_seconds, audio_error = audio_future.result()

            result = {
                "duration_seconds": duration,
                "source_size_bytes": source.total_size,
                "upstream_bytes_read": source.bytes_read,
                "source_percent_read": round(100 * source.bytes_read / source.total_size, 3),
                "hard_transfer_budget_bytes": budget,
                "frames": frame_files,
                "frame_error": frame_error,
                "complete_sampling_grid": len(frame_files) == len(times),
                "full_source_saved": False,
                "audio_chunks": audio_files,
                "audio_covered_seconds": round(audio_covered_seconds, 3),
                "audio_complete": bool(audio_files) and not audio_error if args.audio_out else None,
                "audio_error": audio_error,
            }
            if args.out:
                (args.out / "preview-manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        result.update({"hard_transfer_budget_bytes": budget, "upstream_bytes_read": source.bytes_read,
                       "source_size_bytes": source.total_size,
                       "source_percent_read": round(100 * source.bytes_read / source.total_size, 3)})
        print(json.dumps(result, indent=2))
        return 0 if (args.clip or result.get("frames")) else 2
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"remote_preview.py: {exc}", file=sys.stderr)
        raise SystemExit(2)
