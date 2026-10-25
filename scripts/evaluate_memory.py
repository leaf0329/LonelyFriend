from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from openai import AsyncOpenAI

from configure import ROOT, PROVIDER_ID, load_persona_prompt, read_baseline_config, read_json, write_json
sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_memory.local_embedding import LocalChineseEmbeddingProvider
from plugins.astrbot_plugin_lonelyfriend_memory.main import normalize_source_projection, qq_conversation_rules

PLUGIN = ROOT / "data/plugins/astrbot_plugin_memory_companion"
sys.path.insert(0, str(PLUGIN / "tests"))
from package_bootstrap import bootstrap_package

bootstrap_package(PLUGIN)
from astrbot_plugin_memory_companion.core.models import SessionContext
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService
from astrbot_plugin_memory_companion.core.source_semantic import find_active_generation
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.astr_main_agent import _append_system_reminders
from astrbot.api.provider import ProviderRequest

BOT = "999999999"


def event_for(user: str, text: str, group: str = ""):
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    message.sender = MessageMember(user_id=user, nickname="虚构验收用户")
    message.group_id = group
    message.message = []
    message.self_id = BOT
    message.message_id = uuid.uuid4().hex
    event = AiocqhttpMessageEvent(text, message, PlatformMetadata(name="aiocqhttp", description="isolated evaluation", id="cuteq_qq"), group or user, bot=None)
    event.persona_id = "dafeiyu"
    return event


TOOLS = [{"type": "function", "function": {
    "name": "memory_companion_sources",
    "description": "当记忆摘要不足时查历史原文，找到同一件事及后续。search 按词句包含搜索；context 读某条前后文；read 补读长消息；range 按带时区ISO日期范围读取。返回片段可能不完整，未找到就如实说明。",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["search", "context", "read", "range"]},
        "terms": {"type": "array", "items": {"type": "string"}},
        "source_ref": {"type": "string"}, "direction": {"type": "string", "enum": ["around", "before", "after"]},
        "start_at": {"type": "string"}, "end_at": {"type": "string"},
        "cursor": {"type": "string"}, "excerpt_offset": {"type": "integer"}, "limit": {"type": "integer"}
    }, "required": ["action"]}
}}, {"type": "function", "function": {
    "name": "memory_companion_recall", "description": "搜索当前私聊或群聊有权读取的长期记忆。缺少具体细节时继续查原文。",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]}
}}]
TOOLS.append({"type": "function", "function": {
    "name": "memory_companion_discover_sources",
    "description": "用自然语言语义检索历史原文；用于换了说法、摘要遗漏或关键词搜索无结果的往事。候选不证明查全，必要时继续读来源及前后文。",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}
}})


async def prepare_indexes(service, embedding, contexts):
    if not service.config.bool("source_semantic.enabled", False):
        return {"enabled": False}
    await service.initialize_database()
    normalize_source_projection(service)
    await embedding.get_embedding("准备历史检索")
    await service._background_backfill_embeddings(embedding, embedding.provider_config["id"])
    documents = 0
    for ctx in contexts:
        configuration = service._source_semantic_build_configuration(ctx, embedding.provider_config["id"])
        config_hash = service._source_semantic_config_hash(configuration)
        for _ in range(64):
            await service._run_source_semantic_maintenance(ctx, "", "acceptance:" + ctx.session_id)
            generation = await service._source_semantic_database(lambda conn: find_active_generation(conn, config_hash))
            assert generation, "No native source semantic generation was created"
            status = await service.source_semantic_status(generation["generation"])
            if status["state"] == "ready":
                documents += status["ready_documents"]
                break
            if status["worker"].get("last_error") not in ("", "batch_limit_reached", "provider_call_limit_reached"):
                raise RuntimeError("Source index failed: " + status["worker"]["last_error"])
        else:
            raise RuntimeError("Source indexing did not finish within the bounded fixture budget")
    return {"enabled": True, "contexts": len(contexts), "documents": documents}


async def answer(client, model, service, config, question, persona):
    event = event_for(question["user"], question["text"], question.get("group", ""))
    ctx = await service.identity.resolve_event_context(event)
    _, selected, _, slots = await service.explain_context_for_event(event, question["text"], config["memory_injection"]["top_k"])
    injected = service.injection.compose(ctx, selected, max_chars=config["memory_injection"]["max_chars"], max_item_chars=config["memory_injection"]["max_item_chars"], slot_sections=list(slots.items()))
    request = ProviderRequest()
    _append_system_reminders(event, request, {"identifier": True}, "Asia/Shanghai")
    identity = "\n".join(part.text for part in request.extra_user_content_parts)
    binding_path = ROOT / "data/persona-binding.json"
    channel = qq_conversation_rules(event, read_json(binding_path) if binding_path.is_file() else {})
    messages = [{"role": "system", "content": persona + "\n" + channel + "\n当前日期为2026年10月26日。仅依据当前会话可读取的历史回答，不把问题里的暗示当作发生过的事实。金额用阿拉伯数字。"}, {"role": "user", "content": identity + "\n" + injected + "\n\n" + question["text"]}]
    calls = []
    started = time.monotonic()
    for _ in range(8):
        response = await client.chat.completions.create(model=model, messages=messages, tools=TOOLS, reasoning_effort="none", max_tokens=1800)
        message = response.choices[0].message
        if not message.tool_calls:
            text = message.content or ""
            passed = all(any(term in text for term in choices) for choices in question["required"])
            passed = passed and not any(term in text for term in question.get("forbidden", []))
            return {"id": question["id"], "question": question["text"], "answer": text, "passed": passed, "seconds": round(time.monotonic()-started, 2), "source_tool_calls": calls, "automatic_memory_items": len(selected)}
        messages.append(message.model_dump(exclude_none=True))
        for call in message.tool_calls:
            arguments = json.loads(call.function.arguments)
            if call.function.name == "memory_companion_sources":
                result = await service.tool_sources(event, **arguments)
            elif call.function.name == "memory_companion_recall":
                result = await service.tool_recall(event, **arguments)
            elif call.function.name == "memory_companion_discover_sources":
                result = await service.tool_discover_sources(event, **arguments)
            else:
                raise RuntimeError("Unexpected evaluation tool")
            calls.append({"name": call.function.name, "arguments": arguments, "ok": result.get("ok"), "source_count": len(result.get("sources", []))})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    raise RuntimeError("Evaluation response exceeded the tool-call budget")


