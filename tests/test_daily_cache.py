"""每日解读缓存单测：分桶合并写、跨天作废、主题桶上限、KV 降级、脏数据与空解读。

从原 `test_core` 拆出来的一支（2026-10-17）：这一份只管「记事本怎么存、坏了怎么办」，
不碰入口编排。
"""
import asyncio
import json
import types

from data.plugins.astrbot_plugin_star_feather.daily import (
    _daily_pick,
)
from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
from data.plugins.astrbot_plugin_star_feather.tarot_data import (
    TAROT_CARDS,
)
from stubs import PICK

T = StarTarot.__new__(StarTarot)  # 绕过 __init__（不依赖 Context），供抽牌/编排用例复用


# ---------- 每日缓存合并写（解读分桶保留 / 跨天作废） ----------
class TestDailyCacheMerge:
    """模拟 KV：解读先被写入时，抽牌重生成不得覆盖丢掉解读分桶。

    字段名是 interps（主题指纹 → 解读文本的分桶 dict，见 daily._norm_topic /
    interp_cached），不是 interp。旧用例写的是 interp（实现里根本没有这个键），
    合并写原样保留未知键所以恒绿——既测不到真字段的保留语义，也测不到跨天清理。
    """

    @staticmethod
    def _plugin(kv_store):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv_store.get(key, default)

            async def put_kv_data(self, key, value):
                kv_store[key] = value

        p.daily = DailyFortune(_Ctx(), T)
        return p

    def test_keeps_existing_interps_when_regenerating(self):
        import time as _time
        today = _time.strftime("%Y%m%d")
        slots = {"（今日牌运）": "旧解读文本"}
        # 只有解读分桶、无 card（模拟「解读先写入、抽牌重生成」的异常时序）
        kv = {"sf_daily_u1": {"date": today, "interps": dict(slots)}}
        p = self._plugin(kv)
        result = asyncio.run(p.daily.pick_cached("u1"))
        assert result is not None
        cached = kv["sf_daily_u1"]
        # 牌被补上，且当天已写入的解读分桶原样保留（不被抽牌重生成覆盖）
        assert cached["card"] in TAROT_CARDS and isinstance(cached["upright"], bool)
        assert cached["interps"] == slots
        assert cached["date"] == today

    def test_fresh_cache_has_card_no_interps(self):
        kv = {}
        p = self._plugin(kv)
        asyncio.run(p.daily.pick_cached("u1"))
        cached = kv["sf_daily_u1"]
        assert cached["card"] in TAROT_CARDS
        assert "interps" not in cached  # 首次写入不虚构解读分桶

    def test_stale_day_interps_dropped_on_regenerate(self):
        """跨天：旧解读分桶随抽牌重生成一并作废，不盖章复活。"""
        import time as _time
        stale = {"（今日牌运）": "昨天的解读", "最近事业运": "前天的解读"}
        kv = {"sf_daily_u1": {"date": "19700101", "card": TAROT_CARDS[0],
                              "upright": True, "interps": dict(stale)}}
        p = self._plugin(kv)
        result = asyncio.run(p.daily.pick_cached("u1"))
        assert result is not None
        cached = kv["sf_daily_u1"]
        assert cached["date"] == _time.strftime("%Y%m%d")
        assert "interps" not in cached      # 跨天作废：旧分桶不得跟着新日期复活
        assert cached["card"] in TAROT_CARDS
