"""发送编排：解读文本分段、结构化标记切分与结果分发。

抽牌与牌面（core.draw）只关心「抽到什么牌、解读说什么」；
结果如何呈现（分段 / 合并转发 / 免责声明）全部收敛于此。
——牌是占出来了，怎么端到你面前，本羽说了算。
"""
from astrbot.api import logger
from astrbot.api.message_components import Image, Node, Nodes, Plain

from ..ai.prompts import SECTION_MARK_RE
from ..core.models import Interpretation
from ..core.texts import FORWARD_IMAGE_LABEL

# 已验证能收合并转发（Nodes）的平台：框架源码里显式处理 Nodes 的只有这两个
# （aiocqhttp_message_event / satori_event，2026-10-18 核对）。WebChat 的 _send
# 只认 Plain / Json / Image / Record / File，Nodes 落进 else 被静默丢掉；
# Telegram / Discord / WeCom AI bot 也没有 Nodes 分支。默认 send_mode=forward
# 在这些平台上等于「什么都不发」，所以判据取白名单：未知平台一律回退普通链消息，
# 宁可少一个合并转发，也别让用户一条结果都看不到。新增适配器验证过后再加进来。
_FORWARD_SUPPORTED_PLATFORMS = frozenset({"aiocqhttp", "satori"})
_WARNED_FORWARD_PLATFORMS: set[str] = set()


def supports_forward(event) -> bool:
    """该事件所在平台能不能收 Nodes（合并转发）。

    判据取白名单，且**取不到平台名时按不支持处理**：普通链消息在任何适配器都送得出去，
    而未知平台发 Nodes 可能被静默丢掉——诊断字段缺失（空串 / 事件桩 / 老版本事件）
    不该赌用户能不能看到结果。非白名单平台回退普通消息，并在首次遇到时留一条 warning
    ——静默降级正是这条要修的毛病（用户配着默认值却看不到结果，日志里也没有一句解释）。
    """
    try:
        name = str(event.get_platform_name() or "").strip().lower()
    except Exception:
        name = ""
    if name in _FORWARD_SUPPORTED_PLATFORMS:
        return True
    if not name:
        return False
    if name not in _WARNED_FORWARD_PLATFORMS:
        _WARNED_FORWARD_PLATFORMS.add(name)
        logger.warning(
            f"平台 {name} 未验证支持合并转发（Nodes），结果改按普通消息发送；"
            "已验证支持的是 aiocqhttp / satori，要固定行为可把 output.send_mode 设为 plain")
    return False


def split_text(text: str, size: int) -> list[str]:
    """无标记文本按长度切分：先清理首尾空白，再优先在换行处断开；拼接可还原清理后的文本。"""
    text = (text or "").strip()
    if not text:
        return []
    if size <= 0:
        # 非法尺寸防御：整体单段返回，绝不进入 while 死循环（正常配置被 settings floor 保护）
        return [text]
    parts = []
    while len(text) > size:
        cut = text[:size].rfind("\n")
        if cut <= 0:  # 无换行或换行在最前：硬切
            parts.append(text[:size])
            text = text[size:]
        else:  # 在换行处断开（含换行符），保证拼接可还原
            parts.append(text[: cut + 1])
            text = text[cut + 1:]
    return parts + [text]


def split_sections(text: str, fallback_size: int) -> list[str]:
    """按【第N张·位置】/【总结】标记切分为结构化段落（不依赖换行）；无标记退回长度切分。

    标记前的前言（如“你好，你的解读如下：”）会保留为第一条消息，不丢弃。
    """
    text = (text or "").strip()
    if not text:
        return []
    segs = SECTION_MARK_RE.split(text)
    if len(segs) < 3:  # segs[0] 为标记前前言；不足两个标记段则按长度兜底
        return split_text(text, fallback_size)
    output = []
    preamble = segs[0].strip()
    if preamble:
        output.append(preamble)
    output.extend(seg.strip() for seg in segs[1:] if seg.strip())
    return output


