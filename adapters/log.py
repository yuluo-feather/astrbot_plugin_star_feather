"""AstrBot 日志绑定：把框架 logger 注入中立门面。

`astrbot.api.logger` 是按调用模块名路由的代理，插件包内的模块调用时仍落到
`astrbot.plugin.<插件名>`；注入后不重发记录，级别、参数与异常栈原样保留。
绑定失败只降级到 stdlib，不允许带走插件加载。
"""
from astrbot.api import logger as host_logger

from ..core.log import bind


def bind_host_logger() -> None:
    """幂等：只换门面的目标，不增删 handler，不会产生重复日志。"""
    try:
        bind(host_logger)
    except Exception:
        # 日志绑定是增强项，失败时核心层仍走 stdlib；这里刻意不向上抛
        pass
