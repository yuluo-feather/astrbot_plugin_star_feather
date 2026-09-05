"""发送编排：解读文本分段、结构化标记切分与结果分发。

塔罗核心（tarot_core）只关心「抽到什么牌、解读说什么」；
结果如何呈现（分段 / 合并转发 / 免责声明）全部收敛于此。
——牌是占出来了，怎么端到你面前，本羽说了算。
"""
from astrbot.api.message_components import Image, Node, Nodes, Plain

from prompts import SECTION_MARK_RE


def split_text(text: str, size: int) -> list[str]:
    """无标记文本按长度切分：优先在换行处断开，拼接可还原原文。"""
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

    async def deliver(self, event, interp: str | None, img: str | None,
                      formation: str, positions: list[str], picks: list[dict],
                      plain_text, fail_note: str = "", preface: str = ""):
        """发送占卜结果。plain_text: callable(formation, positions, picks) -> str，
        生成无 AI 解读时的本地牌义兜底文案。
        preface: 牌面之后的独立前置行（正常路径是「牌灵的话」；亦承接洗牌提示
        独立发送失败时的降级并入），排在图片之后、解读段之前。框架对 asyncgen
        型工具只保留最后一个 yield 发送，本方法内容合并进单次 yield。
        收尾句不带在这里：命令入口在 _run_reading 里独立直发（不入合并转发），
        工具入口收尾由 Agent Loop 的 LLM 回复承担——本分发器只发结果本体。
        """
        if not interp:
            chain = []
            if img:
                chain.append(Image(file=img))
            if preface:
                chain.append(Plain(preface))
            elif not img:
                chain.append(Plain(plain_text(formation, positions, picks)))
            if fail_note:
                chain.append(Plain(fail_note))
            if chain:
                yield event.chain_result(chain)
            return
        # 结构化切分 + 按 segment_size 二次截断（模型可能无视 80~140 字限制），防单段超长
        parts = [sub for seg in split_sections(interp, self.segment_size)
                 for sub in split_text(seg, self.segment_size)]
        if not parts:
            return
        # 免责声明：空字符串不显示；正常解读末尾统一追加一段
        if self.disclaimer:
            parts = parts + [self.disclaimer]
        # 转发节点 uin：优先框架 get_self_id()（与项目「框架统一接口」修复口径一致），取不到时兜底 '0'
        uin = "0"
        if hasattr(event, "get_self_id"):
            uin = str(event.get_self_id() or "0")
        if self.forward_result:
            # 是否含图由调用方门控（text_only 模式下 img 为 None → 纯文字转发）
            # preface = 牌灵的话（正常路径）/洗牌提示降级并入；节点序：牌面 → preface → 解读段
            nodes = []
            if img:
                nodes.append(Node(content=[Image(file=img), Plain("\n✨ 星羽塔罗 · 牌面")], name="星羽塔罗", uin=uin))
            if preface:
                nodes.append(Node(content=[Plain(preface)], name="星羽塔罗", uin=uin))
            nodes.extend(Node(content=[Plain(seg)], name="星羽塔罗", uin=uin) for seg in parts)
            yield event.chain_result([Nodes(nodes)])
        else:
            # 非转发模式：内容合并进一条链消息（框架只发送最后一个 yield，逐段 yield 会被丢弃）
            chain = []
            if img:
                chain.append(Image(file=img))
            if preface:
                chain.append(Plain(preface + "\n"))
            for i, seg in enumerate(parts):
                tail = "" if i == len(parts) - 1 else "\n"
                chain.append(Plain(seg + tail))
            yield event.chain_result(chain)

