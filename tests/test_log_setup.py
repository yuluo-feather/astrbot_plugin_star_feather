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


class TestInterpretLoggerBinding:
    """interpret 挂共享 handler 时必须传 hardening 模块的 logger **对象**，不能靠名字。

    `setup_logging(logger, logging.getLogger("hardening"))` 只在 hardening.__name__
    恰好等于 "hardening" 时才对得上，而那个前提当前只靠 main.py 的 sys.path.insert
    （顶层导入）维持。一旦按 plugin-import-model 的方向改成包路径相对导入，__name__
    变成全路径，handler 就装到一个没人写的孤儿 logger 上——剥除命中/剥空日志静默消失，
    不报错，只是再也看不到。本用例把那个场景提前复现出来。
    """

    def test_handler_follows_hardening_logger_object(self, monkeypatch):
        """模拟「包路径导入」：换掉 hardening.logger 后重载 interpret，handler 必须跟过去。"""
        import importlib

        import hardening
        import interpret

        pkg_logger = logging.getLogger("pkg.star_feather.hardening")
        pkg_logger.handlers.clear()
        monkeypatch.setattr(hardening, "logger", pkg_logger)
        try:
            importlib.reload(interpret)      # 重跑模块顶部的 setup_logging
            marked = [h for h in pkg_logger.handlers if getattr(h, "_sf_log_handler", False)]
            assert marked, ("共享 handler 没装到 hardening.logger 上——"
                            "interpret 是按名字取的 logger，改成包路径导入后剥除日志会静默丢失")
        finally:
            monkeypatch.undo()
            importlib.reload(interpret)      # 恢复常规绑定，避免污染后续用例


class TestSetupLoggingSharedHandler:
    """同批 logger 必须共享同一个 handler 实例，不得各持一个句柄指向同一文件。

    旧实现：targets 只挑「还没装过的」，然后**无条件新建** handler——A 已有、B 没有
    时会给 B 新建一个，A/B 各持一句柄。TimedRotatingFileHandler 午夜轮转要 rename
    当前文件，另一个句柄仍指向改名前的 inode → 日志从此分裂到两个文件，正是
    docstring 警告「多头轮转同一文件会互相打架」的场景。
    """

    def test_partial_batch_reuses_existing_handler(self):
        lg_a = logging.getLogger("sf_test_shared_a")
        lg_b = logging.getLogger("sf_test_shared_b")
        lg_a.handlers.clear()
        lg_b.handlers.clear()
        assert log_setup.setup_logging(lg_a) is True            # 先只给 A 装
        a_handler = [h for h in lg_a.handlers if getattr(h, "_sf_log_handler", False)][0]
        assert log_setup.setup_logging(lg_a, lg_b) is True      # 再把 A、B 一起传
        b_marked = [h for h in lg_b.handlers if getattr(h, "_sf_log_handler", False)]
        assert b_marked, "B 没拿到 handler"
        assert b_marked[0] is a_handler, "B 拿了新句柄：同一文件两个 handler，午夜轮转会打架"

    def test_full_batch_shares_one_handler(self):
        lg_c = logging.getLogger("sf_test_shared_c")
        lg_d = logging.getLogger("sf_test_shared_d")
        lg_c.handlers.clear()
        lg_d.handlers.clear()
        log_setup.setup_logging(lg_c, lg_d)
        hc = [h for h in lg_c.handlers if getattr(h, "_sf_log_handler", False)][0]
        hd = [h for h in lg_d.handlers if getattr(h, "_sf_log_handler", False)][0]
        assert hc is hd, "同批两个 logger 没共享同一 handler"