class TestDailyCachedCardAnchor:
    """缓存里的牌按 id 锚点回查牌库，而不是整字典值相等。

    值相等的代价：把「存储层序列化/反序列化必须逐字段等值」写成隐式契约——
    字段类型只要漂移一次（如 num 从 str 变 int），命中就全部失配、当天缓存静默失效。
    id 是牌库明面声明的唯一锚点；命中后回查牌库对象，顺带避免旧副本的字面量
    被当成当前牌义。
    """

    @staticmethod
    def _fortune(kv_store):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv_store.get(key, default)

            async def put_kv_data(self, key, value):
                kv_store[key] = value

        return DailyFortune(_Ctx(), T)

    def test_type_drifted_copy_still_hits(self):
        """字段类型漂移后仍须命中：只有牌库里真的没有这个 id 才算缓存不可用。"""
        import copy
        import time as _time
        today = _time.strftime("%Y%m%d")
        det_card, _det_upright = _daily_pick("u1", today)
        cached_card = next(c for c in TAROT_CARDS if c is not det_card)
        drifted = copy.deepcopy(cached_card)
        drifted["num"] = int(drifted["num"])        # 类型漂移：整字典值相等不再成立
        assert drifted not in TAROT_CARDS           # 前提：旧判据在这份副本上确实失配
        kv = {"sf_daily_u1": {"date": today, "card": drifted, "upright": True}}
        _formation, _positions, picks = asyncio.run(self._fortune(kv).pick_cached("u1"))
        # 比牌 id 而不是对象身份：牌库对象会在 deepcopy 与缓存里往返，id 才是这里真正
        # 要断言的东西（「同名模块两份」那类身份问题已由包内相对导入收口）
        assert picks[0]["card"]["id"] == cached_card["id"]   # 命中缓存：返回的是缓存那张牌
        assert picks[0]["card"]["id"] != det_card["id"]      # 而不是「落回重算」出的确定性牌
        assert isinstance(picks[0]["card"]["num"], str)       # 返回的是牌库权威对象：num 仍是 str，
                                                              # 不是缓存里那份被我改了类型的副本
        assert picks[0]["upright"] is True

    def test_unknown_id_falls_back_to_deterministic_pick(self):
        """牌库里没有的 id 一律不信任：按确定性函数出牌，当天牌不会变。"""
        import time as _time
        today = _time.strftime("%Y%m%d")
        det_card, det_upright = _daily_pick("u1", today)
        kv = {"sf_daily_u1": {"date": today, "card": {"id": "no_such_card"}, "upright": True}}
        _formation, _positions, picks = asyncio.run(self._fortune(kv).pick_cached("u1"))
        assert picks[0]["card"]["id"] == det_card["id"]
        assert picks[0]["upright"] == det_upright

    def test_non_dict_card_value_degrades_quietly(self):
        """缓存里放 list / 字符串这类非字典时照常降级，不把占卜入口带崩。"""
        import time as _time
        today = _time.strftime("%Y%m%d")
        for bad in (["不是字典"], "不是字典", 12345):
            kv = {"sf_daily_u1": {"date": today, "card": bad, "upright": True}}
            out = asyncio.run(self._fortune(kv).pick_cached("u1"))
            assert out is not None and out[0] == "羽签"



