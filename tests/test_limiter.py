"""限流纯逻辑单测：会话节流 / 每日计数。
——牌灵不小气，但也经不起连刷。"""
from limiter import cooldown_remaining, daily_remaining, daily_touch


# ---------- 会话节流 cooldown_remaining ----------
class TestCooldownRemaining:
    def test_disabled_or_empty_returns_zero(self):
        assert cooldown_remaining(1000, 1000, 0) == 0
        assert cooldown_remaining(0, 1000, 10) == 0

    def test_remaining_seconds(self):
        assert cooldown_remaining(1000, 1003, 10) == 7

    def test_expired_returns_zero(self):
        assert cooldown_remaining(1000, 1010, 10) == 0
        assert cooldown_remaining(1000, 1100, 10) == 0


# ---------- 每日计数 daily_remaining ----------
class TestDailyRemaining:
    def test_disabled_returns_minus_one(self):
        assert daily_remaining(0, {}, "20260824") == -1

    def test_no_record_returns_full(self):
        assert daily_remaining(5, {}, "20260824") == 5

    def test_new_day_resets(self):
        assert daily_remaining(5, {"date": "20260823", "count": 5}, "20260824") == 5

    def test_remaining_counts_down(self):
        assert daily_remaining(5, {"date": "20260824", "count": 2}, "20260824") == 3

    def test_exhausted_returns_zero(self):
        assert daily_remaining(5, {"date": "20260824", "count": 5}, "20260824") == 0
        assert daily_remaining(5, {"date": "20260824", "count": 9}, "20260824") == 0

    def test_corrupt_data_treated_as_no_record(self):
        # 日期对但 count 非数字：按无记录放行，不误伤
        assert daily_remaining(5, {"date": "20260824", "count": "x"}, "20260824") == 5
        assert daily_remaining(5, None, "20260824") == 5
        assert daily_remaining(5, "not-a-dict", "20260824") == 5


# ---------- 计数 +1 daily_touch ----------
class TestDailyTouch:
    def test_first_touch_today(self):
        assert daily_touch(None, "20260824") == {"date": "20260824", "count": 1}

    def test_increment_same_day(self):
        assert daily_touch({"date": "20260824", "count": 2}, "20260824") == {"date": "20260824", "count": 3}

    def test_new_day_resets_to_one(self):
        assert daily_touch({"date": "20260823", "count": 9}, "20260824") == {"date": "20260824", "count": 1}

    def test_corrupt_count_resets(self):
        assert daily_touch({"date": "20260824", "count": "x"}, "20260824") == {"date": "20260824", "count": 1}
