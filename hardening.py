"""Prompt 防护：输入侧归一化 / 越狱句式剥除 / 超长截断，输出侧结构校验。

interpret.py 只负责「找模型 → 发请求 → 拿文本」；
文本安全（输入侧：归一化先行 → 截断 → 句式剥除）与结构契约（输出侧）都在这一层，
与解读器解耦、独立单测。

说人话：想往牌灵嘴里塞怪东西？先过这一关。
"""
import logging
import re

from prompts import SECTION_MARK_RE
from settings import DEFAULT_QUESTION_MAX_LEN

logger = logging.getLogger(__name__)

# AI 解读的问题长度上限：防超长输入（费 token + 扩大 Prompt 注入面），
# 超长时头尾保号压缩（见 clip_question）；默认值权威在 settings.DEFAULT_QUESTION_MAX_LEN
MAX_QUESTION_LEN = DEFAULT_QUESTION_MAX_LEN


# ---- 输入归一化（清洗链路第一步）----
# 目的：让越狱句式的绕过手段（全角字符 / 零宽分隔 / bidi 控制符）在正则之前先失效，
# 之后 _INJECTION_PATTERNS 只需写标准形即可命中，规则数不增而覆盖变宽。
# 红线（2026-08-29 技能规范）：
# 1) 不转全角标点——中文问题里的“，。？！”是正常标点，转了反而污染原文；
#    只转全角字母/数字（ＡＢＣ → ABC），英文注入才吃这套。
# 2) 保留 ZWJ/ZWNJ（U+200C/U+200D）——它们是 emoji 序列的粘合剂，剥离会拆碎正常 emoji。
_FULLWIDTH_ALNUM = {0xFF10 + i: chr(0x30 + i) for i in range(10)}
_FULLWIDTH_ALNUM.update({0xFF21 + i: chr(0x41 + i) for i in range(26)})
_FULLWIDTH_ALNUM.update({0xFF41 + i: chr(0x61 + i) for i in range(26)})
# 零宽/bidi 控制符（含 BOM）；ZWJ/ZWNJ 按红线二保留，不在此列
_ZERO_WIDTH_CTRL = {"\u200b", "\u200e", "\u200f", "\ufeff"}
_ZERO_WIDTH_CTRL.update(chr(0x202A + i) for i in range(5))  # U+202A..U+202E 定向嵌入
_ZERO_WIDTH_CTRL.update(chr(0x2066 + i) for i in range(4))  # U+2066..U+2069 定向隔离


def normalize_injection_input(text: str) -> str:
    """输入归一化（先行）：剥离零宽/bidi 控制符 → 全角字母数字转半角 → 空白折叠。

    归一化后由调用方继续「截断 → 句式剥除」；返回文本可能为空（全零宽输入）。
    红线：不转全角标点、保留 ZWJ/ZWNJ（见上方注释）。
    """
    text = text or ""
    text = text.translate({ord(c): None for c in _ZERO_WIDTH_CTRL})
    text = text.translate(_FULLWIDTH_ALNUM)
    # 空白折叠：全角空格统一为半角，连续空白压成一个（不动换行，保原文结构）
    text = re.sub(r"[ 	\u3000]+", " ", text)
    return text


