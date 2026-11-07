"""宿主适配层单测：Provider 网关把空响应当成空文本，不抛 AttributeError。"""
import asyncio
import types

from data.plugins.astrbot_plugin_star_feather.adapters.llm import AstrBotProviderGateway


class _Handle:
    def __init__(self, resp):
        self._resp = resp

    async def text_chat(self, **_kwargs):
        return self._resp


def _gateway():
    return AstrBotProviderGateway(types.SimpleNamespace())


def test_chat_treats_missing_response_as_empty_text():
    assert asyncio.run(_gateway().chat(_Handle(None), "prompt", "system")) == ""


def test_chat_treats_non_string_completion_as_empty_text():
    resp = types.SimpleNamespace(completion_text=None)
    assert asyncio.run(_gateway().chat(_Handle(resp), "prompt", "system")) == ""
