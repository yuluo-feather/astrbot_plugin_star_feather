"""核心数据结构：不 import astrbot，只描述一次占卜长什么样。"""
from dataclasses import dataclass
from typing import Literal, TypedDict

ReadingKind = Literal["spread", "daily", "single", "help"]

KIND_SPREAD: ReadingKind = "spread"
KIND_DAILY: ReadingKind = "daily"
KIND_SINGLE: ReadingKind = "single"
KIND_HELP: ReadingKind = "help"


@dataclass(frozen=True)
class ReadingRequest:
    """一次占卜请求的结构化描述：入口层只消费它，不再各自做判定。"""

    kind: ReadingKind
    spread: str        # 牌阵名（help 时为空串）
    question: str      # 清洗后的问题主体（送 AI）
    topic_key: str     # 解读缓存的主题分桶键
    fixed_for_day: bool  # 是否走今日固定牌运缓存


class Pick(TypedDict):
    """一张抽中的牌：牌库字典 + 正逆位。

    用 TypedDict 而不是 dataclass：牌面渲染、缓存与测试都按字典消费，
    换运行期形态只会让上下游多一层搬运。类型上它仍是结构化的。
    """

    card: dict
    upright: bool


@dataclass(frozen=True)
class Reading:
    """一次占卜的完整产出：服务层交出来的唯一结果对象。

    入口只负责把它交给 Presenter；「怎么发、发几条」不在这里。
    """

    request: ReadingRequest
    uid: str
    formation: str
    positions: tuple[str, ...]
    picks: tuple[Pick, ...]
    spirit: str
    interpretation: str | None
    image: str | None
    fail_note: str = ""
    epilogue: str = ""
