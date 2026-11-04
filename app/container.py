"""应用装配：唯一一处把配置、适配器与各服务拼起来的地方。"""
from dataclasses import dataclass

from ..adapters.astrbot_io import PluginKV
from ..adapters.clock import Clock
from ..adapters.deliver import Deliverer
from ..adapters.llm import AstrBotProviderGateway
from ..adapters.presenter import Presenter
from ..ai.chain import AiInterpreter
from ..core.settings import Settings, parse
from ..render import new_instance_image_dir
from ..services.daily import DailyFortune
from ..services.gating import LimitGate
from ..services.reading import ReadingService
from ..services.render import Renderer


@dataclass(frozen=True)
class Container:
    settings: Settings
    clock: Clock
    kv: PluginKV
    interpreter: AiInterpreter
    daily: DailyFortune
    gate: LimitGate
    renderer: Renderer
    reading: ReadingService
    presenter: Presenter


def build_container(context, plugin, config) -> Container:
    """按一份原始配置装配全部依赖。"""
    settings = parse(config)
    clock = Clock.from_context(context)
    kv = PluginKV(plugin)
    interpreter = AiInterpreter(
        AstrBotProviderGateway(context),
        enable_ai=settings.ai.enable_ai,
        ai_timeout=settings.ai.timeout,
        ai_cooldown=settings.ai.fail_cooldown,
        ai_provider_id=settings.ai.provider_id,
        max_question_len=settings.ai.question_max_len,
        persona=settings.ai.persona,
        ai_budget=settings.ai.budget_seconds,
        structure_check=settings.ai.structure_check,
        max_concurrency=settings.ai.max_concurrency,
        all_providers_fallback=settings.ai.all_providers_fallback,
    )
    daily = DailyFortune(kv, clock, settings.ai.persona, interpreter,
                         ai_enabled=settings.ai.enable_ai)
    gate = LimitGate(kv, settings.tool.cmd_rate_limit, settings.limit.daily_count, clock)
    renderer = Renderer(new_instance_image_dir(), settings.output.send_mode)
    reading = ReadingService(
        settings=settings, clock=clock, daily=daily,
        interpreter=interpreter, renderer=renderer)
    presenter = Presenter(Deliverer(
        settings.output.segment_size, settings.output.disclaimer))
    return Container(settings, clock, kv, interpreter, daily, gate, renderer,
                     reading, presenter)
