"""三个入口的编排：意图 → 限流 → 占卜服务 → 呈现。

命令入口与工具入口的差别只在收尾句出口：
- 命令入口由 Presenter 直发；
- 工具入口 Presenter 直发结果后，入口返回一个字符串给 Agent Loop 生成收尾。
"""
import random

from ..adapters.astrbot_io import messenger_for
from ..ai.prompts import FAIL_NOTE_TOOL, HELP_TEXT, TOOL_EPILOGUE
from ..core.log import log
from ..core.models import KIND_HELP
from ..identity import resolve_sender_uid
from ..intent import build_request
from ..services.reading import ReadingContext
from .diagnostics import note_entry_binding

EMPTY_FALLBACK = "（未提具体问题，请作一般运势解读）"


def _note(plugin, entry: str) -> None:
    """走插件上的接线点（测试可替换），没有就退回诊断模块的默认实现。"""
    hook = getattr(plugin, "_note_entry_binding", None)
    if callable(hook):
        hook(entry)
    else:
        note_entry_binding(plugin, entry)


def help_text(version: str) -> str:
    return f"🔮 星羽塔罗 {version}\n{HELP_TEXT}"


async def command_entry(plugin, event, text: str, *, version: str, err_tpl: str,
                        intent_kind: str = "auto", fail_note: str = "",
                        empty_fallback: str = EMPTY_FALLBACK) -> None:
    """命令入口公共流程（/占卜、/单抽、/今日牌运 共用）。"""
    # 本次事件已由插件处理：should_call_llm(True) 的语义是「禁止默认 LLM」。
    # 不用 stop_event()：响应阶段清空 result 后会塞出空 STOP 结果。
    _note(plugin, "command")
    event.should_call_llm(True)
    container = plugin.container
    messenger = messenger_for(event)
    try:
        request = build_request(
            intent_kind, text, daily_enabled=container.settings.output.daily_fixed)
        if request.kind == KIND_HELP:
            await messenger.send_text(help_text(version))
            return
        block = await container.gate.check(event, for_command=True)
        if block:
            await messenger.send_text(block)
            return
        uid = resolve_sender_uid(event)
        prepared = await container.reading.prepare(request, uid)
        reading = await container.reading.run(
            prepared, ReadingContext(uid, getattr(event, "unified_msg_origin", None),
                                     messenger),
            question=request.question, fail_note=fail_note,
            empty_fallback=empty_fallback, epilogue=True)
        await container.presenter.present(reading, messenger, event)
    except Exception as e:
        log.error(f"命令占卜失败: {e}", exc_info=True)
        # 异常细节只进日志：err_tpl 是纯友好文案
        await messenger.send_text(err_tpl)


async def tool_entry(plugin, event, formation: str = "", *, version: str) -> str:
    """自然语言入口：返回给 Agent Loop 的一句收尾引导。"""
    _note(plugin, "tool")
    container = plugin.container
    messenger = messenger_for(event)
    if not container.settings.tool.llm_enabled:
        await messenger.send_text("星羽塔罗的自然语言占卜未开启～ 试试 /占卜 感情 这样的命令。")
        return "已告知用户自然语言占卜未开启。请自然回应一句，不必再调用工具。"
    text = (getattr(event, "message_str", "") or "").strip()
    request = build_request(
        "auto", text, daily_enabled=container.settings.output.daily_fixed,
        source="tool", formation_hint=formation)
    if request.kind == KIND_HELP:
        await messenger.send_text(help_text(version))
        return "帮助页已发送。请自然回应一句，不必再调用工具。"
    if not text:
        return ("工具收到空文本：用户没说占卜什么。请回一句，引导用户说出想占卜的内容"
                "（如感情、事业、学业、今日运势）。")
    umo = getattr(event, "unified_msg_origin", None) or "global"
    remain = await container.gate.session_throttle(
        f"sf_tool_cd_{umo}", container.settings.tool.llm_cooldown)
    if remain > 0:
        await messenger.send_text(
            f"牌灵刚忙完一卦，让它歇 {remain} 秒，或者用 /占卜 再来一卦~")
        return f"已提示用户节流中（剩 {remain} 秒）。请自然回应一句，不必再调用工具。"
    block = await container.gate.check(event, for_command=False)
    if block:
        await messenger.send_text(block)
        return "已提示用户今日次数限制。请自然回应一句，不必再调用工具。"
    # 收尾引导放在所有早退分支之后取：早退路径不该白耗一次全局随机状态。
    note = random.choice(TOOL_EPILOGUE)
    try:
        uid = resolve_sender_uid(event)
        prepared = await container.reading.prepare(request, uid)
        reading = await container.reading.run(
            prepared, ReadingContext(uid, getattr(event, "unified_msg_origin", None),
                                     messenger),
            question=request.question, fail_note=FAIL_NOTE_TOOL, epilogue=False)
        await container.presenter.present(reading, messenger, event)
        return note
    except Exception as e:
        log.error(f"自然语言占卜失败: {e}")
        await messenger.send_text("这卦起得有点乱……牌灵需要缓缓，换个时候再来问。")
        return note
