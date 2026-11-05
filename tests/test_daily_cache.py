"""每日解读缓存单测：分桶合并写、跨天作废、主题桶上限、KV 降级、脏数据与空解读。

从原 `test_core` 拆出来的一支（2026-10-17）：这一份只管「记事本怎么存、坏了怎么办」，
不碰入口编排。
"""
import asyncio
import json
import types

import pytest
from data.plugins.astrbot_plugin_star_feather.core.tarot_data import (
    TAROT_CARDS,
)
from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
from data.plugins.astrbot_plugin_star_feather.services.daily import (
    _daily_pick,
)
from stubs import PICK


def _text(value):
    """缓存命中返回 Interpretation、生成路径的替身返回纯字符串：断言统一取文本。"""
    return value.text if hasattr(value, "text") else value


def _rec(**over):
    """schema 2 记录的最小夹具：字段名跟 services/daily_store.DailyRecord 一致。"""
    from data.plugins.astrbot_plugin_star_feather.services.daily import _PROMPT_V
    base = {"schema": 2, "date": "", "card_id": PICK["card"]["id"],
            "upright": True, "sign": f"{PICK['card']['id']}:1",
            "prompt_v": _PROMPT_V, "persona": "", "interps": {},
            "spirit_line": "", "spirit_sign": ""}
    base.update(over)
    return base


class _Interp:
    """把旧的 _ai_interpret 桩适配到解释器接口（detail 参数由服务层格式化）。"""

    def __init__(self, fn):
        self.fn = fn

    async def interpret(self, umo, formation, positions, picks, user_input, detail,
                        persona_eff=None, budget=None):
        return await self.fn(umo, formation, positions, picks, user_input,
                             persona_eff=persona_eff, budget=budget)


# ---------- 每日缓存合并写（解读分桶保留 / 跨天作废） ----------
class TestDailyCacheMerge:
    """模拟 KV：解读先被写入时，抽牌重生成不得覆盖丢掉解读分桶。

    字段名是 interps（主题指纹 → 解读文本的分桶 dict，见 daily.norm_topic /
    interp_cached），不是 interp。旧用例写的是 interp（实现里根本没有这个键），
    合并写原样保留未知键所以恒绿——既测不到真字段的保留语义，也测不到跨天清理。
    """

    @staticmethod
    def _plugin(kv_store):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv_store.get(key, default)

            async def put_kv_data(self, key, value):
                kv_store[key] = value

        p.daily = DailyFortune(_Ctx())
        return p

    def test_keeps_existing_interps_when_regenerating(self):
        import time as _time
        today = _time.strftime("%Y%m%d")
        slots = {"（今日牌运）": "旧解读文本"}
        card, upright = _daily_pick("u1", today)
        sig = f"{card['id']}:{1 if upright else 0}"
        kv = {"sf_daily_u1": _rec(date=today, card_id=card["id"], upright=upright,
                                  sign=sig, interps=dict(slots))}
        p = self._plugin(kv)
        result = asyncio.run(p.daily.pick_cached("u1"))
        assert result is not None
        cached = kv["sf_daily_u1"]
        # 牌被补上，且当天已写入的解读分桶原样保留（不被抽牌重生成覆盖）
        assert cached["card_id"] in {c["id"] for c in TAROT_CARDS} and isinstance(cached["upright"], bool)
        assert cached["interps"] == slots
        assert cached["date"] == today

    def test_fresh_cache_has_card_no_interps(self):
        kv = {}
        p = self._plugin(kv)
        asyncio.run(p.daily.pick_cached("u1"))
        cached = kv["sf_daily_u1"]
        assert cached["card_id"] in {c["id"] for c in TAROT_CARDS}
        assert cached["interps"] == {}  # 首次写入不虚构解读分桶

    def test_stale_day_interps_dropped_on_regenerate(self):
        """跨天：旧解读分桶随抽牌重生成一并作废，不盖章复活。"""
        import time as _time
        stale = {"（今日牌运）": "昨天的解读", "最近事业运": "前天的解读"}
        kv = {"sf_daily_u1": _rec(date="19700101", card_id=TAROT_CARDS[0]["id"],
                                  upright=True, interps=dict(stale))}
        p = self._plugin(kv)
        result = asyncio.run(p.daily.pick_cached("u1"))
        assert result is not None
        cached = kv["sf_daily_u1"]
        assert cached["date"] == _time.strftime("%Y%m%d")
        assert cached["interps"] == {}      # 跨天作废：旧分桶不得跟着新日期复活
        assert cached["card_id"] in {c["id"] for c in TAROT_CARDS}
class TestDailyCachedCardAnchor:
    """缓存里的牌按 id 锚点回查牌库，而不是整字典值相等。

    值相等的代价：把「存储层序列化/反序列化必须逐字段等值」写成隐式契约——
    字段类型只要漂移一次（如 num 从 str 变 int），命中就全部失配、当天缓存静默失效。
    id 是牌库明面声明的唯一锚点；命中后回查牌库对象，顺带避免旧副本的字面量
    被当成当前牌义。
    """

    @staticmethod
    def _fortune(kv_store):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv_store.get(key, default)

            async def put_kv_data(self, key, value):
                kv_store[key] = value

        return DailyFortune(_Ctx())

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
        kv = {"sf_daily_u1": _rec(date=today, card_id=drifted["id"], upright=True)}
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
        kv = {"sf_daily_u1": _rec(date=today, card_id="no_such_card", upright=True)}
        _formation, _positions, picks = asyncio.run(self._fortune(kv).pick_cached("u1"))
        assert picks[0]["card"]["id"] == det_card["id"]
        assert picks[0]["upright"] == det_upright

    def test_non_dict_card_value_degrades_quietly(self):
        """缓存里放 list / 字符串这类非字典时照常降级，不把占卜入口带崩。"""
        import time as _time
        today = _time.strftime("%Y%m%d")
        for bad in (["不是字典"], "不是字典", 12345):
            kv = {"sf_daily_u1": _rec(date=today, card_id=bad, upright=True)}
            out = asyncio.run(self._fortune(kv).pick_cached("u1"))
            assert out is not None and out[0] == "羽签"



