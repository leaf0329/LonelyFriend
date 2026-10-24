from __future__ import annotations

import asyncio
import json

from configure import ROOT, PROVIDER_ID, apply_context_policy, read_json, write_json
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.message import Message
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial


async def main():
    config = read_json(ROOT / "data/cmd_config.json")
    apply_context_policy(config)
    model = next(p for p in config["provider"] if p["id"] == PROVIDER_ID)
    source = next(p for p in config["provider_sources"] if p["id"] == model["provider_source_id"])
    provider = ProviderOpenAIOfficial({**source, **model}, config["provider_settings"])
    messages = [Message(role="system", content="这是隔离的虚构对话验收，只根据对话事实回答。"), Message(role="user", content="我给虚构盆栽起的名字是青柚回声。"), Message(role="assistant", content="这个名字记在这次虚构对话里了。")]
    for index in range(90):
        messages.extend([Message(role="user", content=f"第{index}段闲聊：我今天在楼下散了步，回来继续整理书架，没有新的个人事实。"), Message(role="assistant", content="慢慢整理，先把常看的几本放在顺手的位置。")])
    messages.append(Message(role="user", content="最早那盆虚构盆栽叫什么？只返回名字。"))
    policy = config["agent_runner"]["config"]["compression"]
    manager = ContextManager(ContextConfig(max_context_tokens=1, enforce_max_turns=policy["max_turns"], llm_compress_keep_recent_ratio=policy["keep_recent_ratio"], llm_compress_instruction=policy["instruction"], llm_compress_provider=provider))
    before = manager.token_counter.count_tokens(messages)
    # Scale only this isolated request's budget to exercise compression cheaply.
    manager.config.max_context_tokens = before
    try:
        compressed = await manager.process(messages)
        after = manager.token_counter.count_tokens(compressed)
        assert after < before
        assert compressed[-1].content == messages[-1].content
        response = await provider.text_chat(contexts=compressed, request_max_retries=1)
        assert "青柚回声" in response.completion_text
        report = {"model": model["model"], "synthetic_rounds": 92, "estimated_tokens_before": before, "estimated_tokens_after": after, "latest_request_preserved": True, "earliest_fact_recalled_after_real_compression": True, "reply": response.completion_text, "production_context_budget": model["max_context_tokens"], "production_trigger_estimated_tokens": int(model["max_context_tokens"]*0.82), "note": "Small synthetic fixture exercises the actual compressor; no million-token load test."}
        write_json(ROOT / "docs/evaluations/context-compression.json", report)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await provider.client.close()


if __name__ == "__main__":
    asyncio.run(main())