class Deliverer:
    """结果分发器：统一「AI 成功 → 结构化发送；失败 → 图片/文字兜底」。"""

    def __init__(self, segment_size: int, disclaimer: str, forward_result: bool):
        """三段参数就是全部决策：每段多长、末尾挂什么免责、要不要合并转发。

        forward_result 由调用方从 send_mode 解析成 bool（send_mode == "forward"），
        本类不关心配置长什么样——给什么用什么，坏不了。
        """
        self.segment_size = segment_size
        self.disclaimer = disclaimer
        self.forward_result = forward_result

    def forward_for(self, event) -> bool:
        """本次事件实际走不走合并转发：配置开了 + 平台能收 Nodes。"""
        return self.forward_result and supports_forward(event)

    async def deliver(self, event, interp: str | None, img: str | None,
                      formation: str, positions: list[str], picks: list[dict],
                      plain_text, fail_note: str = "", preface: str = ""):
        """发送占卜结果。plain_text: callable(formation, positions, picks) -> str，
        生成无 AI 解读时的本地牌义兜底文案。
        preface: 牌面之后的独立前置行（正常路径是「牌灵的话」；亦承接洗牌提示
        独立发送失败时的降级并入），排在图片之后、解读段之前。产出统一为单次
        yield——两个入口的消费方式不同：工具入口只取最后一个 yield 的值
        （asyncgen 取末值），命令入口则在 handlers.command_entry 里 await event.send 直发；
        单次 yield 保证两边都只发一份结果。
        收尾句不带在这里：命令入口在 ReadingService.run 之后独立直发（不入合并转发），
        工具入口收尾由 Agent Loop 的 LLM 回复承担——本分发器只发结果本体。
        """
        if not interp:
            chain = []
            if img:
                chain.append(Image(file=img))
            if preface:
                chain.append(Plain(preface))
            if not img:
                # 独立 if 而非 elif：preface 恒非空时（spirit_cached 在 picks 非空必返非空），
                # elif 分支在生产路径不可达 → send_mode=text_only + AI 失败时逐牌牌义整段丢失
                # 只补 text_only / 图片渲染失败：图片模式有卡面两行关键词，再叠完整牌义会抹平
                # 两种模式的区别，也让用户误以为「AI 其实说了、只是排版乱」；想看逐张详义的
                # 请切纯文字模式（2026-09-10 定案，那是配置项的职责，不是渲染路径该兜的）。
                chain.append(Plain(plain_text(formation, positions, picks)))
            if fail_note:
                chain.append(Plain(fail_note))
            if self.disclaimer:
                chain.append(Plain(self.disclaimer))
            if chain:
                yield event.chain_result(chain)
            return
        # 结构化切分只做一次：AI 返回时已解析成 Interpretation，这里不再重新切；
        # 只有旧版字符串才回退 split_sections。随后仍按 segment_size 防止单段超长。
        if isinstance(interp, Interpretation) and interp.structured:
            segs = list(interp.sections) + ([interp.summary] if interp.summary else [])
        else:
            text = interp.text if isinstance(interp, Interpretation) else interp
            segs = split_sections(text, self.segment_size)
        parts = [sub for seg in segs for sub in split_text(seg, self.segment_size)]
        if not parts:
            return
        # 免责声明：空字符串不显示；正常解读末尾统一追加一段
        if self.disclaimer:
            parts = parts + [self.disclaimer]
        # 转发节点 uin：优先框架 get_self_id()（与项目「框架统一接口」修复口径一致），取不到时兜底 '0'
        uin = "0"
        if hasattr(event, "get_self_id"):
            uin = str(event.get_self_id() or "0")
        if self.forward_for(event):
            # 是否含图由调用方门控（text_only 模式下 img 为 None → 纯文字转发）
            # preface = 牌灵的话（正常路径）/洗牌提示降级并入；节点序：牌面 → preface → 解读段
            nodes = []
            if img:
                nodes.append(Node(content=[Image(file=img), Plain(FORWARD_IMAGE_LABEL)], name="星羽塔罗", uin=uin))
            if preface:
                nodes.append(Node(content=[Plain(preface)], name="星羽塔罗", uin=uin))
            nodes.extend(Node(content=[Plain(seg)], name="星羽塔罗", uin=uin) for seg in parts)
            yield event.chain_result([Nodes(nodes)])
        else:
            # 非转发模式：内容合并进一条链消息（逐段 yield 在工具入口那侧会被丢掉——
            # 那边取末值，只看最后一个 yield）
            chain = []
            if img:
                chain.append(Image(file=img))
            if preface:
                chain.append(Plain(preface + "\n"))
            for i, seg in enumerate(parts):
                tail = "" if i == len(parts) - 1 else "\n"
                chain.append(Plain(seg + tail))
            yield event.chain_result(chain)

