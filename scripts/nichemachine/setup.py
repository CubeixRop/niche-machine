"""Prepare Niche Machine dependencies on Windows, macOS, or Linux."""
from __future__ import annotations
import platform, shutil, subprocess, sys, urllib.request
import zipfile
from pathlib import Path
from .util import APP, MODELS, SHORTS, VENV, WORK, say, settings
PIP_PKGS = ["numpy", "opencv-python-headless", "pillow", "kokoro-onnx", "soundfile", "yt-dlp", "youtube-transcript-api"]
KOKORO_MODEL = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = "voices-v1.0.bin"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/{name}"
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
WHISPER_WINDOWS_URL = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip"
def _have(cmd): return shutil.which(cmd) or shutil.which(cmd + ".exe")
def _run(cmd):
    say("  $ " + " ".join(map(str, cmd)))
    return subprocess.run(cmd).returncode == 0
def _download(url, dest):
    try:
        dest.parent.mkdir(parents=True, exist_ok=True); urllib.request.urlretrieve(url, dest); return True
    except Exception as exc: say(f"  download failed: {exc}"); return False
def setup(doctor_only=False):
    ok = True; win = sys.platform == "win32"
    say("Niche Machine · " + ("checking" if doctor_only else "setting up") + " this computer\n")
    say(f"✓ {platform.system()} · {platform.machine()}")
    free = shutil.disk_usage(Path.home()).free / 1e9
    say(f"{'✓' if free > 6 else '!'} {free:.0f} GB free disk")
    if win:
        winget = _have("winget")
        if not _have("ffmpeg") and winget and not doctor_only:
            ok &= _run([winget, "install", "--id", "Gyan.FFmpeg", "--exact", "--accept-source-agreements", "--accept-package-agreements"])
        have_ff = bool(_have("ffmpeg") and _have("ffprobe"))
        say(f"{'✓' if have_ff else '✗'} ffmpeg + ffprobe" + ("" if have_ff else " (install FFmpeg, then reopen Terminal)")); ok &= have_ff
    else:
        brew = shutil.which("brew")
        if sys.platform == "darwin" and brew and not _have("ffmpeg") and not doctor_only: ok &= _run([brew, "install", "ffmpeg"])
        ok &= bool(_have("ffmpeg") and _have("ffprobe"))
    vpy = VENV / ("Scripts/python.exe" if win else "bin/python")
    # Use the interpreter running this setup command so the private venv has
    # the same supported Python version as the launcher.
    py = sys.executable
    if not vpy.exists() and not doctor_only and py:
        APP.mkdir(parents=True, exist_ok=True); ok &= _run([py, "-m", "venv", str(VENV)])
    def imports_ok(): return vpy.exists() and subprocess.run([str(vpy), "-c", "import numpy, cv2, PIL, kokoro_onnx, soundfile, yt_dlp"], capture_output=True).returncode == 0
    if vpy.exists() and not doctor_only and not imports_ok():
        _run([str(vpy), "-m", "pip", "install", "-q", "--upgrade", "pip"]); ok &= _run([str(vpy), "-m", "pip", "install", "-q"] + PIP_PKGS)
    good = imports_ok(); say(f"{'✓' if good else '✗'} private Python environment ({VENV})"); ok &= good
    MODELS.mkdir(parents=True, exist_ok=True)
    if win and not _have("whisper-cli"):
        exe = MODELS / "whisper-cli.exe"
        if not exe.exists() and not doctor_only:
            archive = MODELS / "whisper-windows.zip"
            say("  downloading whisper.cpp for Windows x64…")
            if _download(WHISPER_WINDOWS_URL, archive):
                try:
                    with zipfile.ZipFile(archive) as z:
                        cli = next(n for n in z.namelist() if Path(n).name.lower() == "whisper-cli.exe")
                        with z.open(cli) as src, exe.open("wb") as dst: shutil.copyfileobj(src, dst)
                        for member in z.namelist():
                            if member.lower().endswith(".dll"):
                                with z.open(member) as src, (MODELS / Path(member).name).open("wb") as dst:
                                    shutil.copyfileobj(src, dst)
                except Exception as exc:
                    say(f"  could not extract whisper.cpp: {exc}")
                finally:
                    archive.unlink(missing_ok=True)
        whisper = exe.exists() or bool(_have("whisper-cli"))
    else: whisper = bool(_have("whisper-cli") or _have("whisper-cpp"))
    say(f"{'✓' if whisper else '✗'} whisper-cli"); ok &= whisper
    name = settings()["whisper_model"]
    mp = MODELS / f"ggml-{name}.bin"
    if not mp.exists() and not doctor_only: say(f"  downloading Whisper {name}…"); ok &= _download(WHISPER_URL.format(name=name), MODELS / mp.name)
    say(f"{'✓' if mp.exists() else '✗'} Whisper {name} model"); ok &= mp.exists()
    for fn in (KOKORO_MODEL, KOKORO_VOICES):
        f = MODELS / fn
        if not f.exists() and not doctor_only: say(f"  downloading {fn}…"); ok &= _download(KOKORO_URL.format(name=fn), f)
        say(f"{'✓' if f.exists() else '✗'} voice model: {fn}"); ok &= f.exists()
    voice_ok = False
    if vpy.exists() and all((MODELS / fn).exists() for fn in (KOKORO_MODEL, KOKORO_VOICES)):
        check = subprocess.run([str(vpy), "-c",
                                "from kokoro_onnx import Kokoro; import sys; Kokoro(sys.argv[1], sys.argv[2])",
                                str(MODELS / KOKORO_MODEL), str(MODELS / KOKORO_VOICES)],
                               capture_output=True, text=True)
        voice_ok = check.returncode == 0
        if not voice_ok:
            detail = (check.stderr or check.stdout or "model initialization failed").strip().splitlines()[-1]
            say(f"✗ voice model cannot load: {detail}")
    else:
        say("✗ voice model cannot be checked until both model files exist")
    ok &= voice_ok
    for d in (WORK / "gameplay", WORK / "projects", SHORTS): d.mkdir(parents=True, exist_ok=True)
    say(f"\n{'✓ Ready.' if ok else '✗ Not ready yet: fix the ✗ lines above.'}")
    if ok: say(f"  Gameplay: {WORK / 'gameplay'}\n  Shorts: {SHORTS}")
    return ok

