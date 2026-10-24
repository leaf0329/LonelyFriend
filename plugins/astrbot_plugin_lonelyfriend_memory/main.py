from __future__ import annotations

from datetime import datetime, timezone

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register


async def capture_visible_text(service, event: AstrMessageEvent, persona_id: str):
    if event.is_stopped():
        return None
    existing = getattr(event, "_memory_capture_receipt", None)
    if existing is not None:
        return existing
    event.persona_id = persona_id
    ctx = await service.identity.resolve_event_context(event)
    message = event.message_obj
    text = "".join(part.text for part in message.message if isinstance(part, Plain))
    if not text:
        return None
    capture = service.capture
    identity = capture.identity(ctx, direction="incoming", message=str(message.message_id))
    source_time = datetime.fromtimestamp(message.timestamp, timezone.utc).isoformat() if message.timestamp else ""
    # The pinned source API stores full redacted text and chunks in one transaction.
    receipt = capture.submit(identity, {"body": text, "source_message_at": source_time, "observed_at": datetime.now(timezone.utc).isoformat(), "evidence": "observed"}, authorize=lambda: not event.is_stopped())
    event._memory_capture_receipt = receipt
    if receipt["status"] != "committed" or receipt["source_state"] != "current":
        raise RuntimeError("LonelyFriend original-message storage did not commit.")
    return receipt


@register("astrbot_plugin_lonelyfriend_memory", "leaf0329", "获准 QQ 会话的完整原文保存", "0.1.0")
class LonelyFriendMemory(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def _capture(self, event: AstrMessageEvent, request: ProviderRequest | None = None):
        if event.get_platform_name() != "aiocqhttp" or event.get_platform_id() != "cuteq_qq":
            return
        metadata = next((star for star in self.context.get_all_stars() if star.root_dir_name == "astrbot_plugin_memory_companion" and star.activated), None)
        if metadata is None or metadata.star_cls is None:
            raise RuntimeError("MemoryCompanion must be enabled for full original-message storage.")
        if metadata.version != "2.3.1":
            raise RuntimeError("The original-message adapter supports MemoryCompanion 2.3.1; verify compatibility before upgrading.")
        service = metadata.star_cls.service
        if not await service.initialize_database():
            raise RuntimeError("MemoryCompanion storage is unavailable.")
        conversation_persona = request.conversation.persona_id if request and request.conversation else None
        persona_id, _, _, _ = await self.context.persona_manager.resolve_selected_persona(umo=event.unified_msg_origin, conversation_persona_id=conversation_persona, platform_name=event.get_platform_name())
        try:
            await capture_visible_text(service, event, persona_id or "")
        except (ValueError, UnicodeError) as exc:
            logger.error("LonelyFriend 原文未保存，消息格式或大小超出原文接口边界: %s", type(exc).__name__)
            raise

    @filter.on_llm_request(priority=100)
    async def on_llm_request(self, event: AstrMessageEvent, request: ProviderRequest):
        await self._capture(event, request)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1100)
    async def on_group_message(self, event: AstrMessageEvent):
        await self._capture(event)
