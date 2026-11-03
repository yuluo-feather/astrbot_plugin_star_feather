"""AI 预算：一次占卜的 AI 阶段共用同一个截止点。

阶段上限、低于一次有意义请求就不发——这些规则与模型选择、候选链无关，
所以独立成模块；AiInterpreter 只负责把预算交给每一次请求。
"""
import time

from ..core.settings import AI_BUDGET_MIN_ATTEMPT, _budget_seconds_float

UNLIMITED_TIMEOUT = float("inf")


class AiBudget:
    """一次占卜的 AI 总预算：所有 AI 阶段共用同一个截止点（硬上限）。

    为什么要有：单请求超时（ai_timeout）只管一次调用，管不住「两段 × 每段多个
    候选 × 供应商回退」的总时长——最坏情况下没有上界，用户就在那儿干等。
    本对象只保证一件事：**该次占卜 AI 阶段的等待有上界**；它不是整条消息端到端
    的上界（图片渲染、框架调度、平台发送各有各的耗时）。

    seconds <= 0 = 不限制（limited=False）：显式的高级选项，等于关掉总预算保护
    （AiInterpreter 构造时会就此留一条 warning）。

    另可开「阶段级上限」（open_stage）：某个次要阶段（牌灵的话）不许独吞整笔预算。
    这条上限是**阶段内累计**的——候选获取与阶段内每一次请求共用同一个截止点，
    不会按「上限 × 候选数」翻倍；总预算关掉时它照样生效（独立第二约束）。

    clock 可注入：测试用假时钟推进，不睡墙钟。
    """

    def __init__(self, seconds, clock=time.monotonic):
        self.seconds = _budget_seconds_float(seconds)
        self.clock = clock
        self.limited = self.seconds > 0
        self.deadline = clock() + self.seconds if self.limited else None
        # 已因预算停手的阶段：同一阶段只报一次日志（多候选各自报一遍会把日志刷满）
        self.stopped: set[str] = set()
        # 阶段级上限（如牌灵的话的 20 秒）：{阶段名: (截止点, 上限值)}。同名阶段只开
        # 一次，候选获取与阶段内每一次请求读的都是同一个截止点——连续失败/供应商
        # 回退不会按「上限 × 候选数」累加（issue #21 ④ 复核：旧写法正是那样累加的）。
        self._stages: dict[str, tuple[float, float]] = {}

    def remaining(self) -> float:
        """剩余预算（秒）；不限制时返回 inf，调用方无需分支。"""
        if not self.limited:
            return float("inf")
        return self.deadline - self.clock()

    def open_stage(self, stage: str, stage_cap: float) -> None:
        """开一个阶段级上限（同名阶段幂等）。

        截止点 = min(总截止点, 现在 + 阶段上限)。总预算不限（limited=False）时只受
        阶段上限约束——阶段上限是独立的第二约束，不因 ai_budget_seconds=0 而失效。
        """
        if stage in self._stages:
            return
        cap = float(stage_cap)
        cap_deadline = self.clock() + cap
        deadline = min(self.deadline, cap_deadline) if self.limited else cap_deadline
        self._stages[stage] = (deadline, cap)

    def stage_left(self, stage: str) -> float | None:
        """该阶段的剩余上限（秒）；未开过该阶段返回 None。"""
        rec = self._stages.get(stage)
        return None if rec is None else rec[0] - self.clock()

    def stage_cap(self, stage: str) -> float | None:
        """该阶段的上限值（秒，仅用于日志）；未开过返回 None。"""
        rec = self._stages.get(stage)
        return None if rec is None else rec[1]

    def step_timeout(self, per_call, stage: str | None = None) -> float | None:
        """本次请求的有效超时 = min(单次超时, 剩余总预算, 阶段剩余)。

        返回 None = 不再发起新请求：预算耗尽，或剩下的不够跑一次有意义的请求
        （低于 AI_BUDGET_MIN_ATTEMPT，硬发出去就是白等一次超时）。
        stage：已开阶段上限的阶段名（见 open_stage）；None 表示本阶段无独立上限。
        总预算不限时 per_call 照样生效（有阶段上限则再取 min）。
        per_call <= 0（配置 ai_timeout=0）= 不限制单请求：返回 inf，或在有总预算 /
        阶段上限时返回那个上限——**不**返回 None（None 只表示「别再发了」）。
        """
        left = self.remaining()
        if left < AI_BUDGET_MIN_ATTEMPT:
            return None
        # per_call <= 0 = 这一档不设单请求上限；总预算与阶段剩余仍是硬约束
        limit = float(per_call) if float(per_call) > 0 else UNLIMITED_TIMEOUT
        if self.limited:
            limit = min(limit, left)
        if stage is not None:
            stage_left = self.stage_left(stage)
            if stage_left is not None:
                if stage_left < AI_BUDGET_MIN_ATTEMPT:
                    return None
                limit = min(limit, stage_left)
        return limit
