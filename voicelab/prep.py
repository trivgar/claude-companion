#!/usr/bin/env python3
"""Turn a folder of audio into a GPT-SoVITS dataset.

    python3 prep.py <clips-dir> <out-dir> <voice-name> [--lang en] [--no-transcribe]

Output in <out-dir>:
    wavs/<voice>_0001.wav ...   mono 32 kHz, 3 to 12 s, cut on silence
    <voice>.list                path|speaker|LANG|text   (GPT-SoVITS list format)

Needs ffmpeg on PATH. Transcription uses faster-whisper when it is installed
(`pip install faster-whisper`); without it the list is written with empty text
and this script says so: fill the text in by hand or install it and rerun.
Only use clips you have the right to use.
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

MIN_S, MAX_S, PAD_S = 3.0, 12.0, 0.1
SLACK = 0.2  # silence detection trims edges; padding in _cut brings a piece back to MIN_S
AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".mkv", ".webm"}
_START = re.compile(r"silence_start: (-?[\d.]+)")
_END = re.compile(r"silence_end: ([\d.]+)")
_DUR = re.compile(r"Duration: (\d+):(\d+):([\d.]+)")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg not found. In WSL: sudo apt install ffmpeg")
    return exe


def probe(path: Path, noise_db: int = -35, min_silence: float = 0.4) -> tuple[float, list[tuple[float, float]]]:
    """(duration, [(silence_start, silence_end)]) from one ffmpeg pass."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = _DUR.search(r.stderr)
    if not m:
        return 0.0, []
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    sil, start = [], None
    for line in r.stderr.splitlines():
        s, e = _START.search(line), _END.search(line)
        if s:
            start = max(0.0, float(s[1]))
        elif e and start is not None:
            sil.append((start, float(e[1])))
            start = None
    if start is not None:
        sil.append((start, dur))
    return dur, sil


def plan_pieces(duration: float, silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Cut points: speech between silences, short runs joined, long runs split."""
    segs, pos = [], 0.0
    for a, b in sorted(silences):
        if a - pos > 0.05:
            segs.append((pos, a))
        pos = max(pos, b)
    if duration - pos > 0.05:
        segs.append((pos, duration))

    out: list[tuple[float, float]] = []

    def emit(a: float, b: float) -> None:
        n = math.ceil((b - a) / MAX_S)
        step = (b - a) / n
        for i in range(n):
            if step >= MIN_S - SLACK:
                out.append((a + i * step, a + (i + 1) * step))

    cur = None
    for a, b in segs:
        if cur and b - cur[0] <= MAX_S and (cur[1] - cur[0] < MIN_S - SLACK or b - a < MIN_S - SLACK):
            cur = (cur[0], b)
            continue
        if cur:
            emit(*cur)
        cur = (a, b)
    if cur:
        emit(*cur)
    return out


def _cut(src: Path, a: float, b: float, dst: Path, duration: float) -> None:
    a, b = max(0.0, a - PAD_S), min(duration, b + PAD_S)
    subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                    "-i", str(src), "-ac", "1", "-ar", "32000", "-c:a", "pcm_s16le", str(dst)], check=True)


def _transcriber(lang: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        model = WhisperModel("small", device="auto", compute_type="auto")
    except Exception as exc:  # no CUDA libs, no download: fall back on empty text
        print(f"faster-whisper could not start ({exc}); writing empty text", file=sys.stderr)
        return None

    def run(path: Path) -> str:
        segs, _ = model.transcribe(str(path), language=lang.lower()[:2], vad_filter=False)
        return " ".join(s.text.strip() for s in segs).replace("|", ",").replace("\n", " ").strip()
    return run


def prep(src: Path, out: Path, voice: str, lang: str = "en", transcribe: bool = True) -> dict:
    src, out = Path(src), Path(out)
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in AUDIO)
    wavs = out / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)
    n, made = 0, []
    for f in files:
        dur, sil = probe(f)
        for a, b in plan_pieces(dur, sil):
            n += 1
            dst = wavs / f"{voice}_{n:04d}.wav"
            _cut(f, a, b, dst, dur)
            made.append(dst)
    stt = _transcriber(lang) if transcribe else None
    if transcribe and stt is None:
        print("faster-whisper not available: the list has empty text. Install it "
              "(pip install faster-whisper) and rerun, or fill the text column in by hand.", file=sys.stderr)
    lines = [f"{p.resolve()}|{voice}|{lang.upper()}|{stt(p) if stt else ''}" for p in made]
    (out / f"{voice}.list").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"files": len(files), "pieces": len(made), "transcribed": stt is not None, "list": str(out / f"{voice}.list")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clips")
    ap.add_argument("out")
    ap.add_argument("voice")
    ap.add_argument("--lang", default="en", help="en, zh, ja, ko or yue")
    ap.add_argument("--no-transcribe", action="store_true")
    a = ap.parse_args(argv)
    res = prep(Path(a.clips), Path(a.out), a.voice, a.lang, not a.no_transcribe)
    print(f"{res['files']} files -> {res['pieces']} pieces; list: {res['list']}")
    return 0 if res["pieces"] else 1


if __name__ == "__main__":
    sys.exit(main())
