"""运行日志落盘单测：路径候选链、handler 幂等安装、异常不阻断。
——出事了总得有个地方翻旧账。"""
import logging

import log_setup


class TestResolveLogPath:
    def test_returns_string(self):
        # 候选链最终必返回（含临时目录兜底），且为 .log 文件路径
        assert log_setup.resolve_log_path().endswith("star_feather.log")

    def test_candidates_fall_back_on_makedirs_failure(self, monkeypatch):
        # 前两个候选不可创建时，最后一个（临时目录）兜底
        import os
        import tempfile
        calls = {"n": 0}
        real_makedirs = os.makedirs

        def fake_makedirs(path, **kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError("no write")
            return real_makedirs(path, **kw)

        monkeypatch.setattr(os, "makedirs", fake_makedirs)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: "T:/tmp")
        p = log_setup.resolve_log_path()
        assert "star_feather.log" in p


class TestSetupLogging:
    def test_installs_handler_and_idempotent(self):
        logger = logging.getLogger("sf_test_log_setup")
        logger.handlers.clear()
        assert log_setup.setup_logging(logger) is True
        marked = [h for h in logger.handlers if getattr(h, "_sf_log_handler", False)]
        assert len(marked) == 1
        # 重复调用不重复安装
        assert log_setup.setup_logging(logger) is True
        marked = [h for h in logger.handlers if getattr(h, "_sf_log_handler", False)]
        assert len(marked) == 1

    def test_failure_returns_false_and_keeps_flow(self, monkeypatch):
        logger = logging.getLogger("sf_test_log_setup_fail")
        logger.handlers.clear()

        def boom(*a, **k):
            raise OSError("no file")

        monkeypatch.setattr(log_setup, "resolve_log_path", boom)
        assert log_setup.setup_logging(logger) is False
