"""配置新鲜度自证：进门读数行 + 陈旧实例告警（来龙去脉见 main.py 顶部那段）。

那条「工具入口吃旧配置」孤例（2026-10-01，账在 TODO.md §3 第 6 条）三条机制全被
验倒、现象此后再没复现，代码侧没有靶子可修——于是把判据从「输出文案里有没有某句话」
换成「进门时当场读到的值」。本文件守这把哨兵还在、且不误报：

- 登记必须落在 **context** 上：模块级全局一热重载就被重置，而旧实例调用的正是旧
  模块里那份全局，两边永远对得上、什么都报不出来（尺子选错就等于白干）；
- 当前代、以及「还没登记过」两种情形都只报读数、不告警；上一代进门必须 warning，
  行里带两个 id，能直接与 [cfg] LOAD 那行对照定罪；
- 字段残缺、没有 context 时读数行不许抛——为一行诊断把整次占卜带走更亏；
- 两个入口各有一条接线守卫：把进门那行调用删掉，哨兵就名存实亡。
"""
import asyncio
import logging
import types

from main import (
    _ACTIVE_INSTANCE_ATTR,
    _CFG_FIELD_NAMES,
    StarFeatherPlugin,
    _cfg_fingerprint,
)


def _tarot(**over):
    base = {"shuffle_lines": False, "enable_ai": True,
            "ai_timeout": 5, "llm_tool_enabled": True}
    base.update(over)
    return types.SimpleNamespace(**base)


def _plugin(tarot=None):
    """绕过 __init__ 造实例：本域只碰 tarot 与 context 两样东西。"""
    p = StarFeatherPlugin.__new__(StarFeatherPlugin)
    p.tarot = _tarot() if tarot is None else tarot
    p.context = types.SimpleNamespace()
    return p


def _text(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


class TestFingerprint:
    """读数行：报的就是那四个「面板一改、用户侧立刻看得见」的开关。"""

    def test_reports_all_four_switches(self):
        line = _cfg_fingerprint(_plugin())
        for name in _CFG_FIELD_NAMES:
            assert name + "=" in line
        assert line.startswith("inst=")          # 身份在前，两个 id 便于跨行比对
        assert " tarot=" in line
        assert "shuffle_lines=False" in line
        assert "ai_timeout=5" in line

    def test_partial_tarot_does_not_raise(self):
        line = _cfg_fingerprint(_plugin(types.SimpleNamespace(shuffle_lines=True)))
        assert "shuffle_lines=True" in line
        assert "enable_ai='?'" in line           # 缺的字段留印，不当成 False

    def test_no_tarot_at_all(self):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        assert _cfg_fingerprint(p).startswith("inst=")


class TestLoadRegistration:
    """initialize 是重载后必经的一步：登记当前代 + 留一行快照。"""

    def test_registers_on_context_and_logs_load(self, caplog):
        p = _plugin()
        with caplog.at_level(logging.INFO):
            asyncio.run(p.initialize())
        assert getattr(p.context, _ACTIVE_INSTANCE_ATTR, None) is p
        assert "[cfg] LOAD" in _text(caplog)

    def test_context_less_instance_still_logs(self, caplog):
        """没有 context 不许多写一行就把启动弄挂：横幅与读数行照打。"""
        p = _plugin()
        del p.context
        with caplog.at_level(logging.INFO):
            asyncio.run(p.initialize())
        assert "[cfg] LOAD" in _text(caplog)


class TestEntryBinding:
    """进门那一行：正常只报读数，跑在上一代实例上才 warning。"""

    def test_current_generation_only_reports(self, caplog):
        p = _plugin()
        setattr(p.context, _ACTIVE_INSTANCE_ATTR, p)
        with caplog.at_level(logging.INFO):
            p._note_entry_binding("tool")
        assert "[cfg] ENTRY tool" in _text(caplog)
        assert _warnings(caplog) == []

    def test_unregistered_context_does_not_warn(self, caplog):
        """还没跑过 initialize（登记为空）不构成「旧实例」证据，不许误报。"""
        p = _plugin()
        with caplog.at_level(logging.INFO):
            p._note_entry_binding("tool")
        assert _warnings(caplog) == []

    def test_stale_instance_is_called_out(self, caplog):
        current, stale = _plugin(), _plugin()
        setattr(stale.context, _ACTIVE_INSTANCE_ATTR, current)
        with caplog.at_level(logging.INFO):
            stale._note_entry_binding("command")
        warn = _warnings(caplog)
        assert len(warn) == 1
        assert "陈旧实例" in warn[0] and "command" in warn[0]
        assert f"inst={id(stale):#x}" in warn[0]
        assert f"当前代={id(current):#x}" in warn[0]

    def test_missing_context_does_not_raise(self, caplog):
        p = _plugin()
        del p.context
        with caplog.at_level(logging.INFO):
            p._note_entry_binding("tool")
        assert "[cfg] ENTRY tool" in _text(caplog)


class TestEntriesAreWired:
    """接线守卫：两个入口都得进门自报，否则上面那把哨兵永远不会被触发。"""

    def test_command_entry_notes_binding(self):
        p = _plugin()
        seen = []
        p._note_entry_binding = seen.append

        async def block(event, for_command):
            return "BLOCK"          # 闸门直接拦下：进门那行就该已经记下了

        p.gate = types.SimpleNamespace(check=block)
        ev = types.SimpleNamespace(should_call_llm=lambda v: None,
                                   plain_result=lambda t: t)

        async def collect():
            return [r async for r in p._command_entry(ev, "", err_tpl="x")]

        asyncio.run(collect())
        assert seen == ["command"]

    def test_tool_entry_notes_binding(self):
        p = _plugin(_tarot(llm_tool_enabled=False))   # 开关关掉：进第一个分支就 return，不必走完流程
        seen = []
        p._note_entry_binding = seen.append

        async def fake_send(event, text):
            return None

        p._tool_send = fake_send

        async def collect():
            return [r async for r in p.divine_tool(types.SimpleNamespace())]

        asyncio.run(collect())
        assert seen == ["tool"]
