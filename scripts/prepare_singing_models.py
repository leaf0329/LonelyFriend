"""Download the optional local cover models to the isolated singing directory."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "runtime/singing/seed-vc"
MODELS = [
    ("Plachta/Seed-VC", "checkpoints", [
        "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
        "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
    ]),
    ("lj1995/VoiceConversionWebUI", "checkpoints", ["rmvpe.pt"]),
    ("funasr/campplus", "checkpoints", ["campplus_cn_common.bin"]),
    ("nvidia/bigvgan_v2_44khz_128band_512x", "checkpoints/hf_cache", [
        "config.json", "bigvgan_generator.pt",
    ]),
    ("openai/whisper-small", "checkpoints/hf_cache", [
        "config.json", "preprocessor_config.json", "model.safetensors",
    ]),
]


def main():
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TORCH_HOME"] = str(ROOT / "runtime/singing/torch-cache")
    if not (BACKEND / "inference.py").is_file():
        raise FileNotFoundError("请先按docs/singing.md安装固定版本Seed-VC。")
    from huggingface_hub import hf_hub_download
    from demucs.pretrained import get_model

    manifest = []
    for repo, cache, names in MODELS:
        for name in names:
            path = Path(hf_hub_download(repo, name, cache_dir=BACKEND / cache, token=False))
            manifest.append({"repo": repo, "revision": path.parent.name,
                             "file": name, "path": str(path), "bytes": path.stat().st_size})
            print(f"就绪：{repo}/{name}", flush=True)
    get_model("htdemucs")
    output = ROOT / "data/singing/model-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("模型已下载，后续翻唱使用本地缓存。", flush=True)


if __name__ == "__main__":
    main()
