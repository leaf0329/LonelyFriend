from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from astrbot.core.provider.provider import Provider
from data.plugins.astrbot_plugin_qzone.core.llm_action import LLMAction


class QzonePublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_post_without_chat_material_does_not_read_groups(self):
        provider = Mock(spec=Provider)
        provider.text_chat = AsyncMock(return_value=SimpleNamespace(
            completion_text='"""今天很适合慢慢发呆。"""'
        ))
        client = SimpleNamespace(get_group_list=AsyncMock(
            side_effect=AssertionError("Public posts must not enumerate groups")
        ))
        config = SimpleNamespace(
            context=SimpleNamespace(), client=client,
            source=SimpleNamespace(post_max_msg=0),
            llm=SimpleNamespace(post_provider_id="test", post_prompt="公开的小想法"),
        )
        action = LLMAction(config)
        with patch.object(action, "_get_provider", return_value=provider):
            text = await action.generate_post(topic="当下心情")
        self.assertEqual(text, "今天很适合慢慢发呆。")
        client.get_group_list.assert_not_awaited()
        self.assertEqual(provider.text_chat.await_args.kwargs["contexts"], [])


if __name__ == "__main__":
    unittest.main()
