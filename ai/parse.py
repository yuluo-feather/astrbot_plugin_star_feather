"""解读解析与结构校验：一次解析，产出的 Interpretation 同时供缓存与发送使用。"""
import re

from .prompts import SECTION_MARK_RE


class Interpretation(str):
    """一次 AI 解读：文本本身 + 预切分好的段落与总结。

    继承 str 是为了让既有比较/日志/缓存文本保持原样；额外属性只在需要结构化发送
    或结构化缓存时读取。解析只做一次：AI 返回时构造，发送与写缓存都复用。
    """

    sections: tuple[str, ...]
    summary: str
    structured: bool

    def __new__(cls, text: str):
        obj = super().__new__(cls, text)
        obj._parse()
        return obj

    def _parse(self) -> None:
        text = str(self).strip()
        segs = SECTION_MARK_RE.split(text)
        # 与 deliver.split_sections 同口径：segs[0] 是标记前前言，其余是标记段。
        if len(segs) < 3:
            self.sections = (text,) if text else ()
            self.summary = ""
            self.structured = False
            return
        parts = [s.strip() for s in segs if s.strip()]
        summary = ""
        sections = []
        for part in parts:
            if part.startswith("【总结】"):
                summary = part
            else:
                sections.append(part)
        self.sections = tuple(sections)
        self.summary = summary
        self.structured = True

    def to_record(self) -> dict:
        """缓存里的结构：文本 + 段落 + 总结，读取时不再重新切分。"""
        return {"text": str(self), "sections": list(self.sections),
                "summary": self.summary, "structured": self.structured}

    @classmethod
    def from_record(cls, record) -> "Interpretation":
        """缓存记录 → Interpretation；旧版纯文本字符串仍能读。"""
        if isinstance(record, str):
            return cls(record)
        if not isinstance(record, dict):
            return cls("")
        text = record.get("text")
        if not isinstance(text, str) or not text:
            return cls("")
        obj = cls(text)
        sections = record.get("sections")
        if isinstance(sections, list) and all(isinstance(s, str) for s in sections):
            obj.sections = tuple(sections)
        summary = record.get("summary")
        if isinstance(summary, str):
            obj.summary = summary
        structured = record.get("structured")
        if isinstance(structured, bool):
            obj.structured = structured
        return obj


def parse_interpretation(text: str | None) -> Interpretation | None:
    """AI 原文 → Interpretation；空文本返回 None。"""
    if not text or not text.strip():
        return None
    return Interpretation(text)


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


# ---- 严格结构口径（ai.structure_check = strict / shadow）----
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
