"""AI 解读：候选 Provider 链、超时切换、失败冷却、并发上限与总预算。

输入侧安全（越狱剥除 / 截断）在 ai/hardening.py，输出侧结构校验与解析在 ai/parse.py，
提示词与格式协议在 ai/prompts.py；本模块只做模型调用本身。

说人话：牌抽好了，怎么让 AI 好好讲话，这里管——但它的嘴要听话，由 hardening 管。
"""
import asyncio
import threading
import time
import weakref

from ..core.log import log as logger
from ..core.models import Interpretation
from ..core.settings import (
    AI_BUDGET_MIN_ATTEMPT,
    DEFAULT_AI_BUDGET,
    DEFAULT_AI_PERSONA,
    DEFAULT_QUESTION_MAX_LEN,
    DEFAULT_SPIRIT_STAGE_BUDGET,
    DEFAULT_STRUCTURE_CHECK,
    STRUCTURE_CHECK_STRICT,
    _budget_seconds_float,
)
from .budget import UNLIMITED_TIMEOUT, AiBudget
from .hardening import (
    clip_question,
    normalize_injection_input,
    strip_injection_fragments,
)
from .parse import (
    parse_interpretation,
    validate_interpret_structure,
    validate_interpret_structure_strict,
)
from .prompts import (
    SYSTEM_PROMPT_DIVINE,
    build_reading_prompt,
    build_spirit_line_prompt,
    build_system_prompt,
    resolve_persona,
)

# 输出清洗用的引号集：中英引号 + 直角引号 + 单引号。用码点拼出，避开源码里的转义
# 引号写法（编辑工具会把转义解码成真实字符，这坑踩过）。
_QUOTE_CHARS = "「」『』“”" + chr(34) + chr(39)

# 阶段名：预算停手时进日志，也让调用方判断「本阶段是不是被预算掐掉的」
_STAGE_SPIRIT = "牌灵的话"
_STAGE_INTERPRET = "AI 解读"

class _QueueTimeout:
    """并发闸排队耗尽了本次请求预算（本地原因，不是 Provider 服务失败）。"""

    __slots__ = ()


QUEUE_TIMEOUT = _QueueTimeout()


