"""AI 解读：候选 Provider 链、超时切换、失败冷却——纯「找模型 → 发请求 → 拿文本」。

输入侧安全（越狱剥除 / 截断）与输出侧结构校验在 hardening.py；
运行日志落盘在 log_setup.py；提示词与格式协议在 prompts.py；
本模块只做模型调用本身。

说人话：牌抽好了，怎么让 AI 好好讲话，这里管——但它的嘴要听话，由 hardening 管。
"""
import asyncio
import logging
import time

from hardening import (
    clip_question,
    normalize_injection_input,
    strip_injection_fragments,
    validate_interpret_structure,
)
from log_setup import setup_logging
from prompts import SYSTEM_PROMPT_DIVINE, build_reading_prompt
from settings import DEFAULT_QUESTION_MAX_LEN

logger = logging.getLogger(__name__)

# 运行日志落盘（关键事件：剥除命中/剥空/结构失格/候选链切换/冷却）：
# 初始化失败只告警，不影响解读主流程。
setup_logging(logger)


class AiInterpreter:
    """AI 深度解读器：候选链 + 单请求超时 + 按 Provider 粒度的失败冷却。

    冷却粒度是「Provider」而不是插件全局（2026-08-29 调整）：
    单个模型挂掉只冷却它自己，其他模型与其他用户不受拖累；
    候选全部失败（全部冷却）时才回退本地牌义。

    对塔罗数据零依赖：牌面详情（detail）由调用方（StarTarot）格式化传入，
    本类只负责「找模型 → 发请求 → 拿文本」与失败兜底状态；
    问题清洗（截断/剥除）与输出校验委托 hardening。
    """

    def __init__(self, context, enable_ai: bool = True, ai_timeout: int = 30,
                 ai_cooldown: int = 60, ai_provider_id: str = "",
                 max_question_len: int = DEFAULT_QUESTION_MAX_LEN):
        self.context = context
        self.enable_ai = enable_ai
        self.ai_timeout = ai_timeout
        self.ai_cooldown = ai_cooldown
        self.ai_provider_id = ai_provider_id
        self.max_question_len = max_question_len
        # provider 级失败冷却：key=provider id → 上次服务失败时间戳。
        # 粒度到 provider（而非插件全局熔断）：单个模型挂掉只冷却它自己，
        # 其他模型与其他用户不受拖累；冷却期内该 provider 直接从候选链里跳过。
        self._fail_ts_by_provider: dict[str, float] = {}

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

    def _provider_candidates(self, umo: str | None) -> list:
        """收集候选：当前会话 > 全局默认 > 全部已加载，按 id 去重。

        get_using_provider 在会话配置异常时可能抛 ValueError，
        这里统一吞掉——宁可少一个候选，也不能让「选提供商」这一步本身出错。
        """
        seen = set()
        out = []

        def _add(provider):
            if provider is None:
                return
            try:
                pid = provider.meta().id
            except Exception:
                pid = id(provider)  # 拿不到 meta 时按对象身份去重
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

        try:
            _add(self.context.get_using_provider(umo))
        except Exception:
            pass
        try:
            _add(self.context.get_using_provider())
        except Exception:
            pass
        try:
            for provider in self.context.get_all_providers():
                _add(provider)
        except Exception:
            pass
        return out

    async def _chat_once(self, provider, prompt: str, system_prompt: str) -> str | None:
        """对单个候选发起解读请求：超时或异常一律返回 None，交给下一个候选。"""
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt, session_id=None, contexts=[], image_urls=[],
                    system_prompt=system_prompt,
                ),
                timeout=self.ai_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"AI 解读超时（>{self.ai_timeout}s），放弃该提供商")
            return None
        except Exception as e:
            logger.warning(f"AI 解读请求失败: {e}")
            return None
        try:
            return (resp.completion_text or "").strip() or None
        except Exception:
            return None

    async def interpret(self, event, formation: str, positions: list[str],
                        picks: list[dict], user_input: str, detail: str) -> str | None:
        """AI 结构化解读。返回 None 表示本轮没有 AI 解读（候选全部失败或全部处于冷却期），
        由调用方回退本地牌义。给用户的感觉是：解读要么有，要么是牌义，不会等成雕塑。
        """
        if not self.enable_ai:
            return None
        # 清洗链路顺序（技能规范）：归一化（先行）→ 截断 → 句式剥除
        user_input = normalize_injection_input(user_input)  # 归一化先行：全角/零宽/bidi 绕行先失效
        if self.max_question_len > 0:
            user_input = clip_question(user_input, self.max_question_len)  # 超长压缩（0=不压缩）
        user_input = strip_injection_fragments(user_input)  # 剥除夹带的越狱句式
        if not user_input:
            logger.info("问题剥除注入句式后为空，回退本地牌义")
            return None
        prompt = build_reading_prompt(user_input, formation, detail)
        umo = getattr(event, "unified_msg_origin", None)
        got_text = False  # 是否至少有一个候选产出了文本（区分“服务失败”与“结构失格”）
        tried = 0
        for provider in self._provider_candidates(umo):
            pid = self._provider_id(provider)
            if self._in_cooldown(pid):
                logger.info(f"AI 解读 provider[{pid}] 处于失败冷却期，跳过")
                continue
            tried += 1
            text = await self._chat_once(provider, prompt, SYSTEM_PROMPT_DIVINE)
            if not text:
                self._mark_fail(pid)  # 服务级失败：只冷却这一个 provider
                continue
            got_text = True
            if not validate_interpret_structure(text, len(positions)):
                logger.warning("AI 解读未按【第N张·位置】结构输出，弃用该候选，继续尝试")
                continue
            return text
        if tried == 0:
            logger.info("AI 解读候选全部处于失败冷却期，直接使用本地牌义")
            return None
        if not got_text:
            logger.warning("所有可用提供商均未产出 AI 解读（已按 provider 记录失败冷却）")
        else:
            logger.warning("所有候选均产出但结构失格，回退本地牌义（不进入失败冷却）")
        return None