class TestDailyInterpTopic:
    """解读缓存按主题指纹（norm_topic）分桶：同主题复用、换主题现场生成。

    旧版无主题区分：同一天问什么都返回当天第一段解读（「事业运」的解读
    被「感情运」「学业运」共用），这是「不管问什么答案都一样」的元凶之一。
    """

    @staticmethod
    def _daily(kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp(fake_ai)), calls

    def test_topic_table_decoupled_from_daily_judgement(self):
        """分桶与判定解耦：有具体主题仍走每日固定牌，但解读不与泛问共用。

        SPECIFIC_WORDS 一度一份词表两处用（既是 is_daily_request 的排除条件、
        又是 norm_topic 的分桶判据），于是「今天财运怎么样」只有两种命：与
        「今天运势」共用解读，或被踢出每日牌运、每问重抽。拆表后两者兼得——
        牌仍是当天同一张（is_daily_request 为真），解读各归各（topic 不同）。
        """
        from data.plugins.astrbot_plugin_star_feather.core.intent import (
            _GENERIC_TOPIC,
            is_daily_request,
            norm_topic,
        )

        assert is_daily_request("今天财运怎么样")
        assert norm_topic("今天财运怎么样") != _GENERIC_TOPIC
        # 无主题的泛问仍共享同一段（防反复问刷版本）
        for q in ("今天运势", "看看今天的运势", "每日一签"):
            assert norm_topic(q) == _GENERIC_TOPIC, q

    def test_mixed_generic_question_gets_own_bucket(self):
        """泛问混着具体诉求时不能进通用桶（2026-11-01 复核）。

        「今天运势如何，我该不该表白」整体含运势词，但它真正想问的是表白——
        进通用桶会拿到当天早上「今天运势」那段泛泛解读，答非所问；
        反向也一样：第一个提问会把这段定制解读存成通用解读给后面的人用。
        纯泛问之间仍要共享同一桶（防反复问刷版本）。
        """
        from data.plugins.astrbot_plugin_star_feather.core.intent import (
            _GENERIC_TOPIC,
            norm_topic,
        )

        mixed = "今天运势如何，我该不该表白"
        assert norm_topic(mixed) == mixed
        assert norm_topic(mixed) != _GENERIC_TOPIC
        # 纯泛问仍归通用桶，且不同措辞共享
        for q in ("今天运势", "看看今天的运势", "每日一签", "最近怎么样", "今天来一签"):
            assert norm_topic(q) == _GENERIC_TOPIC, q

    def test_writeback_skipped_when_new_day_already_cached(self):
        """跨午夜生成：KV 已是新一天记录时，前一晚的写回不许覆盖（review 复核）。"""
        kv = {"sf_daily_u1": _rec(date="20991231",
                                  interps={"（今日牌运）": "新一天的解读"})}
        df, calls = self._daily(kv)
        df.clock = types.SimpleNamespace(today=lambda: "20260101")
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天运势"))
        assert out == "解读:今天运势"
        assert kv["sf_daily_u1"]["date"] == "20991231", "昨天的写回盖掉了新一天的记录"
        assert kv["sf_daily_u1"]["interps"] == {"（今日牌运）": "新一天的解读"}

    def test_same_specific_topic_reuses(self):
        kv = {}
        df, calls = self._daily(kv)
        async def go():
            a = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            b = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            return a, b
        a, b = asyncio.run(go())
        assert _text(a) == _text(b) == "解读:今天感情运势"
        assert len(calls) == 1  # 同主题只生成一次

    def test_concurrent_same_topic_generates_once(self):
        """同一主题并发 miss：只调一次 AI，两个调用者拿到同一段。

        生成在锁外跑，没有 in-flight 登记时两条协程会各自问一次、返回两段不同的话。
        """
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        kv, calls = {}, []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def slow_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            calls.append(clean)
            await asyncio.sleep(0.02)
            return f"解读:{clean}"

        df = DailyFortune(_Ctx(), persona="", interpreter=_Interp(slow_ai))

        async def go():
            return await asyncio.gather(
                df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势"),
                df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势"))

        a, b = asyncio.run(go())
        assert a == b == "解读:今天感情运势"
        assert len(calls) == 1, f"同主题并发应只调用一次 AI，实际 {len(calls)}"

    def test_prompt_version_change_invalidates_same_day_interp(self):
        """提示词/人设换版：牌组没变，当天也要重新生成，不能继续吐旧口吻。"""
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic

        topic = norm_topic("今天感情运势")
        sig = "|".join(f"{p['card']['id']}:{1 if p['upright'] else 0}" for p in [PICK])
        kv = {"sf_daily_u1": _rec(date="20990101", sign=sig, prompt_v="oldver",
                                  interps={topic: "旧口吻"})}
        df, calls = self._daily(kv)
        # 用今天重写日期，只留版本号不匹配这一个差异
        kv["sf_daily_u1"]["date"] = df.clock.today()
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           "今天感情运势"))
        assert out == "解读:今天感情运势"
        assert calls == ["今天感情运势"]

    def test_persona_switch_invalidates_same_day_interp(self):
        """面板切人设：牌组与 prompt 版本都没变，当天也要重新生成。"""
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        topic = norm_topic("今天感情运势")
        sig = "|".join(f"{p['card']['id']}:{1 if p['upright'] else 0}" for p in [PICK])
        kv = {"sf_daily_u1": _rec(date="20990101", sign=sig, persona="gentle",
                                  interps={topic: "温柔口吻"})}
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        df = DailyFortune(_Ctx(), persona="tsundere", interpreter=_Interp(fake_ai))
        kv["sf_daily_u1"]["date"] = df.clock.today()
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           "今天感情运势"))
        assert out == "解读:今天感情运势"
        assert calls == ["今天感情运势"]

    def test_different_topic_regenerates(self):
        kv = {}
        df, calls = self._daily(kv)
        async def go():
            a = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "最近事业运")
            b = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势")
            c = await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "最近事业运")
            return a, b, c
        a, b, c = asyncio.run(go())
        assert _text(a) == "解读:最近事业运" and _text(b) == "解读:今天感情运势"
        assert _text(c) == "解读:最近事业运"  # 换主题后回来仍复用
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
        assert _text(a) == _text(b) == "解读:（今日牌运）"
        assert len(calls) == 1

    def test_concurrent_topics_both_persist(self):
        """并发问两个主题：两个分桶都要在。

        加锁 + 写回前重读之前，两条协程各自读旧值再写回，后写的会把先写的整个覆盖
        （实测 KV 里只剩一个分桶，另一个主题白花一次 AI 调用）。
        """
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
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

        df = DailyFortune(_SlowCtx(), persona="", interpreter=_Interp(fake_ai))
        t1, t2 = "今天感情运势", "最近事业运"

        async def go():
            return await asyncio.gather(
                df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], t1),
                df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], t2))

        a, b = asyncio.run(go())
        assert a == f"解读:{t1}" and b == f"解读:{t2}"
        assert set(kv["sf_daily_u1"]["interps"]) == {norm_topic(t1), norm_topic(t2)}

    def test_stale_day_slots_dropped_when_pick_read_recovers(self):
        """跨天 + pick_cached 读故障：写入端自己判跨天，昨天的分桶不得盖章复活。

        时序：pick_cached 读故障（if ok 门跳过写回、也跳过 interps 清理）→
        interp_cached 这次读成功 → 旧分桶若被原样沿用，就会跟着今天的新日期
        写回去，当天同主题直接复用昨天的解读（写入端只认 data["date"] == today）。
        """
        stale = {"最近事业运": "昨天的解读"}
        kv = {"sf_daily_u1": {"date": "19700101", "card": None, "upright": True,
                              "interps": dict(stale)}}
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
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

        df = DailyFortune(_FlakyCtx(), persona="", interpreter=_Interp(fake_ai))

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

    @staticmethod
    def _sign(picks):
        """与实现同口径的牌指纹（牌 id + 正逆）：命中要求 date + sign + topic 一致。"""
        return "|".join(f"{p['card']['id']}:{1 if p['upright'] else 0}" for p in picks)

    class _JsonKv:
        def __init__(self):
            self.raw = {}
            self.puts = 0

        async def get_kv_data(self, key, default=None):
            text = self.raw.get(key)
            return default if text is None else json.loads(text)

        async def put_kv_data(self, key, value):
            self.puts += 1
            self.raw[key] = json.dumps(value)

        def record(self, key="sf_daily_u1"):
            return json.loads(self.raw[key]) if key in self.raw else None

    @classmethod
    def _daily(cls, kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        calls = []

        async def fake_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        return DailyFortune(kv, persona="", interpreter=_Interp(fake_ai)), calls

    def _ask(self, df, nums):
        """按顺序问这些编号的主题（同一事件循环内串行，与真实问答同形）。"""
        async def go():
            for n in nums:
                await df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                       self.TOPIC.format(n))
        asyncio.run(go())

    def test_the_cap_is_the_documented_number(self):
        """口径对账：实现里的上限就是本文件写死的那个数。"""
        from data.plugins.astrbot_plugin_star_feather.services.daily import (
            DAILY_INTERP_MAX,
        )
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
        assert _text(hit) == f"解读:{self.TOPIC.format(1)}"      # 命中：复用旧桶
        assert len(calls) == self.CAP                     # 命中不花 AI 调用
        self._ask(df, [self.CAP + 1])
        slots = kv.record()["interps"]
        assert self.TOPIC.format(1) not in slots
        assert self.TOPIC.format(2) in slots

    def test_a_cache_hit_does_not_write_anything_back(self):
        """命中只读：超限旧缓存也不在命中路径做写回。

        无 CAS 的 KV 上，任何「读旧值→裁→写回」都可能盖掉并发生成的新主题；
        写回还会占锁或占用用户时间。命中只返回文本，收口交给下一次真正写入。
        """
        kv = self._JsonKv()
        df, calls = self._daily(kv)
        extra = self.CAP + 5
        legacy = {self.TOPIC.format(n): f"旧解读{n}" for n in range(1, extra + 1)}
        kv.raw[self.KEY] = json.dumps(_rec(date=df.clock.today(),
                                           sign=self._sign([PICK]),
                                           interps=legacy))

        hit = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           self.TOPIC.format(1)))
        assert _text(hit) == "旧解读1"
        assert calls == []                              # 命中不花 AI 调用
        assert kv.puts == 0                             # 命中不写 KV
        assert len(kv.record()["interps"]) == extra     # 旧字典原样留着

    def test_the_next_new_topic_trims_a_legacy_oversized_dict(self):
        """旧超限字典在下一次真正写入（问新主题）时收口到上限。"""
        kv = self._JsonKv()
        df, calls = self._daily(kv)
        extra = self.CAP + 5
        legacy = {self.TOPIC.format(n): f"旧解读{n}" for n in range(1, extra + 1)}
        kv.raw[self.KEY] = json.dumps(_rec(date=df.clock.today(),
                                           sign=self._sign([PICK]),
                                           interps=legacy))

        new_topic = self.TOPIC.format(extra + 1)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], new_topic))
        assert out == f"解读:{new_topic}"
        assert calls == [new_topic]
        slots = kv.record()["interps"]
        assert len(slots) == self.CAP
        assert new_topic in slots

    def test_a_card_change_invalidates_the_days_interpretations(self):
        """同一天换牌（升级 / 修 KV / 牌库变动）：旧解读不再命中，旧分桶整体作废。"""
        kv = self._JsonKv()
        df, calls = self._daily(kv)
        kv.raw[self.KEY] = json.dumps(_rec(
            date=df.clock.today(), sign=self._sign([PICK]),
            interps={self.TOPIC.format(1): "旧解读"}))

        other = {"card": {**PICK["card"], "id": "another_card"}, "upright": False}
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [other],
                                           self.TOPIC.format(2)))
        assert out == f"解读:{self.TOPIC.format(2)}"
        record = kv.record()
        assert record["sign"] == self._sign([other])
        assert self.TOPIC.format(1) not in record["interps"], "牌变了，旧解读不能留着"
        assert self.TOPIC.format(2) in record["interps"]

    def test_trim_touches_only_the_bucket_bag(self):
        """裁剪只动 interps：记录里的日期与当天抽好的牌一律不动。

        若实现是「顺手重造一条记录」，当天已经确定的牌会被抹掉——当场看不出来，
        要等下一次问运势时牌面换了张才发现。
        """
        kv = self._JsonKv()
        df, _ = self._daily(kv)
        today = df.clock.today()
        kv.raw[self.KEY] = json.dumps(_rec(date=today, interps={}))
        self._ask(df, range(1, self.CAP + 2))
        rec = kv.record()
        assert rec["date"] == today
        assert rec["card_id"] == PICK["card"]["id"] and rec["upright"] is True

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
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        return DailyFortune(kv)

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
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return f"解读:{clean}"

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp(fake_ai)), calls

    def test_truthy_non_dict_cache_value_self_heals(self):
        """truthy 非 dict：既不抛异常，也要把坏值覆盖成合法结构（真正自愈）。"""
        import time as _time

        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
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
            from data.plugins.astrbot_plugin_star_feather.ai.parse import (
                interpretation_from_record,
            )
            assert interpretation_from_record(cached["interps"][norm_topic(topic)]).text == out
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
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_ai(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            calls.append(clean)
            return ret

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp(fake_ai)), calls

    def test_none_interp_not_written_into_slots(self):
        import time as _time

        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        today = _time.strftime("%Y%m%d")
        topic = norm_topic("今天感情运势")
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

        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        today = _time.strftime("%Y%m%d")
        other = norm_topic("最近事业运")
        sig = "|".join(f"{p['card']['id']}:{1 if p['upright'] else 0}" for p in [PICK])
        kv = {"sf_daily_u1": _rec(date=today, sign=sig, interps={other: "事业解读"})}
        df, calls = self._daily(kv, None)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "今天感情运势"))
        assert out is None
        slots = kv["sf_daily_u1"]["interps"]
        assert slots.get(other) == "事业解读"
        assert norm_topic("今天感情运势") not in slots