class AiInterpreter:
    """AI 深度解读器：候选链 + 单请求超时 + 按 Provider 粒度的失败冷却。

    冷却粒度是「Provider」而不是插件全局（2026-08-29 调整）：
    单个模型挂掉只冷却它自己，其他模型与其他用户不受拖累；
    候选全部失败/全部冷却时才回退本地牌义。
    对塔罗数据零依赖：牌面详情（detail）由调用方（ReadingService）格式化传入，
    本类只负责「找模型 → 发请求 → 拿文本」与失败兜底状态；
    问题清洗（截断/剥除）与输出校验委托 hardening。
    """

    def __init__(self, context, enable_ai: bool = True, ai_timeout: int = 30,
                 ai_cooldown: int = 60, ai_provider_id: str = "",
                 max_question_len: int = DEFAULT_QUESTION_MAX_LEN,
                 persona: str = DEFAULT_AI_PERSONA,
                 ai_budget: int = DEFAULT_AI_BUDGET,
                 structure_check: str = DEFAULT_STRUCTURE_CHECK,
                 max_concurrency: int = 2,
                 all_providers_fallback: bool = False,
                 clock=time.monotonic):
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
        # 冷却计时用进程内单调时钟：只剩「过了多久」这一种语义，墙钟回拨不该
        # 把 provider 多冻一段时间。可注入假时钟，测试不睡墙钟。
        self._clock = clock
        self.ai_budget = ai_budget  # 单次占卜的 AI 总预算（秒），0=不限制；见 AiBudget
        # 输出结构校验强度（ai.structure_check）：见 _accept_structure
        self.structure_check = structure_check
        # 同一 Provider 的并发上限：单槽本地模型 / 限流严格时，两段 AI 同时压过去会
        # 排队甚至触发限流，随后还会被误记成失败冷却。0 = 不限制。
        self.max_concurrency = max_concurrency
        # 并发闸按事件循环分开存。外层用弱引用键：未争用的闸不碰循环，循环回收后
        # 条目自动消失；争用的闸会反向持有循环，取闸时再清一遍已关闭循环的条目。
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = (
            weakref.WeakKeyDictionary())
        # 同一解释器可能被多个线程（各自跑一个事件循环）同时使用：
        # 清理/查找/插入必须串行，否则迭代中改字典会抛 RuntimeError
        self._semaphores_guard = threading.Lock()
        # 是否在「指定 → 会话 → 全局默认」之后回退到全部已加载 Provider。
        # 默认关：未预期的模型不该被悄悄拉进来烧请求。
        self.all_providers_fallback = all_providers_fallback
        # 0 = 不限制：显式的高级选项，但别让它静默生效——面板上看不出这个值的后果，
        # 日志里至少留一次（构造期一次，不往每次占卜里刷）
        if _budget_seconds_float(ai_budget) <= 0:
            logger.warning(
                f"ai_budget_seconds={ai_budget}：本次运行不设 AI 总预算上限，"
                "AI 阶段的等待时长将没有上界（该选项关闭了总预算保护）")

    def new_budget(self) -> AiBudget:
        """开一笔新的 AI 总预算。

        入口（services.reading.ReadingService.run）在一次占卜开始时调**一次**，把同一个对象喂给这次
        占卜的全部 AI 阶段（牌灵的话 + 解读，含候选重试与供应商回退）——阶段之间
        不许重置截止点，这正是本预算存在的意义。
        """
        return AiBudget(self.ai_budget, self._clock)

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
        return ts > 0 and self._clock() - ts < self.ai_cooldown

    def _mark_fail(self, pid: str) -> None:
        """记录一次服务级失败：进入该 provider 的冷却期（服务没挂则不记冷却）。"""
        if self.ai_cooldown > 0:
            self._fail_ts_by_provider[pid] = self._clock()

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
                    except Exception as e:
                        logger.warning(
                            f"候选来源[指定模型]读取 Provider 元信息失败，跳过: {e}")
                        continue
            except Exception as e:
                logger.warning(f"候选来源[指定模型]枚举失败，本签该来源缺席: {e}")

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
        if self.all_providers_fallback:
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
            # 出口纪律：候选收集自身的意外绝不带走整条消息（同 Renderer.ensure_image）
            logger.warning(f"候选收集失败（{e}）：本签按无候选处理，回退本地牌义")
            return []

    async def spirit_line(self, umo: str | None, cards: list, topic: str,
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
        for provider in await self._gather_candidates(umo, bd, _STAGE_SPIRIT):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                continue
            timeout = bd.step_timeout(self.ai_timeout, _STAGE_SPIRIT)
            if timeout is None:
                self._stage_stop(bd, _STAGE_SPIRIT)
                break  # 总预算或阶段上限见底：候选链剩下的不再试（共享截止点，不重置）
            text = await self._chat_once(provider, prompt, system, timeout)
            if text is QUEUE_TIMEOUT:
                continue
            if not isinstance(text, str) or not text:
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

    def _semaphore_for(self, provider) -> asyncio.Semaphore | None:
        """该 Provider 的并发闸；0 = 不限制，返回 None。

        按事件循环分开存：同一解释器可能被多个 asyncio.run 依次使用（测试与诊断
        脚本），Semaphore 绑在创建它的循环上，跨循环复用会直接报错。
        弱键 + 取闸时清理已关闭循环，保证登记表不会随循环更替一直增长。
        """
        if self.max_concurrency <= 0:
            return None
        loop = asyncio.get_running_loop()
        with self._semaphores_guard:
            for known in [lp for lp in self._semaphores if lp.is_closed()]:
                self._semaphores.pop(known, None)
            per_loop = self._semaphores.get(loop)
            if per_loop is None:
                per_loop = {}
                self._semaphores[loop] = per_loop
            pid = self._provider_id(provider)
            sem = per_loop.get(pid)
            if sem is None:
                sem = asyncio.Semaphore(self.max_concurrency)
                per_loop[pid] = sem
            return sem

    async def _chat_once(self, provider, prompt: str, system_prompt: str,
                         timeout: float | None = None) -> str | None | _QueueTimeout:
        """对单个候选发起解读请求：超时或异常一律返回 None，交给下一个候选。

        timeout：本次请求的有效超时，由调用方按预算算出（AiBudget.step_timeout）；
        不传则用实例的 ai_timeout——直接调用本方法的老口径不变。
        两者为 0 或 inf 时视为**不限制单请求**：不加 wait_for。上游若还有总预算或
        阶段上限，算出来的就不是 inf，这里自然照旧加（不限制 ≠ 整段没有上界）。

        并发闸的排队也算在这笔 timeout 里：先按同一截止点等槽位，拿到槽位后用
        剩余时间发请求。排队超时返回 QUEUE_TIMEOUT——它是本地资源不足，不是
        Provider 服务失败，调用方不得据此记失败冷却。
        """
        raw = self.ai_timeout if timeout is None else timeout
        limit = UNLIMITED_TIMEOUT if raw is None or float(raw) <= 0 else float(raw)
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            # 调用本身也放进 try：同步抛异常（签名不匹配、参数校验失败）同样
            # 只是「这个候选失败」，不许绕过 AI 失败的兜底边界带走整条卦
            sem = self._semaphore_for(provider)
            if sem is None:
                resp = await self._await_chat(provider, prompt, system_prompt, limit)
            else:
                remaining = limit
                if limit == UNLIMITED_TIMEOUT:
                    await sem.acquire()
                else:
                    try:
                        await asyncio.wait_for(sem.acquire(), timeout=limit)
                    except asyncio.TimeoutError:
                        logger.warning("AI 并发闸排队超时：拿到槽位前已耗尽本次预算，按本地原因放弃")
                        return QUEUE_TIMEOUT
                    remaining = limit - (loop.time() - started)
                    if remaining <= 0:
                        sem.release()
                        logger.warning("AI 并发闸排队耗尽本次预算，按本地原因放弃")
                        return QUEUE_TIMEOUT
                try:
                    resp = await self._await_chat(provider, prompt, system_prompt, remaining)
                finally:
                    sem.release()
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

    async def _await_chat(self, provider, prompt: str, system_prompt: str,
                          limit: float):
        """真正发请求：超时/不限制两档在这里落地。"""
        call = provider.text_chat(
            prompt=prompt, session_id=None, contexts=[], image_urls=[],
            system_prompt=system_prompt,
        )
        if limit == UNLIMITED_TIMEOUT:
            return await call
        return await asyncio.wait_for(call, timeout=limit)

    def _accept_structure(self, text: str, n_positions: int) -> bool:
        """输出结构闸：这一份候选文本按当前口径放不放行（ai.structure_check）。

        loose  —— 只要求【第N张·位置】标记段数 ≥ 牌数（旧行为，默认）。
        strict —— 编号 1..N 各一次、【总结】0/1 次、位置名非空；不合规的候选
                  弃用并继续尝试下一个模型。
        """
        if self.structure_check == STRUCTURE_CHECK_STRICT:
            ok, why = validate_interpret_structure_strict(text, n_positions)
            if not ok:
                logger.warning(
                    f"AI 解读结构不合规（严格口径：{'/'.join(why)}），弃用该候选，继续尝试")
                return False
            return True
        if not validate_interpret_structure(text, n_positions):
            logger.warning("AI 解读未按【第N张·位置】结构输出，弃用该候选，继续尝试")
            return False
        return True

    async def interpret(self, umo: str | None, formation: str,
                        positions: list[str],
                        picks: list[dict], user_input: str, detail: str,
                        persona_eff: str | None = None,
                        budget: AiBudget | None = None) -> Interpretation | None:
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
        got_text = False  # 是否至少有一个候选产出了文本（区分“服务失败”与“结构失格”）
        tried = 0
        queued = 0  # 因本地并发闸排队超时退出的候选数：本地资源不足，不算 Provider 失败
        for provider in await self._gather_candidates(umo, bd, _STAGE_INTERPRET):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                logger.info(f"AI 解读 provider[{pid}] 处于失败冷却期，跳过")
                continue
            # 解读阶段不开阶段上限：ai_budget_seconds=0 时公开口径就是「没有上界」，
            # 用户显式关掉两项保护后不许再藏一条 120 秒的暗上限（README 已写明）
            timeout = bd.step_timeout(self.ai_timeout)
            if timeout is None:
                self._stage_stop(bd, _STAGE_INTERPRET)
                break  # 预算见底：剩下的候选与供应商回退都不再试（截止点不重置）
            tried += 1
            text = await self._chat_once(provider, prompt, system_prompt, timeout)
            if text is QUEUE_TIMEOUT:
                queued += 1
                continue
            if not isinstance(text, str) or not text:
                self._mark_fail(pid)  # 服务级失败：只冷却这个 provider
                continue
            got_text = True
            # 结构闸：按 ai.structure_check 的口径判定
            if not self._accept_structure(text, len(positions)):
                continue
            parsed = parse_interpretation(text)
            if parsed is None:
                continue
            return parsed
        if _STAGE_INTERPRET in bd.stopped:
            return None  # 预算掐掉的：原因已在 _stage_stop 里写过，别叠一条误导性的「全败」
        if tried == 0:
            logger.info("AI 解读没有可用候选（来源不可用或全部处于失败冷却期），直接使用本地牌义")
            return None
        if not got_text:
            if queued == tried:
                logger.warning("所有候选均因本地并发闸排队超时而未产出 AI 解读"
                               "（本地资源不足，未记 Provider 失败冷却）")
            elif queued:
                logger.warning(f"所有可用提供商均未产出 AI 解读（{queued} 个候选本地排队超时、"
                               "未记 Provider 冷却，其余已按 provider 记录失败冷却）")
            else:
                logger.warning("所有可用提供商均未产出 AI 解读（已按 provider 记录失败冷却）")
        else:
            logger.warning("所有候选均产出但结构失格，回退本地牌义（不进入失败冷却）")
        return None
