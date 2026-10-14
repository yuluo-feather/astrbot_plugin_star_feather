"""pytest 启动准备：打桩 astrbot 框架 API，使插件模块可在无 AstrBot 环境下导入。

测试按域分文件：test_core（核心与三入口集成）/ test_settings（配置语义）/
test_spreads（选阵与清洗）/ test_hardening（Prompt 防护）/ test_identity（身份标识）/
test_gating（限流闸门）/ test_card_render（渲染与清理）/ test_clock（日期边界：时区口径与今天唯一来源）/
test_fonts（字体子系统）/ test_deliver（分段与分发）/ test_limiter / test_config /
test_dailylines（每日签文池与确定性挑选）/ test_integrity（数据完整性域）/
test_kv_utils（KV 读写降级）/ test_fault_injection（故障注入矩阵：存储故障与降级契约）/
test_ai_budget（AI 总预算：单次占卜的 AI 花费上界与回退）/
test_structure_check（解读结构校验：三档口径行为、严格判据与影子计数分母）/
test_judgement_corpus（判定语料回归）/
test_stub_signatures（打桩与真实签名一致性）/ test_docs_consistency（测试清单对账）/
test_release_gates（发布红线：版本四件套与市场包体）/
test_config_freshness（配置新鲜度自证：进门读数与陈旧实例告警）/
test_astrbot_contract（AstrBot 契约守卫：真框架面存活，没有框架时跳过）/
test_import_model（导入模型红线：一份文件一个身份、插件根不碰 sys 机关、框架前缀清理删得干净），
共享桩（FakeProvider / FakeContext / 牌常量）在 stubs.py。

【导入约定】测试文件一律用 AstrBot 运行时的包路径（from data.plugins.
astrbot_plugin_star_feather.daily import ...）——那是插件模块唯一的身份，包链由本文件
在收集前搭好（见 _install_plugin_package）。别退回「插件目录进 sys.path + 顶层名」：
同一个文件会因此出现两份模块对象，补丁打空、断言比错对象，全是静默假绿的形状
（2026-10-12 把插件侧改成包内相对导入，测试侧跟着换成了包路径）。

conftest 只提供最小名字桩：@register / @command / @llm_tool 装饰器与消息组件类
（At / Plain / Node 等），不需要模拟行为；事件对象与插件实例另由各测试文件构造。

说人话：没有 AstrBot 的机器上，靠这些桩把戏把插件骗起来跑测试——本羽的牌灵得有个地方练手。
"""
import importlib.machinery
import logging
import os
import sys
import types

import pytest

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 插件在框架里的真实包路径（AstrBot 就是这么加载它的）。测试替它把包链搭起来：
# 插件内模块之间是包内相对导入（from .daily import ...），脱离包就导不动；
# 而「把插件目录塞进 sys.path + 顶层名导入」那套老办法会让同一个文件出现两个模块
# 身份（daily 与 data.plugins....daily），补丁打在哪个上全看运气，静默假绿。
_PLUGIN_PKG = "data.plugins.astrbot_plugin_star_feather"


def _install_plugin_package(pkg: str = _PLUGIN_PKG, path: str = _PLUGIN_DIR) -> None:
    """把插件目录挂成 `data.plugins.<目录名>`（namespace 包），并接好父级属性链。

    父级（data、data.plugins）没有真实目录可依，就自己造——它们只是命名空间，
    不承载内容。属性链也要显式接上：`import data.plugins.X.daily` 靠父级属性解析，
    而已经预注册在 sys.modules 里的模块不会被 import 机制自动挂到父模块上。
    """
    parts = pkg.split(".")
    parent = None
    for i, part in enumerate(parts):
        name = ".".join(parts[:i + 1])
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            mod.__path__ = [path] if i == len(parts) - 1 else []
            mod.__package__ = name
            mod.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=True)
            sys.modules[name] = mod
        if parent is not None:
            setattr(parent, part, mod)
        parent = mod


_install_plugin_package()


@pytest.fixture(autouse=True)
def _isolate_font_cache():
    """每个用例前后清空字体缓存，避免覆盖检测用例间互相污染。

    三处都清：_FONT_CACHE（size,bold → 字体）、_FONT_CMAP（path → cmap|None，
    含「无法解析」的负结果缓存）、_load_static_cmap 的 functools 缓存
    （charsets.json 查找结果）。
    _font_candidates 不清：它只依赖 bold 参数、内容是平台路径常量，
    不随用例变化（清了对结果无影响，属无意义操作）。
    """
    from data.plugins.astrbot_plugin_star_feather import fonts
    fonts._FONT_CACHE.clear()
    fonts._FONT_CMAP.clear()
    fonts._load_static_cmap.cache_clear()
    yield
    fonts._FONT_CACHE.clear()
    fonts._FONT_CMAP.clear()
    fonts._load_static_cmap.cache_clear()


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
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


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


# 框架 logger 桩：真框架里 astrbot.api.logger 是按插件路由的 logger 代理，运行时插件全走它。
# 这里给一个同形的真 logging.Logger（名字按插件路由后的形态取），caplog 照旧收得到记录。
_FRAMEWORK_LOGGER = logging.getLogger("astrbot.plugin.astrbot_plugin_star_feather")

_mk_module("astrbot")
_mk_module("astrbot.api", logger=_FRAMEWORK_LOGGER)
_mk_module("astrbot.api.all", Context=Context, AstrBotConfig=AstrBotConfig,
           Star=Star, register=register, command=command)
_mk_module("astrbot.api.event", AstrMessageEvent=AstrMessageEvent,
           MessageChain=MessageChain, filter=_Filter())
_mk_module("astrbot.api.message_components", At=At, Image=Image,
           Node=Node, Nodes=Nodes, Plain=Plain)