class TestDailyInterpTopic:
    """解读缓存按主题指纹（_norm_topic）分桶：同主题复用、换主题现场生成。

    旧版无主题区分：同一天问什么都返回当天第一段解读（「事业运」的解读
    被「感情运」「学业运」共用），这是「不管问什么答案都一样」的元凶之一。
    """

    @staticmethod
    def _daily(kv):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        return DailyFortune(_Ctx(), types.SimpleNamespace(_ai_interpret=fake_ai)), calls

    def test_topic_table_decoupled_from_daily_judgement(self):
        """分桶与判定解耦：有具体主题仍走每日固定牌，但解读不与泛问共用。

        SPECIFIC_WORDS 一度一份词表两处用（既是 _is_daily_request 的排除条件、
        又是 _norm_topic 的分桶判据），于是「今天财运怎么样」只有两种命：与
        「今天运势」共用解读，或被踢出每日牌运、每问重抽。拆表后两者兼得——
        牌仍是当天同一张（_is_daily_request 为真），解读各归各（topic 不同）。
        """
        from data.plugins.astrbot_plugin_star_feather.daily import (
            _GENERIC_TOPIC,
            _is_daily_request,
            _norm_topic,
        )

        assert _is_daily_request("今天财运怎么样")
        assert _norm_topic("今天财运怎么样") != _GENERIC_TOPIC
        # 无主题的泛问仍共享同一段（防反复问刷版本）
        for q in ("今天运势", "看看今天的运势", "每日一签"):
            assert _norm_topic(q) == _GENERIC_TOPIC, q

    def test_same_specific_topic_reuses(self):
        kv = {}
        df, calls = self._daily(kv)
        async def go():
            a = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            b = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            return a, b
        a, b = asyncio.run(go())
        assert a == b == "解读:今天感情运势"
        assert len(calls) == 1  # 同主题只生成一次

    def test_different_topic_regenerates(self):
        kv = {}
        df, calls = self._daily(kv)
        async def go():
            a = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "最近事业运")
            b = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            c = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "最近事业运")
            return a, b, c
        a, b, c = asyncio.run(go())
        assert a == "解读:最近事业运" and b == "解读:今天感情运势"
        assert c == "解读:最近事业运"  # 换主题后回来仍复用
        assert len(calls) == 2  # 两个主题各生成一次，不串答

    def test_generic_question_shared_all_day(self):
        kv = {}
        df, calls = self._daily(kv)
        async def go():
            a = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "")
            b = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "看看今天的运势")
            return a, b
        a, b = asyncio.run(go())
        # 泛问（无具体主题词）统一归为当日牌运，共享同一段解读（防刷版本）
        assert a == b == "解读:（今日牌运）"
        assert len(calls) == 1

    def test_concurrent_topics_both_persist(self):
        """并发问两个主题：两个分桶都要在。

        加锁 + 写回前重读之前，两条协程各自读旧值再写回，后写的会把先写的整个覆盖
        （实测 KV 里只剩一个分桶，另一个主题白花一次 AI 调用）。
        """
        from data.plugins.astrbot_plugin_star_feather.daily import (
            DailyFortune,
            _norm_topic,
        )
        kv = {}

        class _SlowCtx:
            async def get_kv_data(self, key, default=None):
                await asyncio.sleep(0.005)   # 制造交错窗口
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                await asyncio.sleep(0.005)
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            await asyncio.sleep(0.01)        # AI 调用期间另一条协程会进来
            return f"解读:{clean}"

        df = DailyFortune(_SlowCtx(), types.SimpleNamespace(_ai_interpret=fake_ai))
        t1, t2 = "今天感情运势", "最近事业运"

        async def go():
            return await asyncio.gather(
                df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], t1),
                df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], t2))

        a, b = asyncio.run(go())
        assert a == f"解读:{t1}" and b == f"解读:{t2}"
        assert set(kv["sf_daily_u1"]["interps"]) == {_norm_topic(t1), _norm_topic(t2)}

    def test_stale_day_slots_dropped_when_pick_read_recovers(self):
        """跨天 + pick_cached 读故障：写入端自己判跨天，昨天的分桶不得盖章复活。

        时序：pick_cached 读故障（if ok 门跳过写回、也跳过 interps 清理）→
        interp_cached 这次读成功 → 旧分桶若被原样沿用，就会跟着今天的新日期
        写回去，当天同主题直接复用昨天的解读（写入端只认 data["date"] == today）。
        """
        stale = {"最近事业运": "昨天的解读"}
        kv = {"sf_daily_u1": {"date": "19700101", "card": None, "upright": True,
                              "interps": dict(stale)}}
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        calls = []

        class _FlakyCtx:
            def __init__(self):
                self.n = 0

            async def get_kv_data(self, key, default=None):
                self.n += 1
                if self.n == 1:                    # 第一次读（pick_cached）瞬时故障
                    raise RuntimeError("kv 瞬时故障")
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        df = DailyFortune(_FlakyCtx(), types.SimpleNamespace(_ai_interpret=fake_ai))

        async def go():
            await df.pick_cached("u1")             # 读故障：不写回、不清理
            a = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            b = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "最近事业运")
            return a, b

        a, b = asyncio.run(go())
        assert a == "解读:今天感情运势"
        assert b == "解读:最近事业运"              # 昨天的分桶已作废：现场重生成
        assert len(calls) == 2


