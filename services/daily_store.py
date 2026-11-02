"""今日牌运的持久化记录：一个用户一个键，schema 2。

旧版拆成 `sf_daily_*`（牌 + 解读分桶）与 `sf_spirit_*`（牌灵的话）两个键；
现在合并成 `sf_daily_*` 一条记录，牌、解读与牌灵的话共用一个日期边界。
升级时旧记录（没有 schema=2）直接视为未命中，由下一次读写覆盖。
"""
from dataclasses import dataclass, field

SCHEMA = 2


def _str(value, default: str = "") -> str:
    return value if isinstance(value, str) else default


@dataclass
class DailyRecord:
    date: str = ""
    card_id: str = ""
    upright: bool = False
    sign: str = ""
    prompt_v: str = ""
    persona: str = ""
    interps: dict = field(default_factory=dict)
    spirit_line: str = ""
    spirit_sign: str = ""
    spirit_persona: str = ""
    schema: int = SCHEMA

    @classmethod
    def from_raw(cls, raw) -> "DailyRecord":
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            return cls()
        upright = raw.get("upright")
        if not isinstance(upright, bool):
            # 历史数据或人工改坏的值（"false" / 0 / 缺失）按无效记录处理：
            # bool("false") 是 True，静默转换会把逆位变成正位
            return cls()
        interps = raw.get("interps")
        return cls(
            date=_str(raw.get("date")),
            card_id=_str(raw.get("card_id")),
            upright=upright,
            sign=_str(raw.get("sign")),
            prompt_v=_str(raw.get("prompt_v")),
            persona=_str(raw.get("persona")),
            interps=dict(interps) if isinstance(interps, dict) else {},
            spirit_line=_str(raw.get("spirit_line")),
            spirit_sign=_str(raw.get("spirit_sign")),
            spirit_persona=_str(raw.get("spirit_persona")),
        )

    def to_raw(self) -> dict:
        return {
            "schema": SCHEMA,
            "date": self.date,
            "card_id": self.card_id,
            "upright": self.upright,
            "sign": self.sign,
            "prompt_v": self.prompt_v,
            "persona": self.persona,
            "interps": self.interps,
            "spirit_line": self.spirit_line,
            "spirit_sign": self.spirit_sign,
            "spirit_persona": self.spirit_persona,
        }

    def put_pick(self, card_id: str, upright: bool, sign: str, date: str,
                 prompt_v: str, persona: str) -> None:
        """抽牌结果写入：牌字段只能从这一处改（P0 的结构保证）。"""
        self.date = date
        self.card_id = card_id
        self.upright = bool(upright)
        self.sign = sign
        self.prompt_v = prompt_v
        self.persona = persona

    def put_context(self, date: str, sign: str, prompt_v: str, persona: str) -> None:
        """解读/上下文写入：只改日期、牌组指纹与配置指纹，不碰牌字段。"""
        self.date = date
        self.sign = sign
        self.prompt_v = prompt_v
        self.persona = persona

    def put_interp(self, topic: str, record: dict, sign: str, date: str,
                   prompt_v: str, persona: str) -> None:
        """解读写入：只改解读分桶与它依赖的上下文，不碰牌字段。"""
        self.put_context(date, sign, prompt_v, persona)
        self.interps[topic] = record

    def put_spirit(self, line: str, sign: str, date: str, prompt_v: str,
                   persona: str) -> None:
        """牌灵的话写入：只改日期/提示词版本/牌灵字段，不碰牌字段。

        persona 写进独立的 spirit_persona，不复用每日解读的 persona——
        否则普通占卜换人设会把每日牌运的解读上下文一起改掉。
        """
        self.date = date
        self.prompt_v = prompt_v
        self.spirit_line = line
        self.spirit_sign = sign
        self.spirit_persona = persona

    def same_context(self, date: str, sign: str, prompt_v: str, persona: str) -> bool:
        """同一天、同一牌组、同一版提示词、同一人设才沿用旧分桶。"""
        return (self.schema == SCHEMA and self.date == date and self.sign == sign
                and self.prompt_v == prompt_v and self.persona == persona)