class TestSingleFlight:
    """通用同键去重：owner 只跑一次，等待者拿同一结果，取消不连带。"""

    def test_waiters_share_one_result(self):
        from data.plugins.astrbot_plugin_star_feather.services.singleflight import (
            SingleFlight,
        )
        sf, calls = SingleFlight(), []

        async def factory():
            calls.append(1)
            await asyncio.sleep(0.01)
            return "x"

        async def go():
            return await asyncio.gather(sf.run("k", factory), sf.run("k", factory))

        assert asyncio.run(go()) == ["x", "x"]
        assert len(calls) == 1

    def test_owner_cancel_lets_waiter_retry(self):
        from data.plugins.astrbot_plugin_star_feather.services.singleflight import (
            SingleFlight,
        )
        sf = SingleFlight()
        started, calls = asyncio.Event(), []

        async def factory():
            calls.append(1)
            if len(calls) == 1:
                started.set()
                await asyncio.sleep(3600)
            return "retry-ok"

        async def go():
            owner = asyncio.create_task(sf.run("k", factory))
            await started.wait()
            waiter = asyncio.create_task(sf.run("k", factory))
            await asyncio.sleep(0)
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            return await waiter

        assert asyncio.run(go()) == "retry-ok"
        assert len(calls) == 2

    def test_waiter_cancel_does_not_cancel_owner(self):
        from data.plugins.astrbot_plugin_star_feather.services.singleflight import (
            SingleFlight,
        )
        sf = SingleFlight()
        started, release = asyncio.Event(), asyncio.Event()

        async def factory():
            started.set()
            await release.wait()
            return "owner-result"

        async def go():
            owner = asyncio.create_task(sf.run("k", factory))
            await started.wait()
            waiter = asyncio.create_task(sf.run("k", factory))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            return await owner

        assert asyncio.run(go()) == "owner-result"


