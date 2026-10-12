"""AI 解读：候选 Provider 链、超时切换、失败冷却——纯「找模型 → 发请求 → 拿文本」。

输入侧安全（越狱剥除 / 截断）与输出侧结构校验在 hardening.py；
提示词与格式协议在 prompts.py；
本模块只做模型调用本身。

说人话：牌抽好了，怎么让 AI 好好讲话，这里管——但它的嘴要听话，由 hardening 管。
"""
import asyncio
import time

from astrbot.api import logger

from hardening import (
    clip_question,
    normalize_injection_input,
    strip_injection_fragments,
    validate_interpret_structure,
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


class AiBudget:
    """一次占卜的 AI 总预算：所有 AI 阶段共用同一个截止点（硬上限）。

    为什么要有：单请求超时（ai_timeout）只管一次调用，管不住「两段 × 每段多个
    候选 × 供应商回退」的总时长——最坏情况下没有上界，用户就在那儿干等。
    本对象只保证一件事：**该次占卜 AI 阶段的等待有上界**；它不是整条消息端到端
    的上界（图片渲染、框架调度、平台发送各有各的耗时）。

    seconds <= 0 = 不限制（limited=False）：显式的高级选项，等于关掉总预算保护
    （AiInterpreter 构造时会就此留一条 warning）。

    clock 可注入：测试用假时钟推进，不睡墙钟。
    """

    def __init__(self, seconds, clock=time.monotonic):
        self.seconds = _budget_seconds(seconds)
        self.clock = clock
        self.limited = self.seconds > 0
        self.deadline = clock() + self.seconds if self.limited else None
        # 已因预算停手的阶段：同一阶段只报一次日志（多候选各自报一遍会把日志刷满）
        self.stopped: set[str] = set()

    def remaining(self) -> float:
        """剩余预算（秒）；不限制时返回 inf，调用方无需分支。"""
        if not self.limited:
            return float("inf")
        return self.deadline - self.clock()

    def step_timeout(self, per_call, stage_cap: float | None = None) -> float | None:
        """本次请求的有效超时 = min(单次超时, 剩余预算, 阶段上限)。

        返回 None = 不再发起新请求：预算耗尽，或剩下的不够跑一次有意义的请求
        （低于 AI_BUDGET_MIN_ATTEMPT，硬发出去就是白等一次超时）。
        不限制时返回 per_call（阶段上限照样生效）。
        """
        left = self.remaining()
        if left < AI_BUDGET_MIN_ATTEMPT:
            return None
        limit = float(per_call)
        if self.limited:
            limit = min(limit, left)
        if stage_cap is not None:
            limit = min(limit, float(stage_cap))
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
                 ai_budget: int = DEFAULT_AI_BUDGET):
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

    def _budget_stop(self, budget: AiBudget, stage: str) -> None:
        """预算让某阶段停手：留一行日志（同一阶段只报一次），随后按 None 回退本地牌义。"""
        if stage in budget.stopped:
            return
        budget.stopped.add(stage)
        left = budget.remaining()
        if left <= 0:
            logger.warning(f"AI 总预算已耗尽（{stage}，预算 {budget.seconds:g}s）："
                           "不再发起新请求，回退本地牌义")
        else:
            logger.warning(f"AI 总预算仅剩 {left:.1f}s（{stage}）："
                           "不够发起一次有效请求，回退本地牌义")

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

    async def spirit_line(self, event, cards: list, topic: str,
                          persona_eff, budget: AiBudget | None = None) -> str | None:
        """牌灵的一句话（聊天「牌灵的话」）：AI 按人设生成短句
        （要求 25 字内，实际接受 ≤40 字——宽容策略，避免过度丢弃）。

        cards: [(card, upright), ...] —— 羽签/每日牌运单张、羽时三刻/恋羽十字
        整签，一句话锚定整组牌面（贴合本签主题）。与解读共用候选链，但失败
        **不记失败冷却**——牌灵的话挂了自己回退池内签文就行，别把 provider
        冻住拖累解读。返回 None 表示没生成（调用方兜底）。
        """
        if not self.enable_ai:
            return None
        # 预算：调用方（一次占卜）传入共用对象；不传就本阶段自开一笔——直接调用
        # spirit_line 的老用法照样有上界，不会因为漏传而变成无界等待。
        # 牌灵的话是次要内容，另受 DEFAULT_SPIRIT_STAGE_BUDGET 阶段上限约束：它不能
        # 独吞整笔预算、把核心解读的时间耗光（但两者仍服从同一个总截止点）。
        bd = budget if budget is not None else self.new_budget()
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
        for provider in await self._provider_candidates(umo):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                continue
            timeout = bd.step_timeout(self.ai_timeout, DEFAULT_SPIRIT_STAGE_BUDGET)
            if timeout is None:
                self._budget_stop(bd, _STAGE_SPIRIT)
                break  # 预算见底：候选链剩下的不再试（共享截止点，不重置）
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
        for provider in await self._provider_candidates(umo):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                logger.info(f"AI 解读 provider[{pid}] 处于失败冷却期，跳过")
                continue
            timeout = bd.step_timeout(self.ai_timeout)
            if timeout is None:
                self._budget_stop(bd, _STAGE_INTERPRET)
                break  # 预算见底：剩下的候选与供应商回退都不再试（截止点不重置）
            tried += 1
            text = await self._chat_once(provider, prompt, system_prompt, timeout)
            if not text:
                self._mark_fail(pid)  # 服务级失败：只冷却这个 provider
                continue
            got_text = True
            if not validate_interpret_structure(text, len(positions)):
                logger.warning("AI 解读未按【第N张·位置】结构输出，弃用该候选，继续尝试")
                continue
            return text
        if _STAGE_INTERPRET in bd.stopped:
            return None  # 预算掐掉的：原因已在 _budget_stop 里写过，别叠一条误导性的「全败」
        if tried == 0:
            logger.info("AI 解读候选全部处于失败冷却期，直接使用本地牌义")
            return None
        if not got_text:
            logger.warning("所有可用提供商均未产出 AI 解读（已按 provider 记录失败冷却）")
        else:
            logger.warning("所有候选均产出但结构失格，回退本地牌义（不进入失败冷却）")
        return None
