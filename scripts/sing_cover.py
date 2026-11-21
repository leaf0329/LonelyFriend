"""Render short local cover samples without changing the running QQ services."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "runtime/singing/seed-vc"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@contextmanager
def gpu_slot():
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # Siyin uses this same mutex for inference and model changes.
    handle = kernel.CreateMutexW(None, False, "Local\\SiVoiceInference")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result == 258:
            raise RuntimeError("祀音正在合成，请稍后再生成翻唱。")
        if result not in (0, 128):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def audio(path: Path) -> np.ndarray:
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != 44100 or not samples.size or not np.isfinite(samples).all():
        raise ValueError(f"无效的44.1kHz音频：{path}")
    return samples


def mix_cover(vocals: Path, accompaniment: Path, converted: Path, output: Path) -> dict:
    original = audio(vocals).mean(axis=1)
    backing = audio(accompaniment)
    voice = audio(converted).mean(axis=1)
    if backing.shape != (len(original), 2):
        raise ValueError("人声与伴奏长度或声道不匹配。")
    difference = len(original) - len(voice)
    if abs(difference) > 44100 // 4:
        raise ValueError("转换后人声时长变化超过0.25秒，停止混音。")
    voice = np.pad(voice, (0, max(0, difference)))[:len(original)]
    source_rms = float(np.sqrt(np.mean(original ** 2)))
    voice_rms = float(np.sqrt(np.mean(voice ** 2)))
    if min(source_rms, voice_rms) < 0.0001:
        raise ValueError("人声为空或接近静音。")
    gain_db = 20 * math.log10(source_rms / voice_rms)
    if abs(gain_db) > 18:
        raise ValueError("人声电平变化异常，停止混音。")
    mixed = backing + (voice * (source_rms / voice_rms))[:, None]
    fade = min(4410, len(mixed) // 2)
    mixed[:fade] *= np.linspace(0, 1, fade, dtype="float32")[:, None]
    mixed[-fade:] *= np.linspace(1, 0, fade, dtype="float32")[:, None]
    peak = float(np.max(np.abs(mixed)))
    scale = min(1, 0.98 / max(peak, 0.0001))
    sf.write(output, mixed * scale, 44100, subtype="PCM_16")
    return {"duration_seconds": len(mixed) / 44100,
            "vocal_gain_db": round(gain_db, 3), "mix_gain": round(scale, 6),
            "peak": round(float(np.max(np.abs(mixed * scale))), 6),
            "alignment_samples": difference}


def transpose_accompaniment(ffmpeg: Path, source: Path, output: Path, pitch: int, run) -> Path:
    if pitch % 12 == 0:
        return source
    # Non-octave vocal shifts need the accompaniment in the same key.
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", source,
         "-af", f"rubberband=tempo=1:pitch={2 ** (pitch / 12):.10f}",
         "-ar", 44100, "-ac", 2, "-c:a", "pcm_f32le", output])
    return output


def main():
    parser = argparse.ArgumentParser(description="本地短片段翻唱试听，不发送QQ消息")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--start", type=float, required=True, help="原曲起点，秒")
    parser.add_argument("--duration", type=float, default=24, help="最多30秒")
    parser.add_argument("--pitch-shift", type=int, nargs="+", default=[0, 12])
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-for-parent", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.wait_for_parent and sys.stdin.readline().strip() != "start":
        raise RuntimeError("翻唱工作进程未确认启动。")
    if not math.isfinite(args.start) or args.start < 0:
        parser.error("起点必须是非负秒数。")
    if not math.isfinite(args.duration) or not 1 <= args.duration <= 30:
        parser.error("试听长度必须在1到30秒之间。")
    if not 1 <= args.steps <= 100 or any(abs(x) > 12 for x in args.pitch_shift):
        parser.error("扩散步数须为1到100，升降调须在正负12半音内。")
    source, reference = args.source.resolve(), args.reference.resolve()
    if not source.is_file() or not reference.is_file():
        parser.error("音源或参考录音不存在。")
    ffmpegs = sorted((ROOT / "runtime/ffmpeg").glob("*/bin/ffmpeg.exe"))
    if not ffmpegs or not (BACKEND / "inference.py").is_file():
        parser.error("请先安装项目FFmpeg和独立Seed-VC环境。")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.update({"TORCH_HOME": str(ROOT / "runtime/singing/torch-cache"),
                        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "MPLBACKEND": "Agg",
                        "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"})
    environment["PATH"] = str(ffmpegs[0].parent) + os.pathsep + environment.get("PATH", "")
    started = time.monotonic()
    report = {"status": "running", "source": str(source), "reference": str(reference),
              "start_seconds": args.start, "duration_seconds": args.duration,
              "steps": args.steps, "variants": []}
    log_path = args.log_file.resolve() if args.log_file is not None else output / "inference.log"

    def run(command, cwd=ROOT, timeout=600):
        with log_path.open("ab") as log:
            subprocess.run([str(x) for x in command], cwd=cwd, env=environment,
                           stdout=log, stderr=log, check=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)

    try:
        clip = output / "source-clip.wav"
        run([ffmpegs[0], "-hide_banner", "-loglevel", "error", "-ss", args.start,
             "-i", source, "-t", args.duration, "-ac", 2, "-ar", 44100, clip])
        if abs(len(audio(clip)) / 44100 - args.duration) > 0.1:
            raise ValueError("所选片段超出音源长度。")
        with gpu_slot():
            print("分离人声与伴奏……", flush=True)
            run([sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems", "vocals",
                 "--segment", 5, "--shifts", 1, "--float32", "--clip-mode", "clamp",
                 "-d", "cuda", "-o", output / "stems", clip])
            stems = output / "stems/htdemucs/source-clip"
            for pitch in dict.fromkeys(args.pitch_shift):
                print(f"转换祀的音色，移调{pitch:+}半音……", flush=True)
                variant_started = time.monotonic()
                converted_dir = output / f"converted-{pitch:+}"
                run([sys.executable, BACKEND / "inference.py", "--source", stems / "vocals.wav",
                     "--target", reference, "--output", converted_dir, "--diffusion-steps", args.steps,
                     "--length-adjust", 1.0, "--inference-cfg-rate", 0.7, "--f0-condition", "True",
                     "--auto-f0-adjust", "False", "--semi-tone-shift", pitch, "--fp16", "True"],
                    cwd=BACKEND)
                converted_files = list(converted_dir.glob("*.wav"))
                if len(converted_files) != 1:
                    raise ValueError("转换结果应包含且仅包含一个音频文件。")
                final = output / f"si-cover-{pitch:+}.wav"
                backing = transpose_accompaniment(ffmpegs[0], stems / "no_vocals.wav",
                                                  converted_dir / "backing.wav", pitch, run)
                result = mix_cover(stems / "vocals.wav", backing,
                                   converted_files[0], final)
                result.update({"pitch_shift": pitch, "path": str(final),
                               "elapsed_seconds": round(time.monotonic() - variant_started, 2)})
                report["variants"].append(result)
                print(f"试听已生成：{final}", flush=True)
        report["status"] = "rendered"
    except Exception as error:
        report.update({"status": "failed", "error": str(error)})
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
