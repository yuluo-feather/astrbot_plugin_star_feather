"""AI 解读：候选 Provider 链、超时切换、失败冷却——纯「找模型 → 发请求 → 拿文本」。

输入侧安全（越狱剥除 / 截断）与输出侧结构校验在 hardening.py；
提示词与格式协议在 prompts.py；
本模块只做模型调用本身。

说人话：牌抽好了，怎么让 AI 好好讲话，这里管——但它的嘴要听话，由 hardening 管。
"""
import asyncio
import time
from collections import Counter

from astrbot.api import logger

from hardening import (
    clip_question,
    normalize_injection_input,
    strip_injection_fragments,
    validate_interpret_structure,
    validate_interpret_structure_strict,
)
from prompts import (
    SYSTEM_PROMPT_DIVINE,
    build_reading_prompt,
    build_spirit_line_prompt,
    build_system_prompt,
    resolve_persona,
)
from settings import (
    AI_BUDGET_MIN_ATTEMPT,
    DEFAULT_AI_BUDGET,
    DEFAULT_AI_PERSONA,
    DEFAULT_QUESTION_MAX_LEN,
    DEFAULT_SPIRIT_STAGE_BUDGET,
    DEFAULT_STRUCTURE_CHECK,
    STRUCTURE_CHECK_LOOSE,
    STRUCTURE_CHECK_SHADOW,
    STRUCTURE_CHECK_STRICT,
)

# 输出清洗用的引号集：中英引号 + 直角引号 + 单引号。用码点拼出，避开源码里的转义
# 引号写法（编辑工具会把转义解码成真实字符，这坑踩过）。
_QUOTE_CHARS = "「」『』“”" + chr(34) + chr(39)

# 阶段名：预算停手时进日志，也让调用方判断「本阶段是不是被预算掐掉的」
_STAGE_SPIRIT = "牌灵的话"
_STAGE_INTERPRET = "AI 解读"


def _budget_seconds(value) -> float:
    """预算配置归一：坏值按默认预算处理——别因为一个坏配置反而取消掉上界。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_AI_BUDGET)


def _rate_text(numerator: int, denominator: int) -> str:
    """拒绝率的统一写法：`分子/分母 (xx.xx%)`；分母为 0 输出 `N/A`。

    为什么分母为零不许写 0%：0/0 不是「没有被拒」，而是**还没有观测量**。写成 0%
    会读成「样本充足、结果良好」，恰好把「没数据」错当成「好数据」——判断要不要
    收紧时最怕这个。分母为 0 时报 N/A，读数缺失就得看起来像缺失。
    """
    if denominator <= 0:
        return f"{numerator}/{denominator} (N/A)"
    return f"{numerator}/{denominator} ({numerator / denominator * 100:.2f}%)"


class _ShadowTally:
    """影子口径的本次占卜计数：只存数字与原因，不存解读原文。

    为什么需要它：逐条影子判定只回答「这一条会被拒」，没有分母——只有分子既算不出
    候选拒绝率，也算不出「切 strict 之后会有多少次占卜整段降级」。而这两个数正是
    决定要不要收紧的全部依据。原文不落盘：隐私与体积都受不了（见日志里的汇总行）。
    """

    def __init__(self):
        self.candidates = 0        # 参与结构校验的非空候选（候选拒绝率的分母）
        self.rejected = 0          # 会被严格口径拒掉的候选（候选拒绝率的分子）
        self.reasons: Counter[str] = Counter()   # 拒绝原因分布（缺号 / 越界 / 重复 / …）
        self.strict_pass_any = False   # 严格口径下本次至少有一个候选能放行
        # 第一个放行候选的序号（从 1 起）：判决要说清「严格口径会走到第几个候选」。
        # 只记序号、不记文本——解读原文不落盘。
        self.strict_pass_index: int | None = None
        # 本次是否**因宽松口径放行而收尾**。这是唯一「严格口径会走得更深」的出口，
        # 也是判决里「未观测」那支的判据（三种出口见 _log_shadow_summary）。
        self.closed_by_loose_return = False


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
        self.seconds = _budget_seconds(seconds)
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
        """
        left = self.remaining()
        if left < AI_BUDGET_MIN_ATTEMPT:
            return None
        limit = float(per_call)
        if self.limited:
            limit = min(limit, left)
        if stage is not None:
            stage_left = self.stage_left(stage)
            if stage_left is not None:
                if stage_left < AI_BUDGET_MIN_ATTEMPT:
                    return None
                limit = min(limit, stage_left)
        return limit