class TestSchema2Reset:
    """旧缓存直接重置：两个旧键里的数据不再参与命中，下一次读写写回 schema 2。"""

    @staticmethod
    def _daily(kv, line="新牌灵句", interp="新解读"):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        class _Interp:
            async def spirit_line(self, *a, **kw):
                return line

            async def interpret(self, *a, **kw):
                return interp

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp())

    def test_old_spirit_key_is_ignored(self):
        import time
        today = time.strftime("%Y%m%d")
        kv = {"sf_spirit_u1": {"date": today, "sign": f"{PICK['card']['cn']}:1",
                               "line": "旧句", "v": "old"}}
        df = self._daily(kv)
        got = asyncio.run(df.spirit_cached(None, "u1", [PICK], "今日运势", None))
        assert got == "新牌灵句"
        assert kv["sf_daily_u1"]["schema"] == 2
        assert kv["sf_daily_u1"]["spirit_line"] == "新牌灵句"

    def test_schema_less_record_is_reset(self):
        import time
        today = time.strftime("%Y%m%d")
        kv = {"sf_daily_u1": {"date": today, "sign": f"{PICK['card']['id']}:1",
                              "v": "old", "persona": "",
                              "interps": {"（今日牌运）": "旧解读"}}}
        df = self._daily(kv)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"],
                                           [PICK], "今日运势"))
        assert out == "新解读"
        rec = kv["sf_daily_u1"]
        assert rec["schema"] == 2
        assert "（今日牌运）" in rec["interps"]
        assert "old" not in str(rec)


