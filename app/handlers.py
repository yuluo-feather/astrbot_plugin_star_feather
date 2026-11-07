"""三个入口的编排：意图 → 限流 → 占卜服务 → 呈现。

命令入口与工具入口的差别只在收尾句出口：
- 命令入口由 Presenter 直发；
- 工具入口 Presenter 直发结果后，入口返回一个字符串给 Agent Loop 生成收尾。
"""
import random

from ..core.dto import DeliveryCapabilities, IncomingEvent
from ..core.intent import build_request
from ..core.log import log
from ..core.models import KIND_HELP
from ..core.ports import ResultMessenger
from ..core.texts import (
    FAIL_NOTE_TOOL,
    HELP_TEXT,
    TOOL_EPILOGUE,
    TOOL_FAILED_SENT,
    TOOL_SEND_FAILED,
)
from ..services.reading import ReadingContext

EMPTY_FALLBACK = "（未提具体问题，请作一般运势解读）"


def _exc_detail(exc: BaseException) -> str:
    """TaskGroup 的 ExceptionGroup 只给一句壳，日志里要展开到具体子异常。"""
    if isinstance(exc, BaseExceptionGroup):
        return " | ".join(f"{type(x).__name__}: {x}" for x in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def help_text(version: str) -> str:
    return f"🔮 星羽塔罗 {version}\n{HELP_TEXT}"


async def command_entry(container, incoming: IncomingEvent,
                        caps: DeliveryCapabilities, messenger: ResultMessenger,
                        text: str, *, version: str, err_tpl: str,
                        intent_kind: str = "auto", fail_note: str = "",
                        empty_fallback: str = EMPTY_FALLBACK) -> None:
    """命令入口公共流程（/占卜、/单抽、/今日牌运 共用）。"""
    try:
        request = build_request(
            intent_kind, text, daily_enabled=container.settings.output.daily_fixed)
        if request.kind == KIND_HELP:
            await messenger.send_text(help_text(version))
            return
        block = await container.gate.check(incoming, for_command=True)
        if block:
            # 拦截文案发不出去不阻塞返回，但至少留一条线索；tool_entry 那边会返回失败标记
            if not await messenger.send_text(block):
                log.warning(f"限流拦截文案发送失败: {block}")
            return
        uid = incoming.user_id
        prepared = await container.reading.prepare(request, uid)
        reading = await container.reading.run(
            prepared, ReadingContext(uid, incoming.conversation_id, messenger),
            question=request.question, fail_note=fail_note,
            empty_fallback=empty_fallback, epilogue=True)
        await container.presenter.present(reading, messenger, caps)
    except Exception as e:
        log.error(f"命令占卜失败: {_exc_detail(e)}", exc_info=True)
        # 异常细节只进日志：err_tpl 是纯友好文案
        await messenger.send_text(err_tpl)


async def tool_entry(container, incoming: IncomingEvent,
                     caps: DeliveryCapabilities, messenger: ResultMessenger,
                     formation: str = "", *, version: str) -> str:
    """自然语言入口：返回给 Agent Loop 的一句收尾引导。"""
    if not container.settings.tool.llm_enabled:
        if not await messenger.send_text(
                "星羽塔罗的自然语言占卜未开启～ 试试 /占卜 感情 这样的命令。"):
            return TOOL_SEND_FAILED
        return "已告知用户自然语言占卜未开启。请自然回应一句，不必再调用工具。"
    text = (incoming.message_text or "").strip()
    if not text:
        return ("工具收到空文本：用户没说占卜什么。请回一句，引导用户说出想占卜的内容"
                "（如感情、事业、学业、今日运势）。")
    # 整条业务流程一个异常出口：意图解析、限流与每日计数同样可能抛出未预期异常，
    # 漏到 Agent Loop 里用户就一句解释都收不到（命令入口从第一行就包在 try 里）。
    # 只 catch Exception：取消语义属于 BaseException，必须原样传出去。
    try:
        request = build_request(
            "auto", text, daily_enabled=container.settings.output.daily_fixed,
            source="tool", formation_hint=formation)
        if request.kind == KIND_HELP:
            # 帮助页有自己的失败语义：发送异常也只回 TOOL_SEND_FAILED，
            # 不能落进下面「占卜失败」的出口（用户问的是帮助，不是占卜）。
            try:
                sent = await messenger.send_text(help_text(version))
            except Exception as e:
                log.error(f"帮助页发送失败: {_exc_detail(e)}")
                sent = False
            if not sent:
                return TOOL_SEND_FAILED
            return "帮助页已发送。请自然回应一句，不必再调用工具。"
        remain = await container.gate.session_throttle(
            f"sf_tool_cd_{incoming.conversation_key}", container.settings.tool.llm_cooldown)
        if remain > 0:
            if not await messenger.send_text(
                    f"牌灵刚忙完一卦，让它歇 {remain} 秒，或者用 /占卜 再来一卦~"):
                return TOOL_SEND_FAILED
            return f"已提示用户节流中（剩 {remain} 秒）。请自然回应一句，不必再调用工具。"
        block = await container.gate.check(incoming, for_command=False)
        if block:
            if not await messenger.send_text(block):
                return TOOL_SEND_FAILED
            return "已提示用户今日次数限制。请自然回应一句，不必再调用工具。"
        # 收尾引导放在所有早退分支之后取：早退路径不该白耗一次全局随机状态。
        note = random.choice(TOOL_EPILOGUE)
        uid = incoming.user_id
        prepared = await container.reading.prepare(request, uid)
        reading = await container.reading.run(
            prepared, ReadingContext(uid, incoming.conversation_id, messenger),
            question=request.question, fail_note=FAIL_NOTE_TOOL, epilogue=False)
        delivered = await container.presenter.present(reading, messenger, caps)
        return note if delivered else TOOL_SEND_FAILED
    except Exception as e:
        log.error(f"自然语言占卜失败: {_exc_detail(e)}")
        # 通知本身失败不能再往外抛：入口只返回字符串，抛出去又是漏给 Agent Loop
        try:
            sent = await messenger.send_text("这卦起得有点乱……牌灵需要缓缓，换个时候再来问。")
        except Exception as send_exc:
            log.error(f"占卜失败提示发送失败: {_exc_detail(send_exc)}")
            sent = False
        return TOOL_FAILED_SENT if sent else TOOL_SEND_FAILED
