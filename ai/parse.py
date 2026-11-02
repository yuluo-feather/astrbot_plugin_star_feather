"""解读解析与结构校验：一次解析，产出的 Interpretation 同时供缓存与发送使用。"""
import re

from ..core.models import Interpretation
from .prompts import SECTION_MARK_RE


def parse_interpretation(text: str | None) -> Interpretation | None:
    """AI 原文 → Interpretation；空文本返回 None。

    切分只做一次：sections / summary 随对象一起产出，发送与写缓存都复用。
    """
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    segs = SECTION_MARK_RE.split(raw)
    if len(segs) < 3:
        return Interpretation(text=raw, sections=(raw,))
    parts = [s.strip() for s in segs if s.strip()]
    summary = ""
    sections = []
    for part in parts:
        if part.startswith("【总结】"):
            summary = part
        else:
            sections.append(part)
    return Interpretation(text=raw, sections=tuple(sections), summary=summary,
                          structured=True)


def interpretation_from_record(record) -> Interpretation:
    """缓存记录 → Interpretation；旧版纯文本字符串仍能读。"""
    if isinstance(record, str):
        return parse_interpretation(record) or Interpretation(text="")
    if not isinstance(record, dict):
        return Interpretation(text="")
    text = record.get("text")
    if not isinstance(text, str) or not text:
        return Interpretation(text="")
    parsed = parse_interpretation(text) or Interpretation(text="")
    sections = record.get("sections")
    summary = record.get("summary")
    structured = record.get("structured")
    return Interpretation(
        text=parsed.text,
        sections=tuple(sections) if isinstance(sections, list)
        and all(isinstance(s, str) for s in sections) else parsed.sections,
        summary=summary if isinstance(summary, str) else parsed.summary,
        structured=structured if isinstance(structured, bool) else parsed.structured,
    )


def validate_interpret_structure(text: str, n_positions: int) -> bool:
    """输出结构校验：AI 解读须按【第N张·位置】标记分段，且段数不少于牌数。

    【总结】段缺失不判失格（只影响完整性，不影响安全）。
    注意：判据是「标记段总数 ≥ 牌数」，【总结】段也计入段数——模型少写
    一张牌但补了总结时仍会通过，阈值偏宽松，只拦「完全失去结构」的最坏情况。
    """
    text = (text or "").strip()
    if not text:
        return False
    marked = [s for s in SECTION_MARK_RE.split(text)[1:] if s.strip()]
    return len(marked) >= n_positions


# ---- 严格结构口径（ai.structure_check = strict）----
# 与 SECTION_MARK_RE 同源，但这里要取编号与位置名本身，所以单开两条正则；
# 容忍范围必须与 SECTION_MARK_RE 一致，否则「切得出来、校验说没有」。
_INDEX_MARK_RE = re.compile(r"【第(\d+)张([^】]*)】")
_SUMMARY_MARK_RE = re.compile(r"【总结】")
_POSITION_SEP = "·"


def validate_interpret_structure_strict(text: str, n_positions: int) -> tuple[bool, tuple[str, ...]]:
    """严格结构校验：返回 (是否合规, 不合格原因元组)。

    比宽松口径多的三条判据（位置名文本本身不锁死）：
    1) 编号恰好是 1..n_positions，每个出现一次；
    2) 【总结】标记出现 0 次或 1 次；
    3) 每个标记的位置名不得为空。
    """
    text = (text or "").strip()
    if not text:
        return False, ("输出为空",)
    marks = _INDEX_MARK_RE.findall(text)
    if not marks:
        return False, ("无【第N张·位置】标记",)
    nums = [int(n) for n, _ in marks]
    reasons: list[str] = []
    wanted = list(range(1, n_positions + 1))
    missing = [i for i in wanted if i not in nums]
    if missing:
        reasons.append("缺号:" + ",".join(str(i) for i in missing))
    out_of_range = sorted({i for i in nums if i not in wanted})
    if out_of_range:
        reasons.append("越界编号:" + ",".join(str(i) for i in out_of_range))
    dup = sorted({i for i in nums if nums.count(i) > 1})
    if dup:
        reasons.append("重复编号:" + ",".join(str(i) for i in dup))
    blank = sorted({i for i, raw in marks if not raw.lstrip(_POSITION_SEP).strip()})
    if blank:
        reasons.append("位置名为空:" + ",".join(str(i) for i in blank))
    if len(_SUMMARY_MARK_RE.findall(text)) > 1:
        reasons.append("总结标记重复")
    return (not reasons), tuple(reasons)
