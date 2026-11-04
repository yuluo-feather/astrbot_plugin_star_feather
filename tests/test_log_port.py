"""日志端口：中立门面的注入、引用稳定与参数原样转发。

核心层不再 import 框架日志；宿主在启动时把 logger 注入门面。这里守住三条：
已导入的引用在注入后仍有效、注入不增删 handler、级别与异常栈不经二次加工。
"""
import logging

import pytest
from data.plugins.astrbot_plugin_star_feather.core import log as log_mod


@pytest.fixture(autouse=True)
def _restore_target():
    original = log_mod.log._target
    yield
    log_mod.log._target = original


class _RecordingHandler(logging.Handler):
    """把记录收进列表；用子类而不是替换 emit，保持状态真正的记录器类型。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _recorder(name: str):
    """独立 logger + 记录器；名字带测试前缀并按用例区分，不污染别的 logger。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _RecordingHandler()
    logger.addHandler(handler)
    return logger, handler.records


def test_default_target_is_stdlib_namespace():
    assert log_mod.log.name == "star_feather"


def test_early_reference_survives_bind_and_forwards_verbatim():
    early = log_mod.log  # 模拟 `from ..core.log import log`
    logger, records = _recorder("sf.test.log_port.verbatim")
    log_mod.bind(logger)

    assert log_mod.log is early
    early.info("hello %s", "world")
    try:
        raise ValueError("boom")
    except ValueError:
        early.warning("bad", exc_info=True)

    assert [record.getMessage() for record in records] == ["hello world", "bad"]
    assert records[0].levelno == logging.INFO
    assert records[1].exc_info[0] is ValueError


def test_bind_is_idempotent_and_touches_no_handlers():
    logger, records = _recorder("sf.test.log_port.idempotent")
    before = list(logger.handlers)

    log_mod.bind(logger)
    log_mod.bind(logger)
    log_mod.log.warning("once")

    assert list(logger.handlers) == before
    assert len(records) == 1  # 重复绑定不产生重复记录


def test_bind_none_keeps_current_target():
    logger, records = _recorder("sf.test.log_port.keep")
    log_mod.bind(logger)

    log_mod.bind(None)
    log_mod.log.warning("still here")

    assert len(records) == 1
