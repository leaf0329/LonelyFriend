from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.provider import EmbeddingProvider
from astrbot.core.provider.register import register_provider_adapter
from astrbot.core.utils.astrbot_path import get_astrbot_root

MODEL = "BAAI/bge-small-zh-v1.5"
REPOSITORY = "Qdrant/bge-small-zh-v1.5"
REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
DIMENSIONS = 512
PROVIDER_ID = "cuteq-local-embedding"
ENCODING_REVISION = f"{REPOSITORY}@{REVISION}:mean320-overlap48-v1"
MODEL_FILES = ["config.json", "model_optimized.onnx", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"]


@register_provider_adapter(
    "lonelyfriend_local_embedding",
    "LonelyFriend 本地中文语义检索",
    provider_type=ProviderType.EMBEDDING,
    default_config_tmpl={"id": PROVIDER_ID, "provider_type": "embedding", "enable": True, "embedding_cache_dir": "runtime/models"},
)
class LocalChineseEmbeddingProvider(EmbeddingProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.set_model(MODEL)
        self.cache_dir = Path(provider_config.get("embedding_cache_dir", "runtime/models"))
        if not self.cache_dir.is_absolute():
            self.cache_dir = Path(get_astrbot_root()) / self.cache_dir
        self._model = None
        # Cancellation must not start concurrent tokenizer/model work on another thread.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lonelyfriend-embedding")

    def _encode(self, texts: list[str]) -> list[list[float]]:
        from fastembed import TextEmbedding
        from huggingface_hub import snapshot_download

        if self._model is None:
            model_path = snapshot_download(REPOSITORY, revision=REVISION, cache_dir=str(self.cache_dir), allow_patterns=MODEL_FILES, local_files_only=True)
            self._model = TextEmbedding(MODEL, specific_model_path=model_path, local_files_only=True, threads=min(4, os.cpu_count() or 1), providers=["CPUExecutionProvider"])
        chunks: list[str] = []
        ranges: list[tuple[int, int]] = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Embedding input must contain nonempty text.")
            start = len(chunks)
            # BGE has a 512-token limit. Pool short overlapping pieces so long
            # summaries and source windows are not represented by their prefix alone.
            for offset in range(0, len(text), 272):
                chunks.append(text[offset:offset + 320])
                if offset + 320 >= len(text):
                    break
            ranges.append((start, len(chunks)))
        encoded = np.asarray(list(self._model.embed(chunks, batch_size=16)), dtype=np.float32)
        vectors = []
        for start, end in ranges:
            vector = encoded[start:end].mean(axis=0)
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(vector).all() or norm <= 0:
                raise ValueError("Local embedding model returned an invalid vector.")
            vectors.append((vector / norm).tolist())
        return vectors

    async def get_embedding(self, text: str) -> list[float]:
        return (await self.get_embeddings([text]))[0]

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        if not text:
            return []
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._encode, text)

    def get_dim(self) -> int:
        return DIMENSIONS

    async def terminate(self) -> None:
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
        self._model = None