class TestSpiritDoesNotRewriteDailyCard:
    """P0 回归：普通占卜写牌灵的话，不许把今日固定牌的 card_id 覆盖掉。"""

    @staticmethod
    def _fortune(kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        class _Interp:
            async def spirit_line(self, *a, **kw):
                return "牌灵今天懒得开口"

            async def interpret(self, *a, **kw):
                return "解读"

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp())

    def test_daily_single_daily_keeps_the_same_card(self):
        kv = {}
        df = self._fortune(kv)
        first = asyncio.run(df.pick_cached("u1"))
        daily_card_id = first[2][0]["card"]["id"]

        other_card = next(c for c in TAROT_CARDS if c["id"] != daily_card_id)
        other = {"card": other_card, "upright": True}
        asyncio.run(df.spirit_cached(None, "u1", [other], "随便问问", None))

        second = asyncio.run(df.pick_cached("u1"))
        assert second[2][0]["card"]["id"] == daily_card_id
        assert kv["sf_daily_u1"]["card_id"] == daily_card_id


# ---------- 旧日期快照：不许把新一天的记录写回成旧日期 ----------
class TestOlderSnapshotDoesNotRollBack:
    """跨午夜回归：持前一天快照的请求晚到时，不得覆盖已经写入的新一天记录。"""

    @staticmethod
    def _fortune(kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        return DailyFortune(_Ctx())

    def test_older_snapshot_keeps_the_newer_record(self):
        newer_card = TAROT_CARDS[0]
        kv = {"sf_daily_u1": _rec(date="20260828", card_id=newer_card["id"],
                                  upright=True, interps={"主题": "新一天的解读"},
                                  spirit_line="新一天的牌灵", spirit_sign="x")}
        before = json.loads(json.dumps(kv, ensure_ascii=False))

        _f, _p, picks = asyncio.run(self._fortune(kv).pick_cached("u1", "20260827"))

        card, upright = _daily_pick("u1", "20260827")
        assert (picks[0]["card"]["id"], picks[0]["upright"]) == (card["id"], upright)
        assert kv == before, "旧日期快照不得把新一天的牌面、解读与牌灵写回成旧日期"


# ---------- 换人设重建解读上下文：牌字段与牌灵缓存不许跟着丢 ----------
class TestInterpRebuildKeepsPickAndSpirit:
    """same_context 失配只该作废解读分桶；牌字段与牌灵缓存不属于这个上下文。"""

    @staticmethod
    def _pair(kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        class _Interp:
            async def interpret(self, *a, **kw):
                return "【第1张·你的当下】解读。"

            async def spirit_line(self, *a, **kw):
                return "牌灵的一句话"

        return (DailyFortune(_Ctx(), persona="gentle", interpreter=_Interp()),
                DailyFortune(_Ctx(), persona="tsundere", interpreter=_Interp()))

    def test_persona_change_keeps_pick_and_spirit(self):
        kv = {}
        old, new = self._pair(kv)
        day = "20260827"
        picks = asyncio.run(old.pick_cached("u1", day))[2]
        asyncio.run(old.spirit_cached(None, "u1", picks, "今日运势", None, today=day))

        asyncio.run(new.pick_cached("u1", day))
        asyncio.run(new.interp_cached(None, "u1", "羽签", ["你的当下"], picks,
                                      "事业运势", today=day))

        record = kv["sf_daily_u1"]
        assert record["card_id"], "重建后牌字段丢失"
        assert record["spirit_line"], "重建后牌灵缓存被抹掉"
        assert record["interps"], "新主题的解读没有写入"

    def test_prompt_change_clears_spirit_line(self, monkeypatch):
        """提示词版本变了：牌字段保留，但旧提示词生成的牌灵话必须作废。"""
        from data.plugins.astrbot_plugin_star_feather.services import daily as daily_mod

        kv = {}
        old, _new = self._pair(kv)
        day = "20260827"
        picks = asyncio.run(old.pick_cached("u1", day))[2]
        asyncio.run(old.spirit_cached(None, "u1", picks, "今日运势", None, today=day))
        assert kv["sf_daily_u1"]["spirit_line"]

        monkeypatch.setattr(daily_mod, "_PROMPT_V", "v-next")
        asyncio.run(old.interp_cached(None, "u1", "羽签", ["你的当下"], picks,
                                      "事业运势", today=day))

        record = kv["sf_daily_u1"]
        assert record["card_id"], "牌字段不该跟着丢"
        assert record["spirit_line"] == "", "旧提示词生成的牌灵话必须作废"
        assert record["prompt_v"] == "v-next"


class TestPromptBumpViaSpiritWriteBack:
    """提示词换版后先走普通占卜：牌灵写回刷新 prompt_v 时，旧解读分桶必须一起作废。

    牌灵与每日解读共用一条记录与一个 prompt_v；只刷新版本号不清分桶，
    旧解读会顶着新版本号被当成有效缓存命中。
    """

    def test_spirit_writeback_clears_stale_interps(self, monkeypatch):
        from data.plugins.astrbot_plugin_star_feather.services import daily as daily_mod
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        kv = {}
        calls = []

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        class _Interp:
            async def interpret(self, *a, **kw):
                calls.append("interpret")
                return "新解读"

            async def spirit_line(self, *a, **kw):
                return "新牌灵"

        day = "20260827"
        df = DailyFortune(_Ctx(), persona="", interpreter=_Interp())
        asyncio.run(df.pick_cached("u1", day))
        asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                     "事业运势", today=day))
        assert kv["sf_daily_u1"]["interps"]

        monkeypatch.setattr(daily_mod, "_PROMPT_V", "v-next")
        asyncio.run(df.spirit_cached(None, "u1", [PICK], "事业运势", None, today=day))

        record = kv["sf_daily_u1"]
        assert record["prompt_v"] == "v-next"
        assert record["spirit_line"] == "新牌灵"
        assert record["interps"] == {}, "旧提示词生成的解读必须一起作废"

        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           "事业运势", today=day))
        assert _text(out) == "新解读"
        assert calls == ["interpret", "interpret"], "换版后必须重新生成，不能命中旧桶"

    @pytest.mark.parametrize("order", ["spirit_first", "interp_first"])
    def test_new_version_interp_survives_either_writeback_order(self, monkeypatch, order):
        """两种写回顺序都要满足：旧桶清掉，新版本生成的有效解读不被误删。"""
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        from data.plugins.astrbot_plugin_star_feather.services import daily as daily_mod
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        kv = {}
        seq = iter(["解读#1", "解读#2"])

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        class _Interp:
            async def interpret(self, *a, **kw):
                return next(seq)

            async def spirit_line(self, *a, **kw):
                return "新牌灵"

        day = "20260827"
        topic = norm_topic("事业运势")
        df = DailyFortune(_Ctx(), persona="", interpreter=_Interp())
        asyncio.run(df.pick_cached("u1", day))
        asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                     "事业运势", today=day))
        assert kv["sf_daily_u1"]["interps"][topic]["text"] == "解读#1"

        monkeypatch.setattr(daily_mod, "_PROMPT_V", "v-next")
        if order == "spirit_first":
            asyncio.run(df.spirit_cached(None, "u1", [PICK], "事业运势", None, today=day))
            assert kv["sf_daily_u1"]["interps"] == {}, "牌灵先写回：旧桶必须当场清掉"
            asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                         "事业运势", today=day))
        else:
            asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                         "事业运势", today=day))
            asyncio.run(df.spirit_cached(None, "u1", [PICK], "事业运势", None, today=day))

        record = kv["sf_daily_u1"]
        assert record["interps"][topic]["text"] == "解读#2", \
            "新版本生成的有效解读不能被误删"
        assert record["spirit_line"] == "新牌灵"
        assert record["prompt_v"] == "v-next"


