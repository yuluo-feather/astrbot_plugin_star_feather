# 日期上下文

## 规则

**一次占卜只取一次日期。**

`ReadingService.prepare` 调 `Clock.snapshot()` 拿到 `(today, date_text)`，存进
`PreparedReading`；后续抽牌、牌灵缓存、解读缓存、海报渲染与人设种子全部用它，不再
各自读时钟。

## 为什么

「今天」在插件里有三个消费者：今日固定牌、每日解读/牌灵缓存、海报上的日期文本。
它们原先各自调用 `clock.today()` / `clock.date_text()`。一次请求要经过两段 AI（以秒计），
如果恰好跨过午夜：

- 牌按 27 日抽出；
- 牌灵与解读缓存按 28 日建键；
- 海报显示 28 日。

用户看到的是「28 日的海报，27 日的牌」。请求没有出错，只是日期上下文被切成了两半。

`Clock.snapshot()` 用同一个 `datetime.now()` 同时给出日期键与卡面文本，避免连这两者
都在零点一刻劈叉。

## 边界

- 每日计数（`LimitGate.check`）用 `clock.today()`，与今日牌运同一口径；它没有
  「一次占卜」的概念，每天的日期在请求内部取一次即可。
- 会话节流用 `time.time()` 的墙钟时间戳，与日期边界无关，两者各管各的。
- 框架配置了 `timezone` 时以它为准，没配则跟随系统本地时区（`adapters/clock.py`）。

## 出处

- 修复：[#53](https://github.com/yuluo-feather/astrbot_plugin_star_feather/issues/53)、
  PR [#54](https://github.com/yuluo-feather/astrbot_plugin_star_feather/pull/54)。
- 回归测试：`tests/test_entries.py::TestDailyDateContext`。
