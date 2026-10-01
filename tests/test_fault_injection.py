"""故障注入矩阵：存储 / 上游故障 × 各消费者，逐格钉死降级契约。

为什么单独开一个文件：这类断言原先散在各处——test_core 管读故障与毒值、
test_kv_utils 管「无记录 vs 存储故障」、test_gating 管 KV 挂掉放行。抬眼看，
没人回答过「故障模式 × 消费者」这张网格上还有哪些格子是空的，于是散落的红线
各自绿着、契约整体可以偷偷歪。本文件把网格铺开：每种故障模式对每个消费者
（daily.pick_cached / daily.interp_cached / daily.spirit_cached / LimitGate.check /
海报渲染）只断言对外可观测的行为——返回什么、写不写回、放不放行，一律不碰内部实现。

三条铁律（跨格不变，改代码时先看它们）：
1. 故障不夺（性能上的）确定性：读坏了，`_daily_pick` 这类纯函数照样出同一张牌；
2. 降级不沉默：必须走「有话说」的兜底（池内当日句、AI 现场解读），不能返回空；
3. 写回判据＝「写回是否依赖旧数据」：依赖旧 dict 的（pick/interp）读故障不写；
   纯覆盖写的（spirit）读故障照写——门住会破坏「当日同牌组同一句」。

零随机：期望值全部由 daily._daily_pick（md5 种子纯函数）现算，不写字面量。
"""
import asyncio
import time
import types

import pytest
from stubs import PICK

import daily as daily_mod
from dailylines import pick_signature

TODAY = time.strftime("%Y%m%d")
UID = "u_fault"
DAILY_KEY = f"sf_daily_{UID}"


def _run(coro):
    return asyncio.run(coro)


# ---------------- 故障 KV 实现（内存桩 + 定向故障） ----------------
class KvSpy:
    """内存 KV 桩：记录每次写回，供断言「写/不写」「写成什么」。"""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.puts = []

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        self.puts.append((key, value))
        self.store[key] = value


class ReadDown(KvSpy):
    async def get_kv_data(self, key, default=None):
        raise RuntimeError("get down")


class WriteDown(KvSpy):
    async def put_kv_data(self, key, value):
        self.puts.append((key, value))  # 先记录再炸：断言「确实尝试过写」
        raise RuntimeError("put down")


class NoInterface:
    """既没有 get_kv_data 也没有 put_kv_data —— 传错对象（如裸 Context）的真实形状。"""


class Poisoned(KvSpy):
    """truthy 非 dict 的坏值：外部工具或旧版残留写坏（["a"] / "abc" / 123）。"""

    def __init__(self, value):
        super().__init__({DAILY_KEY: value})


class StaleDay(KvSpy):
    """昨天的完整条目（带 interps）：跨天必须重抽且旧解读桶作废。"""

    def __init__(self):
        card = PICK["card"]
        super().__init__({DAILY_KEY: {"date": "19700101", "card": card, "upright": True,
                                      "interps": {"（今日牌运）": "昨天的解读"}}})


class BadCardId(KvSpy):
    """date 是今天、但牌 id 查不到牌库（旧版本字面量 / 数据漂移）。"""

    def __init__(self):
        super().__init__({DAILY_KEY: {"date": TODAY, "upright": True,
                                      "card": {"id": "no_such_card", "cn": "旧版残留"}}})


MODES = {
    "read_down": ReadDown,
    "write_down": WriteDown,
    "no_interface": NoInterface,
    "poison_list": lambda: Poisoned(["a"]),
    "poison_str": lambda: Poisoned("abc"),
    "poison_int": lambda: Poisoned(123),
    "empty_record": KvSpy,
    "stale_day": StaleDay,
    "bad_card_id": BadCardId,
}
# 读得通道（ok=True）的模式：这些才谈得上「写回」，读故障的模式一个字节都不许写
READ_OK_MODES = ["write_down", "poison_list", "poison_str", "poison_int",
                 "empty_record", "stale_day", "bad_card_id"]
READ_FAIL_MODES = ["read_down", "no_interface"]


# ---------------- 上游桩（tarot / 渲染） ----------------
def _fake_tarot(interp="AI解读文本", spirit="AI牌灵的话", exc=None, render_lock=None):
    t = types.SimpleNamespace(_render_lock=render_lock)

    async def _ai_interpret(event, formation, positions, picks, clean, persona_eff=None):
        if exc:
            raise exc
        return interp

    async def spirit_line(event, pairs, topic, persona_eff):
        if exc:
            raise exc
        return spirit

    t._ai_interpret = _ai_interpret
    t.interpreter = types.SimpleNamespace(spirit_line=spirit_line)
    return t


