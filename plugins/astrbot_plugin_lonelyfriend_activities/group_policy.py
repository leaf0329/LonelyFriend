from __future__ import annotations

import re

from astrbot.api.message_components import At, Plain, Reply


def bot_mentioned(event) -> bool:
    return any(isinstance(part, At) and str(part.qq) == str(event.get_self_id())
               for part in event.get_messages())


def bot_addressed(event) -> bool:
    return bot_mentioned(event) or any(
        isinstance(part, Reply) and str(part.sender_id) == str(event.get_self_id())
        for part in event.get_messages())


def group_voice_requested(event) -> bool:
    if not bot_mentioned(event):
        return False
    # Only the new message counts; quoted text must not opt someone into speech.
    text = "".join(part.text for part in event.get_messages() if isinstance(part, Plain))
    text = text.replace("能不能", "能").replace("可不可以", "可以")
    if re.search(r"(?:不要|不用|不必|无需|不需要|不准|禁止|不能|别)(?:再|你|给我|帮我|用|发|来|以|通过|使用|回复|回答|发送|一条|一段|条|段|个|\s)*语音", text):
        return False
    return re.search(
        r"(?:^|[，,。.!！?？；;\n])\s*"
        r"(?:(?:请|麻烦|帮我|给我|你|能|能否|可以|这次|现在|直接|就|再|先|也)\s*)*"
        r"(?:(?:用|以|通过)(?:一(?:条|段))?语音|语音(?:回复|回答|回我)|(?:发|来)(?:给我)?一?(?:条|段|个)?语音)",
        text,
    ) is not None
