"""牌灵的话单测：`daily.spirit_cached` 的 AI 生成与当日缓存、`interpret.spirit_line`
的候选链与失败回退、`build_spirit_line_prompt` 的提示词形状。

从原 `test_core` 拆出来的一支（2026-10-17）。
"""
import asyncio
import logging
import types

import pytest
from data.plugins.astrbot_plugin_star_feather.dailylines import pick_signature
from data.plugins.astrbot_plugin_star_feather.prompts import (
    SPIRIT_PROMPT_V,
    build_spirit_line_prompt,
)
from data.plugins.astrbot_plugin_star_feather.tarot_data import (
    TAROT_CARDS,
)
from stubs import CARD, EVENT, PICK, FakeContext, FakeProvider


# ---------- 牌灵的话：daily.spirit_cached（AI 生成 + 当日缓存） ----------
class TestSpiritCache:
    """牌灵的话：AI 成功→缓存当日同句；AI 失败→池内当日句且不写缓存（下次再试）。
    多张整签（普通占卜）与单张（每日牌运）共用：缓存键=当日+牌组指纹。"""

    @staticmethod
    def _daily(kv, line="AI 说的那句"):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_spirit(event, cards, topic, persona_eff, budget=None):
            calls.append((cards, topic, persona_eff))
            return line

        tarot = types.SimpleNamespace(interpreter=types.SimpleNamespace(spirit_line=fake_spirit))
        return DailyFortune(_Ctx(), tarot), calls

    def test_ai_success_caches_and_reuses(self):
        from time import strftime
        kv = {}
        df, calls = self._daily(kv)
        l1 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l1 == "AI 说的那句" and len(calls) == 1
        l2 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l2 == "AI 说的那句" and len(calls) == 1  # 缓存复用：不再调 AI
        cache = next(v for k, v in kv.items() if k.startswith("sf_spirit_"))
        assert cache["date"] == strftime("%Y%m%d") and cache["line"] == "AI 说的那句"
        assert cache["sign"] == f"{PICK['card']['cn']}:1"  # 牌组指纹（卡名:正逆）
        assert cache["v"] == SPIRIT_PROMPT_V  # prompt 版本号：文案迭代旧缓存自动失效

    def test_concurrent_misses_share_one_generated_line(self):
        """同一（用户, 牌组）并发 miss：只调一次 AI，等待者拿到同一句。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        kv, calls = {}, []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def slow_spirit(event, cards, topic, persona_eff, budget=None):
            calls.append(1)
            await asyncio.sleep(0.02)   # 制造重叠窗口：两条协程都会 miss
            return "并发同一句"

        tarot = types.SimpleNamespace(interpreter=types.SimpleNamespace(spirit_line=slow_spirit))
        df = DailyFortune(_Ctx(), tarot)

        async def go():
            return await asyncio.gather(
                df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None),
                df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))

        a, b = asyncio.run(go())
        assert a == b == "并发同一句"
        assert len(calls) == 1, f"并发 miss 应只调用一次 AI，实际 {len(calls)}"

    def test_two_instances_share_the_inflight_registry(self):
        """同一进程里两代 DailyFortune 并存：并发 miss 也只调一次 AI。

        登记表若退化成实例属性，热重载期间新旧实例各问一次 AI，会给出两句
        不同的话并互相覆盖缓存。
        """
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        kv, calls = {}, []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def slow_spirit(event, cards, topic, persona_eff, budget=None):
            calls.append(1)
            await asyncio.sleep(0.02)
            return "跨实例同一句"

        tarot = types.SimpleNamespace(interpreter=types.SimpleNamespace(spirit_line=slow_spirit))
        a, b = DailyFortune(_Ctx(), tarot), DailyFortune(_Ctx(), tarot)

        async def go():
            return await asyncio.gather(
                a.spirit_cached(EVENT, "u1", [PICK], "今日运势", None),
                b.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))

        l1, l2 = asyncio.run(go())
        assert l1 == l2 == "跨实例同一句"
        assert len(calls) == 1, f"跨实例并发 miss 应只调用一次 AI，实际 {len(calls)}"

    def test_cancelled_waiter_does_not_cancel_the_shared_future(self):
        """等待者被取消不能把共享 Future 一起取消：否则所有者 set_result 抛
        InvalidStateError，整条占卜一起失败。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        kv, calls = {}, []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def slow_spirit(event, cards, topic, persona_eff, budget=None):
            calls.append(1)
            await asyncio.sleep(0.05)
            return "共享那句"

        tarot = types.SimpleNamespace(interpreter=types.SimpleNamespace(spirit_line=slow_spirit))
        df = DailyFortune(_Ctx(), tarot)

        async def go():
            owner = asyncio.create_task(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
            await asyncio.sleep(0.01)          # 让 owner 先登记 in-flight
            waiter = asyncio.create_task(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
            await asyncio.sleep(0.01)          # 让 waiter 进入等待
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            return await owner

        assert asyncio.run(go()) == "共享那句"
        assert len(calls) == 1

    def test_new_day_does_not_join_yesterdays_inflight(self):
        """跨午夜的生成属于昨天：新一天的请求必须重新生成，不能拿昨天的文案。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        kv, calls = {}, []

        class _Clock:
            day = "20261017"

            def today(self):
                return self.day

            def date_text(self):
                return "2026.10.17"

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        clock = _Clock()
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_spirit(event, cards, topic, persona_eff, budget=None):
            calls.append(clock.today())
            if len(calls) == 1:
                started.set()
                await release.wait()
                return "昨天那句"
            return "今天那句"

        tarot = types.SimpleNamespace(interpreter=types.SimpleNamespace(spirit_line=slow_spirit),
                                      clock=clock)
        df = DailyFortune(_Ctx(), tarot)

        async def go():
            first = asyncio.create_task(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
            await started.wait()
            clock.day = "20261018"             # 跨午夜
            second = await df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None)
            release.set()
            return await first, second

        l1, l2 = asyncio.run(go())
        assert (l1, l2) == ("昨天那句", "今天那句")
        assert calls == ["20261017", "20261018"]

    def test_spirit_writes_back_even_on_read_failure(self):
        """读故障仍写回——方向与 pick/interp 的 if ok 门相反，是有意的。

        判据＝写回是否依赖旧数据：pick_cached / interp_cached 读旧 dict 再合并写回，读故障
        时旧数据缺失、写回会把有效内容覆盖成空壳，所以必须门住；本函数是纯覆盖写（只写
        v/date/sign/line，不读旧值），读故障不影响写入正确性。机械地「统一」成门住，会让
        故障恢复前的重复查询重新生成，AI 措辞可能不同，破坏「当日同牌组同一句」的承诺。
        """
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        kv = {}
        calls = []

        class _FlakyCtx:
            def __init__(self):
                self.n = 0

            async def get_kv_data(self, key, default=None):
                self.n += 1
                if self.n == 1:                     # 首次读瞬时故障 → ok=False
                    raise RuntimeError("kv 瞬时故障")
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_spirit(event, cards, topic, persona_eff, budget=None):
            calls.append(topic)
            return "故障期间生成的那句"

        tarot = types.SimpleNamespace(interpreter=types.SimpleNamespace(spirit_line=fake_spirit))
        df = DailyFortune(_FlakyCtx(), tarot)
        l1 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l1 == "故障期间生成的那句" and len(calls) == 1
        cache = next(v for k, v in kv.items() if k.startswith("sf_spirit_"))
        assert cache["line"] == "故障期间生成的那句"   # 读故障也写回：覆盖写不依赖旧值
        l2 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l2 == l1 and len(calls) == 1           # 恢复后命中缓存：同签同句成立

    def test_ai_fail_falls_back_static_without_cache(self):
        from time import strftime
        kv = {}
        df, calls = self._daily(kv, line=None)  # AI 失败
        l1 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        sig = pick_signature(PICK["card"], True, "u1", strftime("%Y%m%d"))
        assert l1 == sig
        assert not any(k.startswith("sf_spirit_") for k in kv)  # 不写缓存
        l2 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l2 == sig and len(calls) == 2  # 每次失败都再试 AI

    def test_stale_cache_without_version_regenerates(self):
        # prompt 文案迭代后 SPIRIT_PROMPT_V +1：无版本号的旧缓存不命中（旧句不复活）
        from time import strftime
        kv = {"sf_spirit_u1": {"date": strftime("%Y%m%d"),
                               "sign": f"{PICK['card']['cn']}:1", "line": "旧谜语句"}}
        df, calls = self._daily(kv, line="新直白句")
        l1 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l1 == "新直白句" and len(calls) == 1  # 重新生成，不复用旧句

    def test_cache_invalidated_on_other_card(self):
        kv = {}
        df, calls = self._daily(kv, line="A 句")
        l1 = asyncio.run(df.spirit_cached(EVENT, "u1", [PICK], "今日运势", None))
        assert l1 == "A 句" and len(calls) == 1
        other = TAROT_CARDS[1]  # 不同牌
        l2 = asyncio.run(df.spirit_cached(EVENT, "u1", [{"card": other, "upright": True}],
                                          "今日运势", None))
        assert l2 == "A 句" and len(calls) == 2  # 换牌：重新生成（AI 再次开口）

    def test_cache_keyed_on_whole_set(self):
        # 多张整签锚定：同人同日同牌组同句，牌组变化（正逆）即新句
        kv = {}
        df, calls = self._daily(kv, line="整签句")
        picks3 = [PICK, {"card": TAROT_CARDS[1], "upright": False},
                  {"card": TAROT_CARDS[2], "upright": True}]
        l1 = asyncio.run(df.spirit_cached(EVENT, "u1", picks3, "感情", None))
        assert l1 == "整签句" and len(calls) == 1
        l2 = asyncio.run(df.spirit_cached(EVENT, "u1", picks3, "感情", None))
        assert l2 == "整签句" and len(calls) == 1  # 同牌组：缓存复用一个 AI 句
        flip = [PICK, {"card": TAROT_CARDS[1], "upright": True},
                {"card": TAROT_CARDS[2], "upright": True}]  # 第二张正逆翻转
        l3 = asyncio.run(df.spirit_cached(EVENT, "u1", flip, "感情", None))
        assert l3 == "整签句" and len(calls) == 2  # 牌组变：重新生成

    def test_no_uid_returns_static_without_ai(self):
        from time import strftime
        df, calls = self._daily({})
        l1 = asyncio.run(df.spirit_cached(EVENT, "", [PICK], "今日运势", None))
        assert l1 == pick_signature(PICK["card"], True, "", strftime("%Y%m%d"))
        assert len(calls) == 0

# ---------- 牌灵的话：interpret.spirit_line 生成 ----------
class TestSpiritLine:
    def _interp(self, provider, enable=True):
        from data.plugins.astrbot_plugin_star_feather.interpret import AiInterpreter
        return AiInterpreter(FakeContext(provider, [provider]), enable, 5, 60, "", 200)

    def test_success_returns_cleaned_line(self):
        p1 = FakeProvider("p1", result="「别急，路会慢慢亮起来的。」")
        i = self._interp(p1)
        out = asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", None))
        assert out == "别急，路会慢慢亮起来的。"
        assert p1.calls == 1

    def test_enable_off_no_call(self):
        p1 = FakeProvider("p1")
        i = self._interp(p1, enable=False)
        assert asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", None)) is None
        assert p1.calls == 0

    def test_all_fail_returns_none(self):
        p1 = FakeProvider("p1", exc=RuntimeError("down"))
        i = self._interp(p1)
        assert asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", None)) is None

    def test_uses_persona_style_in_prompt(self):
        p1 = FakeProvider("p1", result="哼，就帮你看这一眼。")
        i = self._interp(p1)
        asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", "tsundere"))
        assert "牌灵一句话签文" in p1.last_prompt  # 人设签文样板进 prompt


    def test_quotes_only_returns_none_not_crash(self):
        """模型只回一个引号或纯空白时不得下标越界。

        旧实现 strip 后直接 splitlines()[0]：空列表 IndexError 会把整个
        spirit_line 协程抛掉，而 daily.spirit_cached 没兜这一句，会一路冒到
        入口最外层（用户侧变成「这场占卜断了」）。
        """
        p1 = FakeProvider("p1", result="「」")
        i = self._interp(p1)
        assert asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", None)) is None

    def test_overlong_candidate_logs_then_falls_through(self, caplog):
        """超长输出不再是静默走到循环末尾：留一行日志，然后继续下一候选。

        interpret() 的候选链四步全有日志（冷却跳过 / 结构失格 / tried==0 / 全败），
        spirit_line 原本零条——「牌灵的话为何老回退池内签文」在日志里查不出原因。
        「只有换行使首行剥完为空」走同一条不合规分支，共用这一行日志。
        """
        from data.plugins.astrbot_plugin_star_feather.interpret import AiInterpreter
        long_text = "这句话故意写得很长，长到超过四十个字，专门用来触发那条不合规分支" * 2
        p1 = FakeProvider("p1", result=long_text)
        p2 = FakeProvider("p2", result="短句就位。")
        i = AiInterpreter(FakeContext(p1, [p1, p2]), True, 5, 60, "", 200)
        with caplog.at_level(logging.INFO):
            out = asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", None))
        assert out == "短句就位。"  # 绕开这一候选，下一候选照用
        assert any("输出不合规" in r.getMessage() for r in caplog.records)
        assert not i._fail_ts_by_provider, "不合规是内容问题而非服务失败：不许记冷却"

    def test_quotes_only_logs_why_it_gave_up(self, caplog):
        """剥引号后为空那支同样要留痕：不记冷却，也不能是哑的。"""
        p1 = FakeProvider("p1", result="「」")
        i = self._interp(p1)
        with caplog.at_level(logging.INFO):
            assert asyncio.run(i.spirit_line(EVENT, [(CARD, True)], "感情", None)) is None
        assert any("剥引号后为空" in r.getMessage() for r in caplog.records)

# ---------- 牌灵的话：build_spirit_line_prompt ----------
class TestSpiritPrompt:
    def test_persona_style_included(self):
        p = build_spirit_line_prompt([("圣杯侍从", False)], "感情", "tsundere")
        assert "圣杯侍从·逆位" in p and "牌灵一句话签文" in p

    def test_multi_card_set_joined(self):
        # 多张整签：牌面全部串进一句话模板（羽时三刻/恋羽十字锚定整签）
        p = build_spirit_line_prompt([("星币五", True), ("权杖王后", True), ("宝剑十", False)],
                                     "感情", None)
        assert "星币五·正位" in p and "权杖王后·正位" in p and "宝剑十·逆位" in p
        assert "牌灵一句话签文" not in p

    def test_off_neutral(self):
        p = build_spirit_line_prompt([("圣杯侍从", True)], "", None)
        assert "牌灵一句话签文" not in p

    def test_plain_language_in_all_personas(self):
        # 直白约束全局生效：三格人设 + 中立分支都要求「比喻一读就懂」
        for eff in ("tsundere", "gentle", "mystic", None):
            p = build_spirit_line_prompt([("圣杯侍从", True)], "感情", eff)
            assert "一读就懂" in p, eff

    def test_version_is_content_fingerprint(self, monkeypatch):
        """版本号由内容指纹生成：改模板或人设签名风格，当日缓存自动失效。

        旧实现是手工常量配「改文案记得 +1」的注释——靠人记着的隐性契约，迟早
        漏改，漏了就是旧句复活。
        """
        from data.plugins.astrbot_plugin_star_feather import prompts
        assert prompts.SPIRIT_PROMPT_V == prompts._spirit_prompt_fingerprint()
        base = prompts.SPIRIT_PROMPT_V
        monkeypatch.setattr(prompts, "_SPIRIT_TEMPLATE", prompts._SPIRIT_TEMPLATE + "改一笔")
        assert prompts._spirit_prompt_fingerprint() != base