def _fortune(kv, **kw):
    return daily_mod.DailyFortune(kv, _fake_tarot(**kw))


def _expected_pick():
    return daily_mod._daily_result(*daily_mod._daily_pick(UID, TODAY))


# ================= ① daily.pick_cached：故障不夺固定 =================
@pytest.mark.parametrize("mode", list(MODES))
def test_pick_returns_deterministic_card_under_any_fault(mode):
    """任何存储故障下，今日固定牌都必须等于确定性函数的输出（不空、不乱）。"""
    kv = MODES[mode]()
    got = _run(_fortune(kv).pick_cached(UID))
    assert got == _expected_pick()


@pytest.mark.parametrize("mode", READ_FAIL_MODES)
def test_pick_does_not_write_back_when_read_failed(mode):
    """读故障不写回：pick_cached 是「读旧 dict → 合并 → 写回」，
    旧值缺失时写回会把有效内容覆盖成只含本条的壳。"""
    kv = MODES[mode]()
    _run(_fortune(kv).pick_cached(UID))
    assert getattr(kv, "puts", []) == []


@pytest.mark.parametrize("mode", READ_OK_MODES)
def test_pick_write_back_is_always_a_healthy_record(mode):
    """读得通就必须把存储修好：写回一条 date=今天、牌取自牌库的规范记录。

    毒值（truthy 非 dict）不许原样留在存储里——它会让该用户缓存永久失效
    （每次问都重新抽），这里断言被自愈覆盖。
    """
    kv = MODES[mode]()
    _run(_fortune(kv).pick_cached(UID))
    assert len(kv.puts) == 1
    # 写故障时存储里读不到，就断言「尝试写入的那份」——两者必须是同一份健康记录
    rec = kv.store.get(DAILY_KEY) or kv.puts[-1][1]
    assert isinstance(rec, dict)
    assert rec["date"] == TODAY
    assert rec["card"]["id"] == _expected_pick()[2][0]["card"]["id"]


@pytest.mark.parametrize("mode", ["poison_list", "poison_str", "poison_int",
                                  "stale_day", "bad_card_id"])
def test_pick_write_back_drops_stale_interp_buckets(mode):
    """跨天 / 坏牌 id：旧解读分桶一律作废（否则同主题当天复用昨天的解读）。"""
    kv = MODES[mode]()
    _run(_fortune(kv).pick_cached(UID))
    assert "interps" not in kv.store[DAILY_KEY]


def test_pick_keeps_today_interp_buckets_when_record_is_fine():
    """同日正常记录：抽牌重生成不许丢弃已写好的解读分桶（合并写，不是覆盖写）。"""
    kv = KvSpy({DAILY_KEY: {"date": TODAY, "card": PICK["card"], "upright": True,
                            "interps": {"（今日牌运）": "今天的解读"}}})
    _run(_fortune(kv).pick_cached(UID))
    assert kv.store[DAILY_KEY]["interps"] == {"（今日牌运）": "今天的解读"}


# ================= ② daily.interp_cached：降级不沉默 =================
@pytest.mark.parametrize("mode", list(MODES))
def test_interp_returns_text_under_any_fault(mode):
    """存储怎么坏，解读都得给出来（现场生成），不许返回空。"""
    kv = MODES[mode]()
    got = _run(_fortune(kv).interp_cached(None, UID, "羽签", ["你的当下"], [PICK], "今日运势"))
    assert got == "AI解读文本"


@pytest.mark.parametrize("mode", READ_FAIL_MODES)
def test_interp_does_not_write_back_when_read_failed(mode):
    kv = MODES[mode]()
    _run(_fortune(kv).interp_cached(None, UID, "羽签", ["你的当下"], [PICK], "今日运势"))
    assert getattr(kv, "puts", []) == []


@pytest.mark.parametrize("mode", ["poison_list", "poison_str", "poison_int", "bad_card_id"])
def test_interp_heals_poisoned_record(mode):
    """毒值自愈：写回后存储里必须是可解析的 dict（否则该用户解读缓存永久失效）。"""
    kv = MODES[mode]()
    _run(_fortune(kv).interp_cached(None, UID, "羽签", ["你的当下"], [PICK], "今日运势"))
    rec = kv.store[DAILY_KEY]
    assert isinstance(rec, dict) and rec["date"] == TODAY
    assert isinstance(rec.get("interps"), dict) and rec["interps"]