class TestAiDisabledIgnoresCache:
    """ai.enable_ai=false：缓存里的历史 AI 产物不许继续返回。

    README 的承诺是「关闭则仅使用内置牌义，牌灵的话一并回退池内签文」；
    缓存命中若绕过这个开关，就等于配置没生效。两条入口分别验。
    """

    @staticmethod
    def _df(kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        calls = []

        class _Interp:
            async def interpret(self, *a, **kw):
                calls.append("interpret")
                return "不该被调用"

            async def spirit_line(self, *a, **kw):
                calls.append("spirit")
                return "不该被调用"

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp(),
                            ai_enabled=False), calls

    def test_cached_interpretation_is_not_served(self):
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic

        day = "20260827"
        kv = {"sf_daily_u1": _rec(date=day,
                                  interps={norm_topic("事业运势"): "旧 AI 解读"})}
        df, calls = self._df(kv)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           "事业运势", today=day))
        assert out is None, "关闭 AI 后应回退本地牌义，不返回历史缓存"
        assert calls == []
        assert kv["sf_daily_u1"]["interps"][norm_topic("事业运势")] == "旧 AI 解读"

    def test_cached_spirit_falls_back_to_pool(self):
        from data.plugins.astrbot_plugin_star_feather.core.dailylines import (
            pick_signature,
        )

        day = "20260827"
        kv = {"sf_daily_u1": _rec(
            date=day, spirit_line="旧 AI 牌灵",
            spirit_sign=f"{PICK['card']['cn']}:1", spirit_persona="")}
        df, calls = self._df(kv)
        line = asyncio.run(df.spirit_cached(None, "u1", [PICK], "事业运势", None,
                                            today=day))
        assert line == pick_signature(PICK["card"], True, "u1", day)
        assert line != "旧 AI 牌灵"
        assert calls == []


# ---------- 主题桶内容损坏：当未命中，不静默丢解读 ----------
_REGENERATED_TEXT = "【第1张·你的当下】新解读。"


class TestCachedInterpHonorsStructureCheck:
    """strict 下缓存命中也要复检：loose 遗留的不合格缓存视为未命中。"""

    @staticmethod
    def _fortune(kv, structure_check):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        calls = []

        class _Interp:
            async def interpret(self, *a, **kw):
                calls.append("interpret")
                return _REGENERATED_TEXT

            async def spirit_line(self, *a, **kw):
                return "牌灵"

        return DailyFortune(_Ctx(), persona="", interpreter=_Interp(),
                            structure_check=structure_check), calls

    def test_strict_rejects_loose_cached_text(self):
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            STRUCTURE_CHECK_STRICT,
        )

        day = "20260827"
        bad = "【第1张·】没有位置名的旧解读"
        kv = {"sf_daily_u1": _rec(date=day,
                                  interps={norm_topic("事业运势"): bad})}
        df, calls = self._fortune(kv, STRUCTURE_CHECK_STRICT)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           "事业运势", today=day))
        assert _text(out) == _REGENERATED_TEXT, "strict 下不合格缓存必须重新生成"
        assert calls == ["interpret"]

    def test_loose_still_serves_cached_text(self):
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            STRUCTURE_CHECK_LOOSE,
        )

        day = "20260827"
        text = "【第1张·】没有位置名的旧解读"
        kv = {"sf_daily_u1": _rec(date=day,
                                  interps={norm_topic("事业运势"): text})}
        df, calls = self._fortune(kv, STRUCTURE_CHECK_LOOSE)
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                           "事业运势", today=day))
        assert _text(out) == text
        assert calls == []