class TestDailyInterpCap:
    """解读分桶的硬上界（⑧）：DAILY_INTERP_MAX = 20，淘汰「最早插入」的那一桶（FIFO）。

    没有上界时桶只增不减——跨天由 pick_cached 的 interps 清理整体作废，当天换主题
    不回收，KV 条目随主题数线性长大。
    口径是 FIFO 不是 LRU：命中路径根本不写回（见 interp_cached 的命中判定），读过
    的桶不会因此变「新」，被挤掉的永远是**最早插入**的那一桶。

    KV 桩按宿主真实形状写：落进去的是 JSON 文本（宿主 Preference.value 就是 JSON
    列），读出来的是重新解析的对象。「插入顺序能不能活过落盘」只能这样验——拿
    一个内存 dict 自证，验的其实是 Python 的 dict 而不是缓存的行为。
    """

    TOPIC = "今天财运{}怎么样"
    KEY = "sf_daily_u1"
    # 20 这个数**写死在这儿是故意的**：它是产品口径（一天换 20 个话题仍留得住），
    # 不是实现细节。若哪天实现动了这个数，本文件当场红、逼一次显式确认。
    # 更实际的理由：下面几条「挤掉最早那桶」的用例若拿被测常量当尺子，把常量改成
    # 999 也照样全绿——那等于没验（实测过，所以才有这一行）。
    CAP = 20

    class _JsonKv:
        def __init__(self):
            self.raw = {}

        async def get_kv_data(self, key, default=None):
            text = self.raw.get(key)
            return default if text is None else json.loads(text)

        async def put_kv_data(self, key, value):
            self.raw[key] = json.dumps(value)

        def record(self, key="sf_daily_u1"):
            return json.loads(self.raw[key]) if key in self.raw else None

    @classmethod
    def _daily(cls, kv):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        calls = []

        async def fake_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        return DailyFortune(kv, types.SimpleNamespace(_ai_interpret=fake_ai)), calls

    def _ask(self, df, nums):
        """按顺序问这些编号的主题（同一事件循环内串行，与真实问答同形）。"""
        async def go():
            for n in nums:
                await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                       self.TOPIC.format(n))
        asyncio.run(go())

    def test_the_cap_is_the_documented_number(self):
        """口径对账：实现里的上限就是本文件写死的那个数。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DAILY_INTERP_MAX
        assert DAILY_INTERP_MAX == self.CAP

    def test_fills_up_to_the_cap_without_evicting_anything(self):
        """刚好装满：一桶不少、一桶不多，也没有白白多发一次请求。"""
        kv = self._JsonKv()
        df, calls = self._daily(kv)
        self._ask(df, range(1, self.CAP + 1))
        slots = kv.record()["interps"]
        assert list(slots) == [self.TOPIC.format(n) for n in range(1, self.CAP + 1)]
        assert len(calls) == self.CAP

    def test_the_earliest_inserted_bucket_goes_when_full(self):
        """第 21 个主题挤掉第 1 桶：留下的 20 桶仍是插入顺序（FIFO 的形状）。"""
        kv = self._JsonKv()
        df, _ = self._daily(kv)
        self._ask(df, range(1, self.CAP + 2))
        slots = kv.record()["interps"]
        assert list(slots) == [self.TOPIC.format(n) for n in range(2, self.CAP + 2)]
        assert self.TOPIC.format(1) not in slots

    def test_a_cache_hit_does_not_make_a_bucket_younger(self):
        """FIFO ≠ LRU：回头读过的那一桶不会因此排到后面去。

        问满 20 桶 → 回头读第 1 桶（命中、不写回）→ 问第 21 个主题。挤掉的必须仍是
        最早插入的第 1 桶；换成「按最近使用淘汰」，被挤掉的会是第 2 桶、而第 1 桶
        反而留下——这条就是两者的分界。
        """
        kv = self._JsonKv()
        df, calls = self._daily(kv)
        self._ask(df, range(1, self.CAP + 1))
        hit = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           self.TOPIC.format(1)))
        assert hit == f"解读:{self.TOPIC.format(1)}"      # 命中：复用旧桶
        assert len(calls) == self.CAP                     # 命中不花 AI 调用
        self._ask(df, [self.CAP + 1])
        slots = kv.record()["interps"]
        assert self.TOPIC.format(1) not in slots
        assert self.TOPIC.format(2) in slots

    def test_oversized_legacy_cache_is_trimmed_on_a_cache_hit(self):
        """升级用户的旧缓存超过上限：命中路径也要把它裁小并写回。

        上限是 v0.8.0 才引入的，升级前落盘的持久化分桶可能早就超过 20 条；只在
        「写入新解读之后」裁剪的话，只问老主题的人会一直留着这本超大字典。命中
        照旧返回原文（不为了缩结构把用户正在等的那段丢掉），但落盘那份必须收口。
        """
        kv = self._JsonKv()
        df, calls = self._daily(kv)
        extra = self.CAP + 5
        legacy = {self.TOPIC.format(n): f"旧解读{n}" for n in range(1, extra + 1)}
        kv.raw[self.KEY] = json.dumps({"date": df.clock.today(), "interps": legacy})

        hit = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           self.TOPIC.format(1)))
        assert hit == "旧解读1"                  # 命中的就是最早那桶：照旧返回
        assert calls == []                       # 命中不花 AI 调用
        record = kv.record()
        assert record["date"] == df.clock.today()
        assert len(record["interps"]) == self.CAP
        assert list(record["interps"]) == [
            self.TOPIC.format(n) for n in range(extra - self.CAP + 1, extra + 1)]

    def test_trim_touches_only_the_bucket_bag(self):
        """裁剪只动 interps：记录里的日期与当天抽好的牌一律不动。

        若实现是「顺手重造一条记录」，当天已经确定的牌会被抹掉——当场看不出来，
        要等下一次问运势时牌面换了张才发现。
        """
        kv = self._JsonKv()
        df, _ = self._daily(kv)
        today = df.clock.today()
        kv.raw[self.KEY] = json.dumps({"date": today, "card": PICK["card"],
                                       "upright": True, "interps": {}})
        self._ask(df, range(1, self.CAP + 2))
        rec = kv.record()
        assert rec["date"] == today
        assert rec["card"] == PICK["card"] and rec["upright"] is True

    def test_eviction_order_survives_a_json_round_trip(self):
        """落盘再读回后淘汰顺序与内存一致——选 FIFO 的前提就是这一条。

        宿主 KV 的 value 是 JSON 列：dict 的插入顺序必须活过 dumps/loads。本桩每次
        put 都序列化、每次 get 都重新解析，再换一个 DailyFortune 实例接着问（同进程
        换实例／重启后的形状），第 22 桶挤掉的仍该是第 2 桶。顺序若是靠内存 dict 才
        成立，这里会当场红。
        """
        kv = self._JsonKv()
        df, _ = self._daily(kv)
        self._ask(df, range(1, self.CAP + 2))             # 此时盘上是第 2..21 桶
        df2, _ = self._daily(kv)                          # 换一代实例，只认盘上那份
        self._ask(df2, [self.CAP + 2])
        slots = kv.record()["interps"]
        assert list(slots) == [self.TOPIC.format(n) for n in range(3, self.CAP + 3)]


class TestDailyKvFallback:
    """KV 接口不可用时的降级回归：真实 AstrBot Context 没有 get/put_kv_data
    （曾误传给 DailyFortune 导致缓存全程静默失效），缓存必须静默失败并
    回退确定性抽牌，绝不抛异常、绝不阻塞占卜主流程。"""

    @staticmethod
    def _daily(kv):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        return DailyFortune(kv, T)

    def test_context_like_no_kv_interface(self):
        """模拟真实 Context：对象上根本没有 get_kv_data/put_kv_data。"""
        df = self._daily(object())
        out = asyncio.run(df.pick_cached("u1"))
        assert out is not None
        assert out[2][0]["card"] in TAROT_CARDS

    def test_broken_kv_store_falls_back(self):
        """KV 方法存在但存储抛错：同样静默降级，返回固定牌。"""
        class _Broken:
            async def get_kv_data(self, key, default=None):
                raise RuntimeError("kv store down")

            async def put_kv_data(self, key, value):
                raise RuntimeError("kv store down")

        df = self._daily(_Broken())
        out = asyncio.run(df.pick_cached("u1"))
        assert out is not None
        assert out[2][0]["card"] in TAROT_CARDS


class TestDailyPoisonedCacheValue:
    """KV 里该 key 存了**非 dict**（外部工具、旧版本残留写坏）时，解读写回必须自愈。

    旧写法 `data = dict(fresh) if isinstance(fresh, dict) else dict(data or {})`
    里的 `data or {}` 只兜 falsy；**truthy 非 dict**（["a"] / "abc" / 123）会直接
    进 dict(...)，抛 ValueError（"dictionary update sequence element #0..."）或
    TypeError（"object is not iterable"）。异常上抛前那次写回**整段跳过**，
    于是 KV 里的坏值永远不会被好值覆盖 —— 该用户的每日牌运解读缓存**永久失效**：
    之后每次问都现场重生成，"当天固定同一段解读"的承诺彻底破掉。
    触发条件比"非 dict"窄一格：只有 truthy 非 dict 会炸，falsy 有 `or {}` 兜着。

    修复口径与同文件 pick_cached 对齐（它一直用 `isinstance(data, dict)`）。
    """

    @staticmethod
    def _daily(kv):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        return DailyFortune(_Ctx(), types.SimpleNamespace(_ai_interpret=fake_ai)), calls

    def test_truthy_non_dict_cache_value_self_heals(self):
        """truthy 非 dict：既不抛异常，也要把坏值覆盖成合法结构（真正自愈）。"""
        import time as _time

        from data.plugins.astrbot_plugin_star_feather.daily import _norm_topic
        today = _time.strftime("%Y%m%d")
        topic = "今天感情运势"
        for poison in (["a"], "abc", 123):
            kv = {"sf_daily_u1": poison}
            df, calls = self._daily(kv)
            out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], topic))
            assert out == f"解读:{topic}", f"毒值 {poison!r} 下解读应正常返回"
            cached = kv["sf_daily_u1"]
            assert isinstance(cached, dict), f"毒值 {poison!r} 未被覆盖成合法结构"
            assert cached.get("date") == today
            assert cached["interps"] == {_norm_topic(topic): out}
            assert len(calls) == 1

    def test_falsy_non_dict_cache_value_also_survives(self):
        """falsy 非 dict（None / 0 / "" / []）本就走 `or {}` 分支，一并锁住不回归。"""
        import time as _time
        today = _time.strftime("%Y%m%d")
        for poison in (None, 0, "", []):
            kv = {"sf_daily_u1": poison}
            df, calls = self._daily(kv)
            out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势"))
            assert out == "解读:今天感情运势"
            assert isinstance(kv["sf_daily_u1"], dict) and kv["sf_daily_u1"].get("date") == today



class TestDailyNullInterp:
    """AI 解读失败（返回 None / 空串）时不得把 falsy 值写进 interps 分桶。

    写入 null 的功能后果其实没有——命中判据 `slots.get(topic)` 对 None 与空串
    同样是 falsy，下次照样现场重生成。但「interps 里有这个 key」会因此失去含义：
    有 key 就一定是一段有效解读，这条不变量值得立住（也让 KV 不白占空间）。
    顺带收益：失败时少一次「拿旧 data 覆盖 KV」的机会（fresh 重读失败的回退路径）。
    """

    @staticmethod
    def _daily(kv, ret):
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return ret

        return DailyFortune(_Ctx(), types.SimpleNamespace(_ai_interpret=fake_ai)), calls

    def test_none_interp_not_written_into_slots(self):
        import time as _time

        from data.plugins.astrbot_plugin_star_feather.daily import _norm_topic
        today = _time.strftime("%Y%m%d")
        topic = _norm_topic("今天感情运势")
        for ret in (None, ""):
            kv = {}
            df, calls = self._daily(kv, ret)
            out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势"))
            assert out == ret and len(calls) == 1
            cached = kv.get("sf_daily_u1")
            if cached is None:
                continue                      # 整个写回被跳过也可以接受
            assert isinstance(cached, dict) and cached.get("date") == today
            slots = cached.get("interps") or {}
            assert topic not in slots, f"失败值 {ret!r} 被写进了分桶"
            assert all(v for v in slots.values()), "分桶里出现了 falsy 值"

    def test_failure_keeps_other_topics_intact(self):
        """AI 失败只影响本主题：别的主题已缓存内容不得被清掉或改写。"""
        import time as _time

        from data.plugins.astrbot_plugin_star_feather.daily import _norm_topic
        today = _time.strftime("%Y%m%d")
        other = _norm_topic("最近事业运")
        kv = {"sf_daily_u1": {"date": today, "interps": {other: "事业解读"}}}
        df, calls = self._daily(kv, None)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势"))
        assert out is None
        slots = kv["sf_daily_u1"]["interps"]
        assert slots.get(other) == "事业解读"
        assert _norm_topic("今天感情运势") not in slots