async def run(args):
    fixture = read_json(ROOT / args.fixture)
    config = read_baseline_config(args.config.removeprefix("baseline:")) if args.config.startswith("baseline:") else read_json(ROOT / args.config)
    core = read_json(ROOT / "data/cmd_config.json")
    source = next(p for p in core["provider_sources"] if p["id"] == "cuteq-deepseek")
    model = next(p for p in core["provider"] if p["id"] == PROVIDER_ID)
    provider = ProviderOpenAIOfficial({**source, **model}, core["provider_settings"])
    embedding_config = read_json(ROOT / "config/memory-policy.json")["embedding_provider"]
    embedding = LocalChineseEmbeddingProvider(embedding_config, core["provider_settings"])
    context = SimpleNamespace(get_provider_by_id=lambda identifier: embedding if identifier == embedding_config["id"] else provider if identifier == PROVIDER_ID else None, get_embedding_provider_by_id=lambda identifier: embedding if identifier == embedding_config["id"] else None, get_using_provider=lambda *_: provider)
    cache = ROOT / ".cache"
    directory = Path(tempfile.mkdtemp(prefix="memory-evaluation-", dir=cache)).resolve()
    assert directory.is_relative_to(cache.resolve())
    service = MemoryCompanionService(context=context, config=copy.deepcopy(config), plugin_root=PLUGIN, data_dir=directory)
    report = {"profile": args.name, "model": model["model"], "scope": "Isolated native memory service and real DeepSeek; synthetic conversations only; not QQ end-to-end", "questions": []}
    try:
        contexts = {}
        for index, item in enumerate(fixture["messages"]):
            group = item.get("group", "")
            session_id = f"cuteq_qq:GroupMessage:{group}" if group else f"cuteq_qq:FriendMessage:{item['user']}"
            ctx = SessionContext(session_id=session_id, scope="group" if group else "private", group_id=group, platform="aiocqhttp", user_id=item["user"], user_name=item.get("name", "虚构验收用户"), bot_id=BOT, persona_id="dafeiyu")
            contexts[session_id] = ctx
            await service.store.add_timeline_event(event_type="user_message", session_id=ctx.session_id, scope=ctx.scope, subject_id=ctx.user_id, object_id=ctx.current_target_id, content=item["text"], occurred_at=item["at"], metadata={"owner_bot_id": BOT, "bot_id": BOT, "platform": ctx.platform, "persona_id": ctx.persona_id, "group_id": group, "sender_name": ctx.user_name, "message_id": f"synthetic-{index}"})
        for ctx in contexts.values():
            summary = await service.maybe_summarize_session(ctx, force=True)
            assert summary, "Synthetic conversation was not summarized"
        report["semantic_indexes"] = await prepare_indexes(service, embedding, list(contexts.values()))
        report["retention"] = await service._run_raw_event_retention()
        print(json.dumps({"stage": "seeded_and_maintained", "profile": args.name, "timeline_deleted": report["retention"]["timeline_deleted"]}), flush=True)
        # Reopen storage before the questions: no short-lived in-memory context.
        await service.aclose()
        service = MemoryCompanionService(context=context, config=copy.deepcopy(config), plugin_root=PLUGIN, data_dir=directory)
        normalize_source_projection(service)
        persona = load_persona_prompt()
        async with AsyncOpenAI(api_key=source["key"][0], base_url=source["api_base"], max_retries=0, timeout=120) as client:
            for question in fixture["questions"]:
                result = await answer(client, model["model"], service, config, question, persona)
                report["questions"].append(result)
                write_json(ROOT / args.output, report)
                print(json.dumps({"id": result["id"], "passed": result["passed"], "seconds": result["seconds"]}), flush=True)
        for user, group in (("100000102", ""), ("100000101", "200000101")):
            event = event_for(user, "阿岚押金1800元和厨房蓝色饼干盒", group)
            result = await service.tool_sources(event, action="search", terms=["押金", "饼干盒"], limit=8)
            assert result["ok"] and not result["sources"], "A different private chat or a group could read the private sources"
        report["private_source_isolation"] = True
        report["passed"] = sum(item["passed"] for item in report["questions"])
        report["total"] = len(report["questions"])
        write_json(ROOT / args.output, report)
        print(json.dumps({"profile": args.name, "passed": report["passed"], "total": report["total"], "private_source_isolation": True}), flush=True)
    finally:
        await service.aclose()
        await provider.client.close()
        await embedding.terminate()
        # Caller may remove this isolated fixture directory after inspecting the report.
        print(json.dumps({"synthetic_database_directory": str(directory)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare synthetic long-term memory using the installed native plugin and real DeepSeek.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixture", default="tests/fixtures/personal-memory.json")
    asyncio.run(run(parser.parse_args()))