class AiInterpreter:
    """AI 深度解读器：候选链 + 单请求超时 + 按 Provider 粒度的失败冷却。

    冷却粒度是「Provider」而不是插件全局（2026-08-29 调整）：
    单个模型挂掉只冷却它自己，其他模型与其他用户不受拖累；
    候选全部失败/全部冷却时才回退本地牌义。
    对塔罗数据零依赖：牌面详情（detail）由调用方（StarTarot）格式化传入，
    本类只负责「找模型 → 发请求 → 拿文本」与失败兜底状态；
    问题清洗（截断/剥除）与输出校验委托 hardening。
    """

    def __init__(self, context, enable_ai: bool = True, ai_timeout: int = 30,
                 ai_cooldown: int = 60, ai_provider_id: str = "",
                 max_question_len: int = DEFAULT_QUESTION_MAX_LEN,
                 persona: str = DEFAULT_AI_PERSONA,
                 ai_budget: int = DEFAULT_AI_BUDGET,
                 structure_check: str = DEFAULT_STRUCTURE_CHECK):
        self.context = context
        self.enable_ai = enable_ai
        self.ai_timeout = ai_timeout
        self.ai_cooldown = ai_cooldown
        self.ai_provider_id = ai_provider_id
        self.max_question_len = max_question_len
        self.persona = persona  # 牌灵人设（ai.persona），见 prompts.build_system_prompt
        # provider 级失败冷却：key=provider id → 上次服务失败时间戳。
        # 粒度到 provider（而非插件全局熔断）：单个模型挂掉只冷却它自己，
        # 其他模型与其他用户不受拖累；冷却期内该 provider 直接从候选链里跳过。
        self._fail_ts_by_provider: dict[str, float] = {}
        self.ai_budget = ai_budget  # 单次占卜的 AI 总预算（秒），0=不限制；见 AiBudget
        # 输出结构校验强度（ai.structure_check）：见 _accept_structure
        self.structure_check = structure_check
        # 影子读数的实例级累计：单签样本太小，判断「要不要收紧」得看长期分布，
        # 所以每次汇总都把累计一并落进同一行（见 _log_shadow_summary）。
        self._shadow_totals: dict[str, int] = {
            "readings": 0, "no_degrade": 0, "degrade": 0, "unknown": 0,
            "candidates": 0, "rejected": 0,
        }
        # 0 = 不限制：显式的高级选项，但别让它静默生效——面板上看不出这个值的后果，
        # 日志里至少留一次（构造期一次，不往每次占卜里刷）
        if _budget_seconds(ai_budget) <= 0:
            logger.warning(
                f"ai_budget_seconds={ai_budget}：本次运行不设 AI 总预算上限，"
                "AI 阶段的等待时长将没有上界（该选项关闭了总预算保护）")

    def new_budget(self) -> AiBudget:
        """开一笔新的 AI 总预算。

        入口（main._run_reading）在一次占卜开始时调**一次**，把同一个对象喂给这次
        占卜的全部 AI 阶段（牌灵的话 + 解读，含候选重试与供应商回退）——阶段之间
        不许重置截止点，这正是本预算存在的意义。
        """
        return AiBudget(self.ai_budget)

    def _stage_stop(self, budget: AiBudget, stage: str) -> None:
        """本阶段停手：留一行日志（同一阶段只报一次）。

        死因分两种，日志必须分得清：**总预算见底**（该阶段起不了有效请求，回退本地
        牌义）与**阶段上限用尽**（总预算还留着，只是本阶段不许再用——那份要留给正式
        解读）。把后者写成「总预算耗尽」会误导排查：日志说没钱了，账上其实还有。
        """
        if stage in budget.stopped:
            return
        budget.stopped.add(stage)
        left = budget.remaining()
        if left < AI_BUDGET_MIN_ATTEMPT:
            if left <= 0:
                logger.warning(f"AI 总预算已耗尽（{stage}，预算 {budget.seconds:g}s）："
                               "不再发起新请求，回退本地牌义")
            else:
                logger.warning(f"AI 总预算仅剩 {left:.1f}s（{stage}）："
                               "不够发起一次有效请求，回退本地牌义")
            return
        cap = budget.stage_cap(stage)
        room = "总预算不限" if not budget.limited else f"剩余总预算 {left:.1f}s"
        if cap is None:
            logger.warning(f"{stage}阶段停手（{room}）")
        else:
            logger.warning(f"{stage}阶段已达 {cap:g}s 上限：本阶段不再尝试后续候选，"
                           f"{room}留给解读")

    def _provider_id(self, provider) -> str:
        try:
            return provider.meta().id
        except Exception:
            return f"obj:{id(provider)}"

    def _in_cooldown(self, pid: str) -> bool:
        """该 provider 是否处于失败冷却期（cooled 后不发起请求，省一次空等超时）。"""
        if self.ai_cooldown <= 0:
            return False
        ts = self._fail_ts_by_provider.get(pid, 0.0)
        return ts > 0 and time.time() - ts < self.ai_cooldown

    def _mark_fail(self, pid: str) -> None:
        """记录一次服务级失败：进入该 provider 的冷却期（服务没挂则不记冷却）。"""
        if self.ai_cooldown > 0:
            self._fail_ts_by_provider[pid] = time.time()

    async def _provider_candidates(self, umo: str | None) -> list:
        """收集候选：当前会话 > 全局默认 > 全部已加载，按 id 去重。

        用 async 版的 get_using_provider_async（同步版已被框架标 @deprecated）；
        它在会话配置异常时可能抛 ValueError，这里统一吞掉——
        宁可少一个候选，也不能让「选提供商」这一步本身出错。
        """
        seen = set()
        out = []

        def _add(provider):
            if provider is None:
                return
            # 与失败冷却同一标识口径（_provider_id 内部已带 obj: 兜底），看见的都是同一种 id
            pid = self._provider_id(provider)
            if pid in seen:
                return
            seen.add(pid)
            out.append(provider)

        # 优先尝试配置指定的解读模型：命中即排在候选链最前；
        # 它挂了后面照样走会话模型 → 全局默认 → 全部已加载，健壮性不变
        if self.ai_provider_id:
            try:
                for provider in self.context.get_all_providers():
                    try:
                        if provider.meta().id == self.ai_provider_id:
                            _add(provider)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        # 三个候选来源相互独立，各自吞各自的异常；但吞掉必须留痕——接口本身不抛是
        # 候选链的纪律，静默失败却会让「用户看到回退本地牌义、日志里一片安静」
        # 无从归因（2026-10-02 补）。
        try:
            _add(await self.context.get_using_provider_async(umo))
        except Exception as e:
            logger.warning(f"候选来源[会话模型]取用失败，本签该来源缺席: {e}")
        try:
            _add(await self.context.get_using_provider_async())
        except Exception as e:
            logger.warning(f"候选来源[全局默认]取用失败，本签该来源缺席: {e}")
        try:
            for provider in self.context.get_all_providers():
                _add(provider)
        except Exception as e:
            logger.warning(f"候选来源[全部已加载]枚举失败，本签该来源缺席: {e}")
        return out

    async def _gather_candidates(self, umo: str | None,
                                 budget: AiBudget | None = None,
                                 stage: str | None = None) -> list:
        """按剩余预算收集候选：候选来源的异步取用本身也在总预算之内（issue #21 ④-a）。

        为什么单开这一层：`ai_timeout` 只包住 `_chat_once`，而「拿候选」这一步本身
        也是异步的（会话模型 / 全局默认 / 全部已加载，三个来源各自 await 一次）。它
        此前没有等待上限，于是所谓「一次占卜的 AI 总预算」漏掉了一段不受约束的等待
        ——上游队列卡住时，预算算得再准也压不住它。

        口径：
          - 无预算、或（预算不限 `ai_budget_seconds=0` 且该阶段没开阶段上限）：
            原样直取，不给 0 那档新增语义；
          - 有预算：等待上限 = 剩余预算；剩余不足 AI_BUDGET_MIN_ATTEMPT 就不再取
            （注定超时，白等一次）；
          - 该阶段开了阶段上限（budget.open_stage，如牌灵的话的 20 秒）：等待上限再取
            min(阶段剩余)——**取候选这一步本身也在阶段上限之内**，否则「阶段最多 20 秒」
            会被一段不受约束的取候选等待绕开（issue #21 ④ 复核）；
          - 取候选超时 = 这段等待把预算吃掉了，与「预算见底」按同一个死因记账
            （`_stage_stop`，同一阶段只报一次），不再自己叠第二条日志；
          - 超时只影响这一次占卜，**不**把 provider 记进失败冷却——取不到候选未必是
            模型服务的问题，冻住它只会连累后面的占卜。
        """
        if budget is None:
            return await self._provider_candidates(umo)

        def _stop_here() -> list:
            """预算/阶段上限见底就地停手：与阶段内的停手共用同一条账。"""
            if stage is not None:
                self._stage_stop(budget, stage)
            else:
                logger.warning("AI 总预算见底，不足以取用候选来源：本签回退本地牌义")
            return []

        wait = budget.remaining() if budget.limited else None
        if stage is not None:
            stage_left = budget.stage_left(stage)
            if stage_left is not None:
                wait = stage_left if wait is None else min(wait, stage_left)
        if wait is None:
            return await self._provider_candidates(umo)
        if wait < AI_BUDGET_MIN_ATTEMPT:
            return _stop_here()
        try:
            return await asyncio.wait_for(self._provider_candidates(umo), timeout=wait)
        except asyncio.TimeoutError:
            return _stop_here()
        except Exception as e:
            # 出口纪律：候选收集自身的意外绝不带走整条消息（同 main._ensure_image）
            logger.warning(f"候选收集失败（{e}）：本签按无候选处理，回退本地牌义")
            return []

    async def spirit_line(self, event, cards: list, topic: str,
                          persona_eff, budget: AiBudget | None = None) -> str | None:
        """牌灵的一句话（聊天「牌灵的话」）：AI 按人设生成短句
        （要求 25 字内，实际接受 ≤40 字——宽容策略，避免过度丢弃）。

        cards: [(card, upright), ...] —— 羽签/每日牌运单张、羽时三刻/恋羽十字
        整签，一句话锚定整组牌面（贴合本签主题）。与解读共用候选链，但失败
        **不记失败冷却**——牌灵的话挂了自己回退池内签文就行，别把 provider
        冻住拖累解读。返回 None 表示没生成（调用方兜底）。

        另受 DEFAULT_SPIRIT_STAGE_BUDGET 阶段上限：候选获取与每次请求都在同一个
        阶段截止点之内（累计，不按候选数翻倍），阶段用尽即停，剩余总预算留给解读。
        """
        if not self.enable_ai:
            return None
        # 预算：调用方（一次占卜）传入共用对象；不传就本阶段自开一笔——直接调用
        # spirit_line 的老用法照样有上界，不会因为漏传而变成无界等待。
        # 牌灵的话是次要内容，另受 DEFAULT_SPIRIT_STAGE_BUDGET 阶段上限约束：它不能
        # 独吞整笔预算、把核心解读的时间耗光（但两者仍服从同一个总截止点）。
        # 这个上限是**阶段级累计**的（open_stage 只开一次，阶段内所有候选获取与请求
        # 共用同一个截止点）：旧写法对每个 Provider 各取一次 20 秒，两个候选连续失败
        # 就能累积等到 40 秒，与「牌灵的话阶段最多 20 秒」的意图不符（issue #21 ④ 复核）。
        # 即使 ai_budget_seconds=0（总预算不限），这条阶段上限照样生效。
        bd = budget if budget is not None else self.new_budget()
        bd.open_stage(_STAGE_SPIRIT, DEFAULT_SPIRIT_STAGE_BUDGET)
        # 清洗链与 interpret() 一致：归一化先行 → 截断 → 句式剥除（防御深度统一，
        # topic 会整段拼进 prompt，不能只剥不档）
        topic = normalize_injection_input(topic or "")
        if self.max_question_len > 0:
            topic = clip_question(topic, self.max_question_len)
        topic = strip_injection_fragments(topic)
        prompt = build_spirit_line_prompt(
            [(card["cn"], upright) for card, upright in cards], topic, persona_eff)
        # system 用中立底稿而非 persona 段：persona 段夹带【第N张】解读格式约束，
        # 会让「一句话」生成跑偏成结构化解读；人设风格已拼在 prompt 的 signature_style
        system = SYSTEM_PROMPT_DIVINE
        umo = getattr(event, "unified_msg_origin", None)
        for provider in await self._gather_candidates(umo, bd, _STAGE_SPIRIT):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                continue
            timeout = bd.step_timeout(self.ai_timeout, _STAGE_SPIRIT)
            if timeout is None:
                self._stage_stop(bd, _STAGE_SPIRIT)
                break  # 总预算或阶段上限见底：候选链剩下的不再试（共享截止点，不重置）
            text = await self._chat_once(provider, prompt, system, timeout)
            if not text:
                continue
            text = text.strip().strip(_QUOTE_CHARS).strip()
            if not text:
                # 模型只回了引号/纯空白：当这次没生成，继续下一候选。
                # 留一行日志的理由：interpret() 的候选链四步全有日志（冷却跳过/结构失格/
                # tried==0/全败），这里原本零条，排查「牌灵的话为什么老是回退池内签文」
                # 时在日志里查不出原因（2026-10-01 补）。
                logger.info(f"牌灵的话候选[{pid}]剥引号后为空，继续下一候选")
                continue
            line = text.splitlines()[0].strip()
            if 0 < len(line) <= 40:
                return line
            # 走到循环体末尾＝这一候选不合规（超长，或「只有换行」使首行剥完为空），
            # 接着试下一个候选。不是 continue，效果同：在最后一个候选上等于 return None。
            # 不记失败冷却（见 docstring），但这支同样必须有日志，理由同上。
            logger.info(
                f"牌灵的话候选[{pid}]输出不合规（len={len(line)}，只收 1~40 字），继续下一候选")
        return None

    async def _chat_once(self, provider, prompt: str, system_prompt: str,
                         timeout: float | None = None) -> str | None:
        """对单个候选发起解读请求：超时或异常一律返回 None，交给下一个候选。

        timeout：本次请求的有效超时，由调用方按预算算出（AiBudget.step_timeout）；
        不传则用实例的 ai_timeout——直接调用本方法的老口径不变。
        """
        limit = self.ai_timeout if timeout is None else timeout
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt, session_id=None, contexts=[], image_urls=[],
                    system_prompt=system_prompt,
                ),
                timeout=limit,
            )
        except asyncio.TimeoutError:
            logger.warning(f"AI 解读超时（>{limit:g}s），放弃该提供商")
            return None
        except Exception as e:
            logger.warning(f"AI 解读请求失败: {e}")
            return None
        try:
            return (resp.completion_text or "").strip() or None
        except Exception:
            return None

    def _accept_structure(self, text: str, n_positions: int,
                          tally: _ShadowTally | None = None) -> bool:
        """输出结构闸：这一份候选文本按当前口径放不放行（ai.structure_check）。

        loose   —— 只走宽松口径（【第N张·位置】标记段数 ≥ 牌数），v0.7.8 之前的行为。
        shadow  —— 默认。行为等同于 loose，但同时用严格口径判一遍，不合规就记一行
                   info：**先量再收紧**。理由很实际——严格口径拒掉一个候选＝那次占卜
                   整段 AI 解读降级成本地牌义，这笔代价只能用真实候选的拒绝率来估，
                   而真实候选只有线上跑得出来。影子判定不改任何行为，所以敢默认开。
        strict  —— 按严格口径放行：编号 1..N 各一次、总结 0/1 次、位置名非空。

        三档共用同一个函数入口：收紧时改一个配置值，不用改代码，也不会出现
        「哪儿查、哪儿不查」的两份实现慢慢走偏。

        tally：影子模式的本次计数（调用方在 shadow 档才传）。它记的是**严格口径**的
        判定，与本次实际放行与否无关——这是故意的：影子要量的就是「若按 strict 走，
        这一条会怎样」，两种口径的判断必须落在同一批候选上才可比。本函数只管逐条
        判定与计数；「这一次究竟会不会降级」取决于候选链从哪个出口收尾，见
        _log_shadow_summary 的三种出口。
        """
        loose = validate_interpret_structure(text, n_positions)
        if self.structure_check == STRUCTURE_CHECK_LOOSE:
            if not loose:
                logger.warning("AI 解读未按【第N张·位置】结构输出，弃用该候选，继续尝试")
                return False
            return True
        ok, why = validate_interpret_structure_strict(text, n_positions)
        if tally is not None:
            tally.candidates += 1
            if ok:
                tally.strict_pass_any = True
                if tally.strict_pass_index is None:
                    tally.strict_pass_index = tally.candidates
            else:
                tally.rejected += 1
                tally.reasons.update(why)
        if self.structure_check == STRUCTURE_CHECK_STRICT:
            if not ok:
                logger.warning(
                    f"AI 解读结构不合规（严格口径：{'/'.join(why)}），弃用该候选，继续尝试")
                return False
            return True
        # shadow：判定只落日志，放行与否完全交给宽松口径
        if loose and not ok:
            logger.info(f"结构校验·影子：本候选会被严格口径拒绝（{'/'.join(why)}），当前仍按宽松口径放行")
        elif not loose:
            logger.warning("AI 解读未按【第N张·位置】结构输出，弃用该候选，继续尝试")
        return loose

    def _log_shadow_summary(self, tally: _ShadowTally) -> None:
        """影子口径的本次汇总：两个指标 + 一个判决，外加实例级累计读数。

        两个指标的口径在这里写死（主人 2026-10-12 定的第二条约束）：
        - **候选拒绝率** = rejected / candidates。分母是**本次真的请求到并判定过**的
          候选，不是「完整候选链的长度」——没请求到的候选没有观测量。
        - **降级率** = 严格口径下「整段 AI 解读回退本地牌义」的概率。它**不能**拿
          「影子拒了多少条候选」顶替：某条候选被拒 ≠ 这次降级，因为严格口径被拒之后
          会继续往下试后面的候选（cost 更高、但可能救回来）。所以判决按候选链的
          **三种出口**分，只有三态才能反映完整链的最终结果：
          ① 出现过严格通过的候选 ⇒ 严格口径也会走到它并放行 ⇒ **不降级**（确定）；
          ② 链已走完（所有可用候选都试过，或被预算/服务失败这类**与结构无关**的原因
             截断——那些在严格口径下同样发生）且无严格通过者 ⇒ **降级**（确定）；
          ③ 链因宽松口径放行而提前收尾、后续候选本次没请求 ⇒ **未观测**：严格口径会
             继续试它们，降级与否取决于它们。这一支只能报未知，不许写成「会降级」，
             否则就是把一个我们没量过的数当成结论用。
        逐条判定只回答「哪一条会被拒」，没有分母就没有结论——这也是这行存在的理由。
        不含解读原文：只有数字与原因（隐私与体积都不允许把用户解读长期存盘）。

        统计口径（主人 2026-10-13 第 2 条补充）：
        - 单次与累计都写「分子/分母 (百分比)」；分母为 0 写 `N/A`，不写 0%；
        - 累计值**只属于当前 AiInterpreter 实例**（进程重启或插件热重载即清零），
          所以行内明确写出作用范围；观察期的总体拒绝率要用「各次观测的 Σ拒绝 ÷
          Σ候选」——**不能**平均各行百分比，也**不能**把多代实例的累计值相加（多代
          之间不重叠，但那不是同一窗口的样本，加起来会把「同一窗口」的口径搅乱）。
        """
        if self.structure_check != STRUCTURE_CHECK_SHADOW or tally.candidates == 0:
            return
        why = "/".join(f"{k}×{v}" for k, v in sorted(tally.reasons.items())) or "无"
        if tally.strict_pass_index is not None:
            verdict = (f"严格口径下本签不降级（第 {tally.strict_pass_index} 个候选即严格放行，"
                       "尝试深度与本次一致）")
            bucket = "no_degrade"
        elif tally.closed_by_loose_return:
            verdict = ("严格口径下会继续尝试本次未请求的后继候选：本签降级与否**未观测**"
                       "（本次是宽松口径放行后收尾的）")
            bucket = "unknown"
        else:
            verdict = "严格口径下本签降级为本地牌义（整条可用候选链已观测完，无候选严格通过）"
            bucket = "degrade"
        t = self._shadow_totals
        t["readings"] += 1
        t[bucket] += 1
        t["candidates"] += tally.candidates
        t["rejected"] += tally.rejected
        logger.info(
            f"结构校验·影子汇总：本次候选拒绝率 {_rate_text(tally.rejected, tally.candidates)}"
            f"（原因：{why}）；{verdict}｜累计（本解释器实例自启动/热重载至今，非全局；"
            f"观察期总体拒绝率请用各次观测的 Σ拒绝÷Σ候选，勿平均各行百分比、勿跨代累加）："
            f"{t['readings']} 签——不降级 {t['no_degrade']}／降级 {t['degrade']}／"
            f"未观测 {t['unknown']}，累计候选拒绝率 {_rate_text(t['rejected'], t['candidates'])}")

    async def interpret(self, event, formation: str, positions: list[str],
                        picks: list[dict], user_input: str, detail: str,
                        persona_eff: str | None = None,
                        budget: AiBudget | None = None) -> str | None:
        """AI 结构化解读。返回 None 表示本轮没有 AI 解读（候选全部失败或全部处于冷却期），
        由调用方回退本地牌义。给用户的感觉是：解读要么有，要么是牌义，不会等成雕塑。

        persona_eff：调用方（如 daily 牌灵的话同签共用人格）传解析后的人格；
        None 时本方法自解析（每次调用一次掷骰）。
        budget：本次占卜共用的 AI 总预算（AiBudget）；不传则本阶段自开一笔。
        候选重试与供应商回退都吃这同一笔预算——不许在这里重置。
        """
        if not self.enable_ai:
            return None
        bd = budget if budget is not None else self.new_budget()
        # 清洗链路顺序（技能规范）：归一化（先行）→ 截断 → 句式剥除
        user_input = normalize_injection_input(user_input)  # 归一化先行：全角/零宽/bidi 绕行先失效
        if self.max_question_len > 0:
            user_input = clip_question(user_input, self.max_question_len)  # 超长压缩（0=不压缩）
        user_input = strip_injection_fragments(user_input)  # 剥除夹带的越狱句式
        if not user_input:
            logger.info("问题剥除注入句式后为空，回退本地牌义")
            return None
        prompt = build_reading_prompt(user_input, formation, detail)
        # 人设只叠加口吻，保护句（只解读塔罗/无视指令）与内容结构不动；
        # system_prompt 必须在候选链循环外构建：一次占卜只掷一次骰子，
        # random 时本签全程同一人格、下一签才换——中途变脸是 bug（有测试锁）
        eff = persona_eff if persona_eff is not None else resolve_persona(self.persona)
        system_prompt = build_system_prompt(eff)
        umo = getattr(event, "unified_msg_origin", None)
        got_text = False  # 是否至少有一个候选产出了文本（区分“服务失败”与“结构失格”）
        tried = 0
        # 影子计数只在 shadow 档开：其余档位没有「量」的任务，别白记
        tally = (_ShadowTally() if self.structure_check == STRUCTURE_CHECK_SHADOW else None)
        for provider in await self._gather_candidates(umo, bd, _STAGE_INTERPRET):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                logger.info(f"AI 解读 provider[{pid}] 处于失败冷却期，跳过")
                continue
            timeout = bd.step_timeout(self.ai_timeout)
            if timeout is None:
                self._stage_stop(bd, _STAGE_INTERPRET)
                break  # 预算见底：剩下的候选与供应商回退都不再试（截止点不重置）
            tried += 1
            text = await self._chat_once(provider, prompt, system_prompt, timeout)
            if not text:
                self._mark_fail(pid)  # 服务级失败：只冷却这个 provider
                continue
            got_text = True
            # 结构闸：按 ai.structure_check 的口径判定（默认影子——只量不拦，
            # 严格口径的拒绝率得先用真实候选统计出来再决定是否启用）
            if not self._accept_structure(text, len(positions), tally):
                continue
            if tally is not None:
                # 因宽松口径放行而收尾：严格口径下会继续往下试，判决要落在「未观测」
                tally.closed_by_loose_return = True
                self._log_shadow_summary(tally)
            return text
        if tally is not None:
            self._log_shadow_summary(tally)
        if _STAGE_INTERPRET in bd.stopped:
            return None  # 预算掐掉的：原因已在 _stage_stop 里写过，别叠一条误导性的「全败」
        if tried == 0:
            logger.info("AI 解读没有可用候选（来源不可用或全部处于失败冷却期），直接使用本地牌义")
            return None
        if not got_text:
            logger.warning("所有可用提供商均未产出 AI 解读（已按 provider 记录失败冷却）")
        else:
            logger.warning("所有候选均产出但结构失格，回退本地牌义（不进入失败冷却）")
        return None
