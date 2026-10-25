from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from huggingface_hub import snapshot_download
from plugins.astrbot_plugin_lonelyfriend_memory.local_embedding import (
    DIMENSIONS, MODEL_FILES, PROVIDER_ID, REPOSITORY, REVISION, LocalChineseEmbeddingProvider,
)


async def main() -> None:
    cache = ROOT / "runtime/models"
    snapshot_download(REPOSITORY, revision=REVISION, cache_dir=str(cache), allow_patterns=MODEL_FILES)
    provider = LocalChineseEmbeddingProvider({"id": PROVIDER_ID, "type": "lonelyfriend_local_embedding", "embedding_cache_dir": str(cache)}, {})
    try:
        vector = await provider.get_embedding("记住具体经历，也记得是谁说的。")
        assert len(vector) == DIMENSIONS
        print(json.dumps({"local_model_ready": True, "dimensions": DIMENSIONS, "revision": REVISION}))
    finally:
        await provider.terminate()


if __name__ == "__main__":
    asyncio.run(main())
