"""pytest 启动准备：打桩 astrbot 框架 API，使插件模块可在无 AstrBot 环境下导入。

测试按域分文件：test_core（核心与三入口集成）/ test_settings（配置语义）/
test_spreads（选阵与清洗）/ test_hardening（Prompt 防护）/ test_identity（身份标识）/
test_gating（限流闸门）/ test_log_setup（运行日志）/ test_card_render（渲染与清理）/
test_fonts（字体子系统）/ test_deliver（分段与分发）/ test_limiter / test_config，
共享桩（FakeProvider / FakeContext / 牌常量）在 stubs.py。
conftest 只提供最小名字桩：@register / @command / @llm_tool 装饰器与消息组件类
（At / Plain / Node 等），不需要模拟行为；事件对象与插件实例另由各测试文件构造。

说人话：没有 AstrBot 的机器上，靠这些桩把戏把插件骗起来跑测试——本羽的牌灵得有个地方练手。
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
    import fonts
    fonts._FONT_CACHE.clear()
    yield
    fonts._FONT_CACHE.clear()


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
    def __init__(self, content=None, **kwargs):
        self.content = content or []
        for k, v in kwargs.items():
            setattr(self, k, v)


class Nodes:
    def __init__(self, nodes=None, **kwargs):
        self.nodes = nodes or []
        for k, v in kwargs.items():
            setattr(self, k, v)


class Plain:
    def __init__(self, text=""):
        self.text = text


class MessageChain:
    """stub：仅记录链内容，供测试断言（真实框架在 astrbot.api.event 导出）。"""

    def __init__(self, chain=None, **kwargs):
        self.chain = list(chain or [])
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __iter__(self):
        return iter(self.chain)


def register(*args, **kwargs):
    def deco(cls):
        return cls
    return deco


def command(*args, **kwargs):
    def deco(fn):
        return fn
    return deco


def llm_tool(*args, **kwargs):
    def deco(fn):
        return fn
    return deco


class _Filter:
    llm_tool = staticmethod(llm_tool)


_mk_module("astrbot")
_mk_module("astrbot.api")
_mk_module("astrbot.api.all", Context=Context, AstrBotConfig=AstrBotConfig,
           Star=Star, register=register, command=command)
_mk_module("astrbot.api.event", AstrMessageEvent=AstrMessageEvent,
           MessageChain=MessageChain, filter=_Filter())
_mk_module("astrbot.api.message_components", At=At, Image=Image,
           Node=Node, Nodes=Nodes, Plain=Plain)
