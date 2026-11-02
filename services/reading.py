"""占卜编排：抽牌 → 两段 AI → 渲染 → 交给 Presenter。

这一层只做阶段顺序与降级裁决，不碰框架对象：
- 入口给 ReadingRequest 与 ReadingContext（uid / umo / Messenger）；
- 服务返回一个 Reading，怎么发由 adapters/presenter.py 决定。

两条出口纪律原样保留：
- 装饰性产出失败不陪葬整卦（牌灵的话坏了退池内句，解读坏了退本地牌义）；
- 图片问题绝不带走消息（渲染失败/发送前核验失败都降级为无图）。
"""
import asyncio
import random
from dataclasses import dataclass

from ..core.draw import draw, render_detail
from ..core.log import log
from ..core.models import (
    KIND_DAILY,
    Pick,
    Reading,
    ReadingRequest,
)
from ..core.ports import Messenger, Rng
from ..intent import norm_topic
from ..prompts import RESULT_EPILOGUE, SHUFFLE_LINES, resolve_persona
from ..settings import Settings


@dataclass(frozen=True)
class ReadingContext:
    """一次占卜的入口上下文：谁在问、从哪个会话来、结果往哪发。"""

    uid: str
    umo: str | None
    messenger: Messenger


@dataclass(frozen=True)
class PreparedReading:
    """抽牌结果：确定性抽牌已完成，后续 AI/渲染/发送只读它。"""

    request: ReadingRequest
    uid: str
    formation: str
    positions: list[str]
    picks: list[Pick]
    is_daily: bool


class ReadingService:
    """三入口共用的占卜流水线。"""

    def __init__(self, *, settings: Settings, clock, daily, interpreter, renderer,
                 rng: Rng | None = None):
        self.settings = settings
        self.clock = clock
        self.daily = daily
        self.interpreter = interpreter
        self.renderer = renderer
        self.rng = rng

    async def prepare(self, request: ReadingRequest, uid: str) -> PreparedReading:
        """按意图抽牌：今日固定走缓存，其余自由随机。"""
        if request.kind == KIND_DAILY and request.fixed_for_day:
            picked = await self.daily.pick_cached(uid)
            if picked:
                formation, positions, picks = picked
                return PreparedReading(request, uid, formation, list(positions),
                                       list(picks), True)
            log.warning("每日牌运不可用（无用户标识），回退自由随机")
        positions, picks = draw(request.spread, self.rng)
        return PreparedReading(request, uid, request.spread, positions, picks, False)

    async def run(self, prepared: PreparedReading, ctx: ReadingContext, *,
                  question: str, fail_note: str = "",
                  empty_fallback: str = "（未提具体问题，请作一般运势解读）",
                  epilogue: bool = False) -> Reading:
        """执行一次占卜：洗牌提示 → 两段 AI → 海报/牌面图 → 发送前核验。"""
        positions, picks = prepared.positions, prepared.picks
        hint = self._shuffle_hint() if len(positions) > 1 else None
        preface = ""
        if hint and not await ctx.messenger.send_text(hint):
            preface = hint

        clean = (question or "").strip()
        topic = prepared.request.topic_key or norm_topic(clean)
        persona_eff = resolve_persona(
            self.settings.ai.persona, seed=self._persona_seed(prepared.uid))
        budget = self.interpreter.new_budget()

        spirit_call = (self.daily.spirit_cached(ctx.umo, prepared.uid, picks, topic,
                                                persona_eff, budget)
                       if picks else None)
        if prepared.is_daily and prepared.uid:
            interp_call = self.daily.interp_cached(
                ctx.umo, prepared.uid, prepared.formation, positions, picks, clean,
                persona_eff, budget)
        else:
            interp_call = self.interpret(
                ctx.umo, prepared.formation, positions, picks,
                clean or empty_fallback, persona_eff, budget)

        if spirit_call is None:
            spirit, interpretation = "", await interp_call
        elif self.settings.ai.parallel_stages:
            # TaskGroup：任一段抛异常时兄弟任务会被取消，不会在后台继续烧 AI 预算
            async with asyncio.TaskGroup() as tg:
                spirit_task = tg.create_task(spirit_call)
                interp_task = tg.create_task(interp_call)
            spirit, interpretation = spirit_task.result(), interp_task.result()
        else:
            # 顺序执行：同一 Provider 上不会同时压两个请求（单槽本地模型/限流严格）
            try:
                spirit = await spirit_call
            except BaseException:
                interp_call.close()  # 未 await 的协程要关掉，别在日志里留 never awaited
                raise
            interpretation = await interp_call

        if spirit:
            preface = f"{preface}\n{spirit}" if preface else spirit

        image = None
        if (prepared.is_daily and prepared.uid and picks
                and self.settings.output.daily_card
                and self.settings.output.send_mode != "text_only"):
            image = await self.renderer.daily_card(picks, prepared.uid, self.clock)
        if not image:
            image = await self.renderer.card(prepared.formation, positions, picks)
        image = await self.renderer.ensure_image(
            image, prepared.formation, positions, picks)

        return Reading(
            request=prepared.request,
            uid=prepared.uid,
            formation=prepared.formation,
            positions=tuple(positions),
            picks=tuple(picks),
            spirit=preface,
            interpretation=interpretation,
            image=image,
            fail_note=fail_note,
            epilogue=random.choice(RESULT_EPILOGUE) if epilogue else "",
        )

    async def interpret(self, umo: str | None, formation: str, positions: list[str],
                        picks: list[Pick], user_input: str,
                        persona_eff: str | None = None, budget=None) -> str | None:
        """AI 结构化解读：牌面详情在这里格式化，解释器保持对塔罗结构零依赖。"""
        detail = render_detail(positions, picks)
        return await self.interpreter.interpret(
            umo, formation, positions, picks, user_input, detail,
            persona_eff=persona_eff, budget=budget)

    def _shuffle_hint(self) -> str | None:
        if not self.settings.output.shuffle_lines:
            return None
        return random.choice(SHUFFLE_LINES)

    def _persona_seed(self, uid: str) -> str | None:
        """random 人设按（用户, 日期）固定：同一天换主题也是同一副腔调。"""
        return f"{uid}|{self.clock.today()}" if uid else None