@pytest.mark.parametrize("interp,exc", [
    (None, None), ("", None), (None, RuntimeError("AI down")),
])
def test_interp_failure_writes_no_falsy_bucket(interp, exc):
    """AI 全失败（异常 / None / 空串）走同一条降级：不落 falsy 分桶——写 null 下次
    还得重问，不如留空，让下一次请求重新生成。异常与 None 必须同形（见 _ai_output）。"""
    kv = KvSpy()
    f = _fortune(kv, interp=interp, exc=exc)
    got = _run(f.interp_cached(None, UID, "羽签", ["你的当下"], [PICK], "今日运势"))
    # 空串是桩专有的形态（真实 interpret 剥洗后只会给 None 或非空文本），
    # 调用点统一按 falsy 判「本轮没有 AI 解读」→ 两种形态在此等价，都算降级成功
    assert not got
    buckets = (kv.store.get(DAILY_KEY) or {}).get("interps") or {}
    assert "（今日牌运）" not in buckets


# ================= ③ daily.spirit_cached：读故障仍写回 =================
def test_spirit_writes_back_even_on_read_failure():
    """红线：牌灵的话是纯覆盖写，读故障必须照写。

    门住的话，故障恢复前的重复查询会重新生成，AI 每次措辞不同——
    「当日同牌组同一句」这条契约当场碎掉。
    """
    kv = ReadDown()
    got = _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    assert got == "AI牌灵的话"
    assert len(kv.puts) == 1
    assert kv.puts[0][1]["line"] == "AI牌灵的话"


def test_spirit_write_failure_does_not_break_reading():
    kv = WriteDown()
    got = _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    assert got == "AI牌灵的话"


def test_spirit_read_down_but_it_would_still_hit():
    """读故障恢复后，先前那次写回要能命中（否则「同牌组同一句」还是白写）。"""
    kv = ReadDown()
    _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    kv2 = KvSpy(kv.store)
    got = _run(_fortune(kv2, spirit="AI牌灵的另一句").spirit_cached(None, UID, [PICK],
                                                                    "今日运势", None))
    assert got == "AI牌灵的话"  # 命中旧句，不再问 AI


@pytest.mark.parametrize("spirit,exc", [(None, None), ("", None),
                                        (None, RuntimeError("AI down"))])
def test_spirit_falls_back_to_pool_line(spirit, exc):
    """AI 失败（异常 / None / 空串）：回退池内当日句（确定性），且不写缓存，
    下次故障恢复后重新生成——异常必须与 None 同形（见 daily._ai_output）。"""
    kv = KvSpy()
    got = _run(_fortune(kv, spirit=spirit, exc=exc).spirit_cached(None, UID, [PICK],
                                                                 "今日运势", None))
    assert got == pick_signature(PICK["card"], PICK["upright"], UID, TODAY)
    assert getattr(kv, "puts", []) == []


def test_ai_exception_does_not_kill_the_reading():
    """红线：解释器自身的 bug（异常）不许把整卦拆掉。

    解释器层只吞 provider 级失败并返回 None；它自己的异常（prompt 拼接、人设模板、
    数据形态漂移）会漏到 daily。不拦的话异常穿到 main 的兜底分支，用户拿到的不是
    「池内牌灵句 + 本地牌义」，而是「这卦起得有点乱……换个时候再来问」——
    一处装饰性产出的 bug 吞掉一整次占卜。
    """
    kv = KvSpy()
    boom = RuntimeError("prompt 拼接炸了")
    f = _fortune(kv, exc=boom)
    # 解读侧：异常 → None（降级走本地牌义），不抛
    assert _run(f.interp_cached(None, UID, "羽签", ["你的当下"], [PICK], "今日运势")) is None
    # 牌灵侧：异常 → 池内当日句，不抛、不写缓存
    assert _run(f.spirit_cached(None, UID, [PICK], "今日运势", None)) == \
        pick_signature(PICK["card"], PICK["upright"], UID, TODAY)


@pytest.mark.parametrize("mode", list(MODES))
def test_spirit_never_returns_empty(mode):
    """任何存储故障下牌灵都得开口（空串会让主链路的前言整段消失）。"""
    kv = MODES[mode]()
    got = _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    assert got


# ================= ④ LimitGate：存储挂了宁可不限流 =================
def _gate(kv, cmd_rate_limit=0, daily_count_limit=5):
    from gating import LimitGate
    return LimitGate(kv, cmd_rate_limit, daily_count_limit)