class TestCorruptInterpBucket:
    """KV 是持久化边界：外层上下文合法、桶内容无效时不能当成命中。"""

    @staticmethod
    def _fortune(kv, spies):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        class _Interp:
            def __init__(self):
                self.calls = 0

            async def interpret(self, *a, **kw):
                self.calls += 1
                return _REGENERATED_TEXT

            async def spirit_line(self, *a, **kw):
                return "牌灵的一句话"

        it = _Interp()
        spies.append(it)
        return DailyFortune(_Ctx(), persona="gentle", interpreter=it)

    def test_corrupt_bucket_regenerates(self):
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic

        kv = {}
        spies = []
        df = self._fortune(kv, spies)
        day = "20260827"
        picks = asyncio.run(df.pick_cached("u1", day))[2]
        topic = norm_topic("今天运势")
        record = kv["sf_daily_u1"]
        record["interps"] = {topic: {"broken": True}}
        kv["sf_daily_u1"] = record

        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], picks,
                                           "今天运势", today=day))

        assert spies[0].calls == 1, "损坏的桶必须当未命中，重新生成"
        text = out.text if hasattr(out, "text") else str(out)
        assert text == _REGENERATED_TEXT, "返回的应是生成器的新解读"
        assert kv["sf_daily_u1"]["interps"][topic]["text"] == _REGENERATED_TEXT, \
            "写回应覆盖损坏的桶"

    def test_structured_without_sections_regenerates(self):
        """text 非空、但结构化记录没有任何可发送段：同样按未命中处理。"""
        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic

        kv = {}
        spies = []
        df = self._fortune(kv, spies)
        day = "20260827"
        picks = asyncio.run(df.pick_cached("u1", day))[2]
        topic = norm_topic("今天运势")
        record = kv["sf_daily_u1"]
        record["interps"] = {topic: {"text": "只剩文本", "sections": [],
                                     "summary": "", "structured": True}}
        kv["sf_daily_u1"] = record

        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"], picks,
                                           "今天运势", today=day))

        assert spies[0].calls == 1, "没有可发送段的结构化记录必须当未命中"
        text = out.text if hasattr(out, "text") else str(out)
        assert text == _REGENERATED_TEXT
        assert kv["sf_daily_u1"]["interps"][topic]["text"] == _REGENERATED_TEXT


# ---------- 二次读取失败：不许用空壳覆盖已有记录 ----------
class TestSecondReadFailureKeepsRecord:
    """初次读到旧记录、生成成功、写回前二次读失败：本次照常返回，但不写回。

    单键记录里牌面、解读分桶与牌灵的话共用一个 raw；二次读故障时 from_raw(None)
    会得到空记录，写回去就是把整条记录清空。
    """

    @staticmethod
    def _flaky(record, fail_on=2):
        class _Ctx:
            def __init__(self):
                self.reads = 0
                self.puts = 0
                self.data = record

            async def get_kv_data(self, key, default=None):
                self.reads += 1
                if self.reads == fail_on:
                    raise RuntimeError("kv 二次读故障")
                return self.data

            async def put_kv_data(self, key, value):
                self.puts += 1
                self.data = value

        return _Ctx()

    @staticmethod
    def _today_rec(**over):
        import time as _time
        today = _time.strftime("%Y%m%d")
        return today, _rec(date=today, **over)

    def test_interp_write_back_is_skipped(self):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        _today, old = self._today_rec(
            interps={"旧主题": {"text": "旧解读", "sections": [],
                                "summary": "", "structured": False}})
        kv = self._flaky(old)

        async def fake_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            return "新解读"

        df = DailyFortune(kv, persona="", interpreter=_Interp(fake_ai))
        out = asyncio.run(df.interp_cached(None, "u1", "羽签", ["你的当下"],
                                           [PICK], "新主题"))
        assert _text(out) == "新解读"   # 本次照常交结果
        assert kv.puts == 0             # 但没有写回
        assert kv.data is old           # 原记录（牌 + 旧分桶）原样保留


# ---------- 跨实例并发：不同主题的合并写不能互相覆盖 ----------
class TestCrossInstanceTopicMerge:
    """两个 DailyFortune 实例共享同一 KV、同时生成不同主题：两个分桶都要在。

    热重载期间新旧实例并存；如果写锁只是实例属性，两边各自读旧记录再写回，
    后写的那一份会把先写的主题桶整个覆盖掉。
    """

    def test_both_topics_survive(self):
        import time as _time

        from data.plugins.astrbot_plugin_star_feather.core.intent import norm_topic
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        today = _time.strftime("%Y%m%d")
        kv = {"sf_daily_u1": _rec(date=today)}

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def slow_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            await asyncio.sleep(0.02)      # 让两个实例都先读到旧记录
            return f"解读:{clean}"

        a = DailyFortune(_Ctx(), persona="", interpreter=_Interp(slow_ai))
        b = DailyFortune(_Ctx(), persona="", interpreter=_Interp(slow_ai))

        async def go():
            return await asyncio.gather(
                a.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "事业运"),
                b.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK], "感情运"))

        asyncio.run(go())
        rec = kv["sf_daily_u1"]
        assert set(rec["interps"]) >= {norm_topic("事业运"), norm_topic("感情运")}
        assert rec["card_id"] == PICK["card"]["id"]


# ---------- 记录解析：布尔字段不做静默转换 ----------
class TestDailyRecordParsing:
    def test_non_bool_upright_is_an_invalid_record(self):
        from data.plugins.astrbot_plugin_star_feather.services.daily_store import (
            DailyRecord,
        )
        for bad in ("false", "true", 0, 1, None):
            rec = DailyRecord.from_raw(_rec(upright=bad))
            assert rec.card_id == "" and rec.interps == {}, repr(bad)
        ok = DailyRecord.from_raw(_rec(upright=False))
        assert ok.upright is False and ok.card_id == PICK["card"]["id"]


# ---------- 普通占卜的牌灵与今日牌运交替调用 ----------
class TestNormalSpiritThenDailyPick:
    """普通占卜先写牌灵、再访问今日牌运：当前设计会清掉普通占卜的牌灵缓存。

    原因是普通占卜不写每日固定牌的 sign，pick_cached 判定上下文不一致后重建记录。
    这不是抽牌正确性问题，只让普通占卜的牌灵多生成一次；锁住现状，
    避免以后被误当成回归，也避免有人无意中把每日牌运的上下文改坏。
    """

    def test_daily_pick_clears_normal_reading_spirit(self):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        kv = {}

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        async def fake_spirit(event, cards, topic, persona_eff, budget=None):
            return "普通占卜的牌灵"

        df = DailyFortune(_Ctx(), persona="",
                          interpreter=types.SimpleNamespace(spirit_line=fake_spirit))
        other = {"card": TAROT_CARDS[1], "upright": True}
        asyncio.run(df.spirit_cached(None, "u1", [other], "随便问问", None))
        assert kv["sf_daily_u1"]["spirit_line"] == "普通占卜的牌灵"

        asyncio.run(df.pick_cached("u1"))
        rec = kv["sf_daily_u1"]
        assert rec["card_id"] and rec["spirit_line"] == ""


