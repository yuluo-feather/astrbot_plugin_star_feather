"""日志出口：中立门面，默认 stdlib，宿主可在启动时注入自己的 logger。

调用点一律 `from ..core.log import log`，不再直接 import 框架日志。门面对象
是稳定的：注入只换它委托的目标，已经持有的引用（`from ... import log`）继续有效。

优先级：绑定了宿主 logger 就用宿主（AstrBot 侧由 adapters/log.py 注入
`astrbot.api.logger`，仍落在 `astrbot.plugin.<插件名>` 路由）；没绑定或绑定失败
就用 stdlib 的 `star_feather` logger——核心层不因为宿主缺席而不可导入。
"""
import logging
from typing import Any

_DEFAULT = logging.getLogger("star_feather")
_DEFAULT.addHandler(logging.NullHandler())


class _LoggerProxy:
    """把日志方法原样转发给当前目标；参数、级别与异常栈都不经二次加工。"""

    __slots__ = ("_target",)

    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


log = _LoggerProxy(_DEFAULT)


def bind(target: Any) -> None:
    """注入宿主 logger；None 视为“不注入”，保持当前目标。

    只做目标替换，不给任何 logger 增删 handler，因此重复调用不会产生重复输出。
    """
    if target is not None:
        log._target = target
