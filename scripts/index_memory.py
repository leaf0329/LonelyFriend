from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

from configure import ROOT, read_json, write_json

sys.path.insert(0, str(ROOT))
from data.plugins.astrbot_plugin_memory_companion.core.models import SessionContext
from data.plugins.astrbot_plugin_memory_companion.core.service import MemoryCompanionService
from plugins.astrbot_plugin_lonelyfriend_memory.local_embedding import LocalChineseEmbeddingProvider
from plugins.astrbot_plugin_lonelyfriend_memory.main import finish_source_index


async def main() -> None:
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 6185)) == 0:
            raise RuntimeError("Stop LonelyFriend before running the offline index maintenance.")
    config = read_json(ROOT / "data/config/astrbot_plugin_memory_companion_config.json")
    config["maintenance"]["auto_schedule_enabled"] = False
    embedding_config = read_json(ROOT / "config/memory-policy.json")["embedding_provider"]
    embedding = LocalChineseEmbeddingProvider(embedding_config, {})
    context = SimpleNamespace(get_embedding_provider_by_id=lambda identifier: embedding if identifier == embedding_config["id"] else None)
    service = MemoryCompanionService(context=context, config=config, plugin_root=ROOT / "data/plugins/astrbot_plugin_memory_companion", data_dir=ROOT / "data/plugin_data/astrbot_plugin_memory_companion")
    try:
        await service.initialize_database()
        await embedding.get_embedding("准备本机历史记忆索引")
        await service._background_backfill_embeddings(embedding, embedding_config["id"])
        rows = await service.store._run_recoverable_database_operation(
            lambda: [dict(row) for row in service.store._conn.execute(
                "SELECT session_id,scope,subject_id,object_id,metadata FROM timeline WHERE event_type='user_message'"
            ).fetchall()]
        )
        contexts = {}
        unbound = 0
        for row in rows:
            metadata = json.loads(row["metadata"])
            bot_id = str(metadata.get("owner_bot_id") or metadata.get("bot_id") or "")
            platform = str(metadata.get("platform") or "")
            if row["scope"] not in {"private", "group"} or not all((row["session_id"], row["object_id"], bot_id, platform)):
                unbound += 1
                continue
            ctx = SessionContext(session_id=row["session_id"], scope=row["scope"], user_id=row["subject_id"], group_id=row["object_id"] if row["scope"] == "group" else "", platform=platform, bot_id=bot_id, persona_id=str(metadata.get("persona_id") or ""))
            key = (ctx.session_id, ctx.current_target_id, bot_id, platform, ctx.persona_id)
            contexts[key] = ctx
        completed = 0
        for ctx in contexts.values():
            completed += int(await finish_source_index(service, ctx))
        report = {"local_embedding_ready": True, "existing_contexts": len(contexts), "completed_contexts": completed, "unbound_message_rows": unbound, "identity_rebinding_performed": False, "chat_text_sent_to_external_embedding_service": False}
        write_json(ROOT / "data/memory-index-status.json", report)
        print(json.dumps(report, ensure_ascii=False))
        if completed != len(contexts):
            raise RuntimeError("Some source partitions remain paused or incomplete; see memory-index-status.json.")
    finally:
        await service.aclose()
        await embedding.terminate()


if __name__ == "__main__":
    asyncio.run(main())
