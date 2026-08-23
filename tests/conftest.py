"""pytest 启动准备：打桩 astrbot 框架 API，使 main.py 可在无 AstrBot 环境下导入。

单测只覆盖 main.py / card_render.py 中的纯逻辑（选阵、剥离、分段、抽牌、字体覆盖），
不涉及框架事件对象，因此 stub 只需提供名字，无需模拟行为。
"""
import os
import sys
import types

import pytest

_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


@pytest.fixture(autouse=True)
def _isolate_font_cache():
    """每个用例前后清空字体缓存，避免覆盖检测用例间互相污染。"""
    import card_render
    card_render._FONT_CACHE.clear()
    yield
    card_render._FONT_CACHE.clear()


def _mk_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class Context:
    pass


class AstrBotConfig:
    pass


class Star:
    pass


class AstrMessageEvent:
    pass


class At:
    def __init__(self, qq="0"):
        self.qq = qq


class Image:
    pass


class Node:
    pass


class Nodes:
    pass


class Plain:
    pass


def register(*args, **kwargs):
    def deco(cls):
        return cls
    return deco


def command(*args, **kwargs):
    def deco(fn):
        return fn
    return deco


_mk_module("astrbot")
_mk_module("astrbot.api")
_mk_module("astrbot.api.all", Context=Context, AstrBotConfig=AstrBotConfig,
           Star=Star, register=register, command=command)
_mk_module("astrbot.api.event", AstrMessageEvent=AstrMessageEvent)
_mk_module("astrbot.api.message_components", At=At, Image=Image,
           Node=Node, Nodes=Nodes, Plain=Plain)
