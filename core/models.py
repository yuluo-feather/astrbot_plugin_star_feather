"""核心数据结构：不 import astrbot，只描述一次占卜长什么样。"""
from dataclasses import dataclass
from typing import Literal

KIND_SPREAD = "spread"
KIND_DAILY = "daily"
KIND_SINGLE = "single"
KIND_HELP = "help"

ReadingKind = Literal["spread", "daily", "single", "help"]


@dataclass(frozen=True)
class ReadingRequest:
    """一次占卜请求的结构化描述：入口层只消费它，不再各自做判定。"""

    kind: ReadingKind
    spread: str        # 牌阵名（help 时为空串）
    question: str      # 清洗后的问题主体（送 AI）
    topic_key: str     # 解读缓存的主题分桶键
    fixed_for_day: bool  # 是否走今日固定牌运缓存