def _evt(uid=UID):
    return types.SimpleNamespace(get_sender_id=lambda: uid, unified_msg_origin="test_umo")


@pytest.mark.parametrize("mode", list(MODES))
def test_gate_never_blocks_on_storage_trouble(mode):
    """存储故障不得把占卜入口拦坏：check 只允许返回 None（放行）或拦截文案，
    绝不抛异常、绝不因为写不进去就拒客。"""
    kv = MODES[mode]()
    out = _run(_gate(kv).check(_evt(), for_command=True))
    assert out is None or isinstance(out, str)


def test_gate_storage_failure_passes_without_counting():
    """读故障：放行且不计数（读不到就不敢扣配额——宁可超发，不可误伤）。"""
    kv = ReadDown()
    assert _run(_gate(kv, daily_count_limit=1).check(_evt(), for_command=False)) is None
    assert getattr(kv, "puts", []) == []


def test_gate_write_failure_still_passes():
    kv = WriteDown()
    assert _run(_gate(kv, daily_count_limit=1).check(_evt(), for_command=False)) is None


def test_gate_counts_when_storage_is_healthy():
    """对照组：存储正常时配额真的会扣（否则上面的「放行」断言没意义）。"""
    kv = KvSpy()
    g = _gate(kv, daily_count_limit=1)
    assert _run(g.check(_evt(), for_command=False)) is None
    assert kv.store[f"sf_cmd_cnt_{UID}"]["count"] == 1
    blocked = _run(g.check(_evt(), for_command=False))
    assert isinstance(blocked, str) and blocked  # 第二次被拦


def test_gate_cooldown_write_failure_passes():
    kv = WriteDown()
    assert _run(_gate(kv, cmd_rate_limit=60).check(_evt(), for_command=True)) is None


# ================= ⑤ 渲染故障：海报挂了不拦主流程 =================
@pytest.mark.parametrize("outcome", ["raise", None, "", "   ", 123])
def test_poster_render_failure_returns_none(monkeypatch, outcome):
    """海报渲染任何失败形态（抛异常 / None / 空串 / 非 str）一律返回 None，
    调用方回退普通牌面图——海报是装饰，不是主流程。"""
    def fake_render(card, upright, signature, date_text, save_dir=None):
        if outcome == "raise":
            raise RuntimeError("pillow down")
        return outcome

    monkeypatch.setattr(daily_mod, "_render_daily_card_img", fake_render)
    monkeypatch.setattr(daily_mod, "_schedule_image_cleanup", lambda img, delay=30: None)
    got = _run(_fortune(KvSpy()).render_daily_card(["今日牌运"], [PICK], UID))
    assert got is None


def test_poster_render_success_is_scheduled_for_cleanup(monkeypatch):
    """对照组：成功路径要真的登记清理（否则 300 秒后没人删，图片目录只会涨）。"""
    seen = {}
    monkeypatch.setattr(daily_mod, "_render_daily_card_img",
                        lambda card, upright, sig, date_text, save_dir=None: "C:/tmp/x.png")
    monkeypatch.setattr(daily_mod, "_schedule_image_cleanup",
                        lambda img, delay=30: seen.update(img=img, delay=delay))
    got = _run(_fortune(KvSpy()).render_daily_card(["今日牌运"], [PICK], UID))
    assert got == "C:/tmp/x.png"
    assert seen == {"img": "C:/tmp/x.png", "delay": 300}


def test_poster_uses_lock_from_tarot(monkeypatch):
    """渲染必须走 tarot._render_lock 那道并发闸（海报与普通牌面图同一把）。"""
    import asyncio as aio
    lock = aio.Semaphore(1)
    monkeypatch.setattr(daily_mod, "_schedule_image_cleanup", lambda img, delay=30: None)
    monkeypatch.setattr(daily_mod, "_render_daily_card_img",
                        lambda card, upright, sig, date_text, save_dir=None: "C:/tmp/x.png")
    f = _fortune(KvSpy(), render_lock=lock)
    assert _run(f.render_daily_card(["今日牌运"], [PICK], UID)) == "C:/tmp/x.png"
    assert lock._value == 1  # 用完归还：没被吞掉


def test_render_slot_degrades_when_no_lock():
    """桩对象（没有 tarot / 没有锁）退化成不限并发，而不是 AttributeError 崩掉。"""
    with daily_mod._render_slot(types.SimpleNamespace()):
        pass