# ---------- 旧牌灵键清理：失败要能重试 ----------
class TestLegacySpiritCleanupRetry:
    def test_failed_delete_is_retried_on_the_next_call(self):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        kv = {"sf_spirit_u1": {"old": True}}

        class _FlakyDelete:
            def __init__(self):
                self.deletes = 0

            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

            async def delete_kv_data(self, key):
                self.deletes += 1
                if self.deletes == 1:
                    raise RuntimeError("kv 临时故障")
                kv.pop(key, None)

        store = _FlakyDelete()
        df = DailyFortune(store)
        asyncio.run(df.pick_cached("u1"))
        assert store.deletes == 1 and "sf_spirit_u1" in kv

        asyncio.run(df.pick_cached("u1"))
        assert store.deletes == 2 and "sf_spirit_u1" not in kv


# ---------- 共享写锁的注册表：关闭的循环要回收 ----------
class TestSharedKvLockRegistry:
    def test_closed_loop_entries_are_reaped_without_another_call(self):
        """弱引用键：循环对象被回收后条目自动消失，不依赖下一次取锁来清。"""
        import gc

        from data.plugins.astrbot_plugin_star_feather.services import daily
        daily._KV_LOCKS.clear()

        async def go():
            return daily._shared_kv_lock()

        asyncio.run(go())
        gc.collect()
        assert len(daily._KV_LOCKS) == 0

    def test_contended_closed_loop_is_reaped_on_next_call(self):
        """争用会让锁绑定到循环（lock → loop），弱键回收不掉；下一次取锁清掉它。"""
        from data.plugins.astrbot_plugin_star_feather.services import daily
        daily._KV_LOCKS.clear()

        async def contend():
            lock = daily._shared_kv_lock()

            async def hold():
                async with lock:
                    await asyncio.sleep(0.01)

            await asyncio.gather(hold(), hold())

        asyncio.run(contend())
        assert len(daily._KV_LOCKS) == 1   # 锁仍持有已关闭的循环

        async def go():
            daily._shared_kv_lock()
            return len(daily._KV_LOCKS)

        assert asyncio.run(go()) == 1

    def test_same_loop_reuses_the_lock(self):
        from data.plugins.astrbot_plugin_star_feather.services import daily
        daily._KV_LOCKS.clear()

        async def go():
            same = daily._shared_kv_lock() is daily._shared_kv_lock()
            return same, len(daily._KV_LOCKS)

        same, count = asyncio.run(go())
        assert same is True and count == 1


class TestDailyUserIndex:
    """可视化视图要列「今天有谁抽过」：宿主 KV 不能枚举，只能维护日索引。"""

    @staticmethod
    def _df(kv):
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Ctx:
            async def get_kv_data(self, key, default=None):
                return kv.get(key, default)

            async def put_kv_data(self, key, value):
                kv[key] = value

        return DailyFortune(_Ctx(), persona="")

    def test_pick_cached_indexes_the_user(self):
        kv = {}
        df = self._df(kv)
        asyncio.run(df.pick_cached("u1", "20260827"))
        assert kv["sf_daily_index"] == {"date": "20260827", "uids": ["u1"]}
        assert asyncio.run(df.users_for("20260827")) == ["u1"]

    def test_cache_hit_also_indexes_an_existing_record(self):
        kv = {"sf_daily_u1": _rec(date="20260827")}
        df = self._df(kv)
        asyncio.run(df.pick_cached("u1", "20260827"))
        assert kv["sf_daily_index"]["uids"] == ["u1"]

    def test_index_does_not_leak_across_days(self):
        kv = {"sf_daily_index": {"date": "20260827", "uids": ["u1"]}}
        df = self._df(kv)
        assert asyncio.run(df.users_for("20260828")) == []

    def test_index_is_keyed_by_date(self):
        """跨天重新登记：同一实例跨天继续用，不能在旧日期上停写。"""
        kv = {}
        df = self._df(kv)
        asyncio.run(df.pick_cached("u1", "20260827"))
        asyncio.run(df.pick_cached("u1", "20260828"))
        assert kv["sf_daily_index"]["date"] == "20260828"
        assert asyncio.run(df.users_for("20260828")) == ["u1"]

    def test_index_read_failure_does_not_overwrite(self):
        """索引读故障时不知道旧名单，写回会把当天名单缩成一个人。"""
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Flaky:
            def __init__(self):
                self.store = {"sf_daily_index": {"date": "20260828", "uids": ["u0"]}}
                self.puts = []

            async def get_kv_data(self, key, default=None):
                if key == "sf_daily_index":
                    raise RuntimeError("boom")
                return self.store.get(key, default)

            async def put_kv_data(self, key, value):
                self.puts.append(key)
                self.store[key] = value

        ctx = _Flaky()
        df = DailyFortune(ctx, persona="")
        asyncio.run(df.pick_cached("u1", "20260828"))
        assert ctx.store["sf_daily_index"]["uids"] == ["u0"]
        assert "sf_daily_index" not in ctx.puts

    def test_index_write_failure_is_retried(self):
        """索引写失败不能记账：下一次命中路径还要再试。"""
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune

        class _Flaky:
            def __init__(self):
                self.store = {}
                self.index_puts = 0
                self.fail = True

            async def get_kv_data(self, key, default=None):
                return self.store.get(key, default)

            async def put_kv_data(self, key, value):
                if key == "sf_daily_index":
                    self.index_puts += 1
                    if self.fail:
                        raise RuntimeError("boom")
                self.store[key] = value

        ctx = _Flaky()
        df = DailyFortune(ctx, persona="")
        asyncio.run(df.pick_cached("u1", "20260828"))
        assert asyncio.run(df.users_for("20260828")) == []
        ctx.fail = False
        asyncio.run(df.pick_cached("u1", "20260828"))
        assert asyncio.run(df.users_for("20260828")) == ["u1"]
        assert ctx.index_puts == 2