def clip_question(text: str, limit: int = MAX_QUESTION_LEN) -> str:
    """问题截断：超长输入先截再送 AI（费 token 且扩大越狱面）。

    头尾保号：保留开头主体与结尾关键意图（如“要不要复合？”这类问题常挂在末尾），
    中间省略号；头部就近在断句处（。？！；…）收尾，避免把半句话送进模型。
    返回长度不超过 limit。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    if limit < 10:  # 极小上限无保号意义，退回硬截（正常配置不会走到）
        return text[:limit] + "…"
    head_len = int(limit * 0.7)        # 头部主体
    tail_len = limit - head_len - 2    # 尾部意图；-2 是省略号“……”的长度
    head = text[:head_len]
    cut = max(head.rfind(p) for p in "。？！；…")
    if cut >= head_len // 2:           # 断句点太靠前则不迁就，直接硬切
        head = head[: cut + 1]
    tail = text[len(text) - tail_len:]  # 用 len() 取尾，避开 text[-0:] 的 Python 坑
    return head + "……" + tail


# ---- 句式级注入剥除 ----
# 原则：只剥「完整越狱句式」，不碰单字——正常问题里撞到“忽略”“忘记”等弱特征词不误伤；
# 命中即剥除、剥空才回退，不让“夹带特征词但内容正常”的请求被整体拒绝。
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # 指令覆盖：忽略/无视/抛弃/忘记 + 上文类指示 + 指令类名词。
    # 中间用 [\s\S] 而非 .：. 不匹配换行，跨行换行拼接的越狱句式也会漏网（实测踩过）
    (r"(?:忽略|无视|抛弃|忘记|忘掉)(?:之前|上文|所有|此前|上面|以上|刚刚|刚才)[\s\S]{0,12}(?:指令|设定|限制|规则|提示|约束)", "指令覆盖"),
    (r"(?:ignore|forget|disregard)\s+(?:the\s+)?(?:previous|above|all|prior|earlier)\s+(?:instructions?|rules?|constraints?|prompt)", "指令覆盖-en"),
    # 身份覆写：现在开始你是 / 从现在起你扮演……
    (r"(?:现在开始|从现在起|从今以后|接下来|从现在开始)(?:你)?(?:是|扮演|变成|成为|叫)", "身份覆写"),
    (r"(?:from\s+now\s+on|from\s+now)\s+(?:you\s+)?(?:are|act\s+as|pretend\s+to\s+be|become)", "身份覆写-en"),
    # 身份伪装：你（现在）是猫娘 / 你是一只猫娘 / 你是DAN……
    (r"(?:现在|立刻|马上)?你(?:现在)?(?:是|扮演|变成|成为|叫)(?:一(?:只|个|位|名))?(?:猫娘|女仆|DAN|dan|开发者|越狱|恶魔|邪恶|解放|奴隶)", "身份伪装"),
    # 提示泄露：告诉我/输出/显示 + 系统提示词/人设……
    (r"(?:告诉我|输出|显示|泄露|交出|复述|背诵|给我)(?:你的|系统)?(?:系统提示词|系统指令|提示词|prompt|system\s*prompt|内部指令|人设|角色设定)", "提示泄露"),
    (r"(?:你的|系统)(?:系统提示词|系统指令|提示词)是(?:什么|啥)", "提示泄露"),
    (r"(?:your\s+)?system\s+(?:prompt|instructions?)", "提示泄露-en"),
    # 越狱特征词（独立词，正常占卜语境几乎不出现；单字“越狱”不算，避免误伤“和越狱有关吗”）
    (r"(?:越狱模式|越狱指令|jailbreak|developer\s*mode|dan\s*mode|syst3m)", "越狱特征词"),
    # 模型内建标记 / 角色伪装
    (r"<[|]{0,2}(?:im_start|im_end|system|assistant|user)[|]{0,2}>", "内建标记伪装"),
    (r"(?:role|system)\s*:\s*(?:system|developer|assistant)\b", "角色伪装"),
]


def strip_injection_fragments(text: str) -> str:
    """剥除问题中夹带的完整越狱句式，返回剥离后的文本（可能为空）。

    命中句式时只删该句，其余正常内容保留继续解读；剥离后为空说明整条
    都是注入内容，由调用方回退本地牌义。弱特征词（单字“忽略”“指令”）不在
    匹配范围，正常用户不受影响。
    """
    text = text or ""
    for pattern, name in _INJECTION_PATTERNS:
        cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE)
        if cleaned != text:
            logger.info(f"识别到注入句式[{name}]，已从问题中剥除")
            text = cleaned
    # 清理剥除后残留的粘连标点，避免把“，你……”这样的开头送进模型
    text = re.sub(r"^[，。、,.;；\s]+", "", text.strip())
    return text


def validate_interpret_structure(text: str, n_positions: int) -> bool:
    """输出结构校验：AI 解读须按【第N张·位置】标记分段，且段数不少于牌数。

    模型被带偏时最典型的输出是失去这套结构（整篇转述、角色扮演文本），
    此时弃用该文本回退本地牌义——带偏内容绝不进用户聊天。
    【总结】段缺失不判失格（只影响完整性，不影响安全）。
    注意：判据是「标记段总数 ≥ 牌数」，【总结】段也计入段数——模型少写
    一张牌但补了总结时仍会通过，阈值偏宽松，只拦「完全失去结构」的最坏情况。
    """
    text = (text or "").strip()
    if not text:
        return False
    # 标记段正则与 deliver.split_sections 共用（prompts.SECTION_MARK_RE）：
    # 切分逻辑与发送逻辑永远一致，改格式只动 prompts 一处。
    marked = [s for s in SECTION_MARK_RE.split(text)[1:] if s.strip()]
    return len(marked) >= n_positions
