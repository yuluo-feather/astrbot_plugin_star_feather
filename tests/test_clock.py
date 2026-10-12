"""日期边界单测（⑨）：时区怎么定、今天是哪一天、边界会不会跑偏。

这一层的价值全在「唯一来源」四个字上：键（sf_daily_* / sf_spirit_* / sf_cmd_cnt_*）
都按今天切片，算错一次就是「刚过零点的人拿到昨天的牌」。所以用例分三段：
配置怎么读（含读不到、名字非法两种退路）、Clock 本身的行为（含跨时区跨零点）、
以及装配后各处是不是同一份 clock。
"""
import datetime
from zoneinfo import ZoneInfo

import pytest
from data.plugins.astrbot_plugin_star_feather import card_render
from data.plugins.astrbot_plugin_star_feather.clock import Clock, resolve_timezone


class _Ctx:
    """只做一件事：给出（或拒绝给出）框架全局配置。"""

    def __init__(self, config=None, boom=None):
        self._config = config
        self._boom = boom

    def get_config(self):
        if self._boom:
            raise self._boom
        return self._config


class TestResolveTimezone:
    def test_reads_framework_timezone(self):
        tz, name = resolve_timezone(_Ctx({"timezone": "Asia/Shanghai"}))
        assert name == "Asia/Shanghai"
        assert tz is not None and tz.key == "Asia/Shanghai"

    def test_blank_or_missing_means_local(self):
        # 框架配置说明原文就是「为空时使用系统默认时区」——这里照抄它的判据
        for cfg in ({}, {"timezone": ""}, {"timezone": "   "}, None):
            tz, name = resolve_timezone(_Ctx(cfg))
            assert (tz, name) == (None, "")

    def test_invalid_name_falls_back_and_warns(self, caplog):
        """名字非法退回本地，但必须留一条日志：静默退回会让日期不对变成疑案。"""
        with caplog.at_level("WARNING"):
            tz, name = resolve_timezone(_Ctx({"timezone": "Mars/Olympus"}))
        assert (tz, name) == (None, "")
        assert "Mars/Olympus" in caplog.text

    def test_get_config_raising_is_not_fatal(self, caplog):
        """读配置炸了也只是退化成本地时区：诊断入口不许把插件打崩。"""
        with caplog.at_level("WARNING"):
            tz, name = resolve_timezone(_Ctx(boom=RuntimeError("配置没这个键")))
        assert (tz, name) == (None, "")
        assert caplog.text

    def test_from_context_keeps_tz(self):
        assert Clock.from_context(_Ctx({"timezone": "UTC"})).tz is not None
        assert Clock.from_context(_Ctx({})).tz is None


class TestClock:
    """now_fn 注入：造「同一个瞬间、两个时区的今天」不必睡墙钟、也不动系统时钟。"""

    INSTANT = datetime.datetime(2026, 10, 12, 23, 30, tzinfo=datetime.timezone.utc)

    @classmethod
    def _at(cls, tz):
        return Clock(tz, now_fn=lambda: cls.INSTANT.astimezone(tz))

    def test_today_is_yyyymmdd(self):
        assert self._at(ZoneInfo("UTC")).today() == "20261012"

    def test_today_moves_with_timezone_across_midnight(self):
        """同一瞬间，UTC 还是 12 日，东八区已经是 13 日——日期键必须跟着时区走。"""
        assert self._at(ZoneInfo("Asia/Shanghai")).today() == "20261013"
        assert self._at(ZoneInfo("America/New_York")).today() == "20261012"

    def test_date_text_shape(self):
        # 月日不补零（旧卡面就是这个观感），周中文名用一位字符
        mon = datetime.datetime(2026, 9, 7, 8, 0, tzinfo=datetime.timezone.utc)
        assert Clock(None, now_fn=lambda: mon).date_text() == "9月7日·周一"
        sun = datetime.datetime(2026, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)
        assert Clock(None, now_fn=lambda: sun).date_text() == "2月1日·周日"

    def test_default_now_is_timezone_aware(self):
        """默认取时必须是 aware：naive 时间的 weekday() 取决于宿主，正是要消掉的东西。"""
        assert Clock().now().utcoffset() is not None
        assert Clock(ZoneInfo("Asia/Shanghai")).now().tzinfo is not None

    def test_configured_tz_beats_host_local(self):
        """配了时区就以它为准，不看宿主机——「容器 UTC / 面板东八区」这类组合靠这条。"""
        assert Clock(ZoneInfo("Asia/Shanghai")).now().utcoffset() == datetime.timedelta(hours=8)


class TestClockWiring:
    """装配之后：核心那一份 clock 得是各处共用的同一份，别各算各的今天。"""

    @pytest.fixture(autouse=True)
    def _isolated_image_root(self, tmp_path, monkeypatch):
        # 造真实例会开实例目录：指到 tmp，别往真实临时目录里丢东西
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "star_feather"))

    def test_core_clock_uses_framework_timezone(self):
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot

        t = StarTarot(_Ctx({"timezone": "Asia/Shanghai"}), None)
        assert t.clock.tz is not None and t.clock.tz.key == "Asia/Shanghai"

    def test_daily_shares_core_clock(self):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        from stubs import FakeContext

        t = StarTarot(_Ctx({"timezone": "UTC"}), None)
        d = DailyFortune(FakeContext(None), t)
        assert d.clock is t.clock, "今日牌运自己造了第二份时钟：日期键会跟核心的今天分家"

    def test_stub_tarot_still_gets_a_clock(self):
        """桩对象（单测/诊断）没有 clock：自建一份跟本地时区，不让日期键失联。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        from stubs import FakeContext

        d = DailyFortune(FakeContext(None), object())
        assert isinstance(d.clock, Clock)
        assert len(d.clock.today()) == 8
