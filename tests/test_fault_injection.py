"""故障注入矩阵：存储 / 上游故障 × 各消费者，逐格钉死降级契约。

为什么单独开一个文件：这类断言原先散在各处——test_core 管读故障与毒值、
test_kv_utils 管「无记录 vs 存储故障」、test_gating 管 KV 挂掉放行。抬眼看，
没人回答过「故障模式 × 消费者」这张网格上还有哪些格子是空的，于是散落的红线
各自绿着、契约整体可以偷偷歪。本文件把网格铺开：每种故障模式对每个消费者
（daily.pick_cached / daily.interp_cached / daily.spirit_cached / LimitGate.check /
海报渲染 / 牌面图的清理登记）只断言对外可观测的行为——返回什么、写不写回、放不放行，一律不碰内部实现。

三条铁律（跨格不变，改代码时先看它们）：
1. 故障不夺（性能上的）确定性：读坏了，`_daily_pick` 这类纯函数照样出同一张牌；
2. 降级不沉默：必须走「有话说」的兜底（池内当日句、AI 现场解读），不能返回空；
3. 写回判据＝「写回是否依赖旧数据」：依赖旧 dict 的（pick/interp）读故障不写；
   纯覆盖写的（spirit）读故障照写——门住会破坏「当日同牌组同一句」。

零随机：期望值全部由 daily._daily_pick（md5 种子纯函数）现算，不写字面量。
"""
import asyncio
import gc
import os
import time
import types
import warnings

import pytest
from data.plugins.astrbot_plugin_star_feather.core.dailylines import pick_signature
from data.plugins.astrbot_plugin_star_feather.core.dto import IncomingEvent
from data.plugins.astrbot_plugin_star_feather.services import daily as daily_mod
from stubs import PICK, FakeContext

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


# ---------------- 上游桩（解释器 / 渲染） ----------------
class _FakeInterp:
    """解释器桩：候选链与缓存层都只认 interpret / spirit_line 两个方法。"""

    def __init__(self, interp="AI解读文本", spirit="AI牌灵的话", exc=None):
        self.interp, self.spirit, self.exc = interp, spirit, exc

    async def interpret(self, umo, formation, positions, picks, user_input, detail,
                        persona_eff=None, budget=None):
        if self.exc:
            raise self.exc
        return self.interp

    async def spirit_line(self, umo, cards, topic, persona_eff, budget=None):
        if self.exc:
            raise self.exc
        return self.spirit


def _fortune(kv, **kw):
    kw.pop("render_lock", None)
    return daily_mod.DailyFortune(kv, persona="", interpreter=_FakeInterp(**kw))


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
    assert rec["card_id"] == _expected_pick()[2][0]["card"]["id"]


@pytest.mark.parametrize("mode", ["poison_list", "poison_str", "poison_int",
                                  "stale_day", "bad_card_id"])
def test_pick_write_back_drops_stale_interp_buckets(mode):
    """跨天 / 坏牌 id：旧解读分桶一律作废（否则同主题当天复用昨天的解读）。"""
    kv = MODES[mode]()
    _run(_fortune(kv).pick_cached(UID))
    assert kv.store[DAILY_KEY]["interps"] == {}


def test_pick_keeps_today_interp_buckets_when_record_is_fine():
    """同日正常记录：抽牌重生成不许丢弃已写好的解读分桶（合并写，不是覆盖写）。"""
    card, upright = daily_mod._daily_pick(UID, TODAY)
    kv = KvSpy({DAILY_KEY: {
        "schema": 2, "date": TODAY, "card_id": card["id"], "upright": upright,
        "sign": f"{card['id']}:{1 if upright else 0}",
        "prompt_v": daily_mod._PROMPT_V, "persona": "",
        "interps": {"（今日牌运）": "今天的解读"},
        "spirit_line": "", "spirit_sign": ""}})
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
def test_spirit_read_failure_does_not_blind_write():
    """单条记录里还有牌与解读：读故障时不盲写，避免把有效内容覆盖成空壳。"""
    kv = ReadDown()
    got = _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    assert got == "AI牌灵的话"
    assert kv.puts == []


def test_spirit_write_failure_does_not_break_reading():
    kv = WriteDown()
    got = _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    assert got == "AI牌灵的话"


def test_spirit_read_recovery_regenerates_then_hits():
    """读故障那次没写回；恢复后重新生成并写回，第三次命中同一句。"""
    kv = ReadDown()
    _run(_fortune(kv).spirit_cached(None, UID, [PICK], "今日运势", None))
    kv2 = KvSpy(kv.store)
    got = _run(_fortune(kv2, spirit="AI牌灵的另一句").spirit_cached(None, UID, [PICK],
                                                                    "今日运势", None))
    assert got == "AI牌灵的另一句"
    got2 = _run(_fortune(kv2, spirit="不会用到").spirit_cached(None, UID, [PICK],
                                                              "今日运势", None))
    assert got2 == "AI牌灵的另一句"


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
    from data.plugins.astrbot_plugin_star_feather.services.gating import LimitGate
    return LimitGate(kv, cmd_rate_limit, daily_count_limit)


def _evt(uid=UID):
    return IncomingEvent(user_id=uid, platform_id="", conversation_id="test_umo",
                         message_text="", platform_name="")


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
    """海报渲染任何失败形态一律返回 None，调用方回退普通牌面图。"""
    from data.plugins.astrbot_plugin_star_feather.services import render as render_mod
    from data.plugins.astrbot_plugin_star_feather.services.render import Renderer

    def fake_render(card, upright, signature, date_text, save_dir=None):
        if outcome == "raise":
            raise RuntimeError("pillow down")
        return outcome

    monkeypatch.setattr(render_mod, "render_daily_card", fake_render)
    monkeypatch.setattr(render_mod, "_schedule_image_cleanup", lambda img, delay=30: None)
    got = _run(Renderer("", "plain").daily_card([PICK], UID, "20260827", "8月27日·周四"))
    assert got is None


def test_poster_render_success_is_scheduled_for_cleanup(monkeypatch):
    """对照组：成功路径要真的登记清理；延迟由 render/store.py 的统一保险丝给。"""
    from data.plugins.astrbot_plugin_star_feather.services import render as render_mod
    from data.plugins.astrbot_plugin_star_feather.services.render import Renderer

    seen = {}
    monkeypatch.setattr(render_mod, "render_daily_card",
                        lambda card, upright, sig, date_text, save_dir=None: "C:/tmp/x.png")
    monkeypatch.setattr(render_mod, "_schedule_image_cleanup",
                        lambda img, delay=None: seen.update(img=img, delay=delay))
    got = _run(Renderer("", "plain").daily_card([PICK], UID, "20260827", "8月27日·周四"))
    assert got == "C:/tmp/x.png"
    assert seen == {"img": "C:/tmp/x.png", "delay": None}


# ================= ⑥ 图片生命周期故障：发送前图没了，不能把整条消息带走 =================
def _entry_evt():
    evt = types.SimpleNamespace(unified_msg_origin="umo", message_str="问感情")
    evt.result = None
    evt.sent = []
    evt.get_self_id = lambda: "12345"
    evt.get_sender_id = lambda: "u1"
    evt.should_call_llm = lambda v: None

    def chain_result(chain):
        evt.result = chain
        return chain
    evt.chain_result = chain_result

    async def send(chain):
        evt.sent.append(chain)
    evt.send = send
    return evt


def _entry_plugin(*, settings, daily, interpreter, renderer):
    from data.plugins.astrbot_plugin_star_feather.adapters.clock import Clock
    from data.plugins.astrbot_plugin_star_feather.adapters.deliver import Deliverer
    from data.plugins.astrbot_plugin_star_feather.adapters.presenter import Presenter
    from data.plugins.astrbot_plugin_star_feather.services.reading import ReadingService

    clock = Clock()
    reading = ReadingService(settings=settings, clock=clock, daily=daily,
                             interpreter=interpreter, renderer=renderer)
    presenter = Presenter(Deliverer(settings.output.segment_size,
                                    settings.output.disclaimer))

    async def gate_check(incoming, for_command):
        return None

    container = types.SimpleNamespace(
        settings=settings, gate=types.SimpleNamespace(check=gate_check),
        reading=reading, presenter=presenter, renderer=renderer, clock=clock)
    return types.SimpleNamespace(container=container, context=types.SimpleNamespace())


def _run_entry(p, evt, question="羽签 问感情"):
    from data.plugins.astrbot_plugin_star_feather.adapters.astrbot_io import (
        messenger_for,
    )
    from data.plugins.astrbot_plugin_star_feather.adapters.event import (
        delivery_capabilities,
        incoming_from_event,
    )
    from data.plugins.astrbot_plugin_star_feather.app import handlers
    caps = delivery_capabilities(
        evt, p.container.settings.output.send_mode == "forward")
    _run(handlers.command_entry(p.container, incoming_from_event(evt), caps,
                                messenger_for(evt), question, version="0",
                                err_tpl="断了"))


def _plain_settings(**over):
    from data.plugins.astrbot_plugin_star_feather.core.settings import parse
    cfg = {"ai": {"enable_ai": False, "parallel_stages": True},
           "output": {"send_mode": "plain", "daily_fixed": False,
                      "daily_card": False, "shuffle_lines": False}}
    for group, values in over.items():
        cfg.setdefault(group, {}).update(values)
    return parse(cfg)


class TestImageGoneBeforeSend:
    """Issue #2：牌面图在**发送之前**就没了，平台在发送那一刻读文件读到空，
    整条消息链（图 + 解读 + 牌灵的话）一起被打回——用户什么都没收到。

    修复后的出口纪律：发送前核验（Renderer.ensure_image）补渲染一次；补不回来就降级无图，
    消息照发。渲染已后置到两段 AI 之后，这里守的是出口，不是图片年龄。
    """

    FORMATION = "羽签"
    POSITIONS = ["你的当下"]

    @staticmethod
    def _plugin(render, on_after_render=None):
        """真入口 + 真渲染器 + 真分发，只把「渲染实现」和 AI 换成可控桩。"""
        from data.plugins.astrbot_plugin_star_feather.adapters.clock import Clock
        from data.plugins.astrbot_plugin_star_feather.ai.chain import AiInterpreter
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        from data.plugins.astrbot_plugin_star_feather.services.render import Renderer

        calls = []

        def fake_sync(formation, positions, picks):
            calls.append(formation)
            path = render()
            if on_after_render and len(calls) == 1:
                on_after_render()
            return path

        renderer = Renderer(image_dir="", send_mode="plain")
        renderer._render_sync = fake_sync
        settings = _plain_settings()
        interpreter = AiInterpreter(FakeContext(None), enable_ai=False)
        daily = DailyFortune(KvSpy(), clock=Clock(), interpreter=interpreter)
        p = _entry_plugin(settings=settings, daily=daily, interpreter=interpreter,
                          renderer=renderer)
        return p, calls

    @staticmethod
    def _images(chain):
        return [c for c in chain if type(c).__name__ == "Image"]

    def test_image_deleted_mid_window_is_rerendered_before_send(self, tmp_path):
        """图在「渲染 → 发送」之间消失 → 发送前补渲染一张，交出去的是**存在的**文件。"""
        first = str(tmp_path / "tarot_first.png")
        fresh = str(tmp_path / "tarot_fresh.png")
        rendered = []

        def render():
            path = fresh if rendered else first
            rendered.append(path)
            open(path, "wb").write(b"PNG")
            return path

        def kill_the_image():
            os.remove(first)

        p, calls = self._plugin(render, on_after_render=kill_the_image)
        evt = _entry_evt()
        _run_entry(p, evt)
        assert evt.result, "图补回来了，消息必须照发"
        imgs = self._images(evt.result)
        assert len(imgs) == 1
        assert imgs[0].file == fresh, "交出去的仍是被删掉的旧路径＝平台还是会读到空"
        assert os.path.exists(imgs[0].file), "发送那一刻文件必须真实存在"
        assert calls == [self.FORMATION, self.FORMATION], "正好补渲染一次"

    def test_rerender_failure_degrades_to_text_and_still_sends(self, tmp_path):
        """图没了而且重渲染也失败 → 降级为无图，但解读照发。"""
        path = str(tmp_path / "tarot_only.png")
        rendered = []

        def render():
            rendered.append(path)
            if len(rendered) > 1:
                return None
            open(path, "wb").write(b"PNG")
            return path

        p, calls = self._plugin(render, on_after_render=lambda: os.remove(path))
        evt = _entry_evt()
        _run_entry(p, evt)
        assert evt.result, "无图也必须把解读发出去"
        assert not self._images(evt.result), "图已不存在，就不该再把 Image 交出去"
        texts = [getattr(c, "text", "") for c in evt.result]
        assert any(texts), "无图时补逐牌牌义（既有退路）"
        assert calls == [self.FORMATION, self.FORMATION]

    def test_image_alive_adds_no_extra_render(self, tmp_path):
        """对照组：图还在 → 一次都不多渲染。"""
        path = str(tmp_path / "tarot_ok.png")

        def render():
            open(path, "wb").write(b"PNG")
            return path

        p, calls = self._plugin(render)
        evt = _entry_evt()
        _run_entry(p, evt)
        assert [c.file for c in self._images(evt.result)] == [path]
        assert calls == [self.FORMATION], "图在就不该重渲染"

    def test_no_image_skips_verification(self):
        """本来就无图：不核验、不重渲染，消息照发。"""
        p, calls = self._plugin(lambda: None)
        evt = _entry_evt()
        _run_entry(p, evt)
        assert evt.result
        assert calls == [self.FORMATION]
        assert not self._images(evt.result)

    def test_verify_image_returns_as_is_on_healthy_path(self, tmp_path):
        """核验函数自身的契约：存在→原样返回；空/None→原样返回（不额外渲染）。"""
        p, calls = self._plugin(lambda: None)
        renderer = p.container.renderer
        path = str(tmp_path / "tarot_v.png")
        open(path, "wb").write(b"PNG")
        assert _run(renderer.ensure_image(path, self.FORMATION, self.POSITIONS, [PICK])) == path
        assert _run(renderer.ensure_image(None, self.FORMATION, self.POSITIONS, [PICK])) is None
        assert _run(renderer.ensure_image("", self.FORMATION, self.POSITIONS, [PICK])) == ""
        assert calls == [], "这三种情形都不该触发渲染"

    def test_verify_image_failure_paths_return_none(self, tmp_path):
        """核验函数：图不在 → 重渲染；重渲染再失败 → 无图，不把异常带给发送侧。"""
        p, _ = self._plugin(lambda: None)
        renderer = p.container.renderer
        missing = str(tmp_path / "never_there.png")

        async def boom(*a):
            raise RuntimeError("pillow down")

        renderer.card = boom
        assert _run(renderer.ensure_image(missing, self.FORMATION,
                                          self.POSITIONS, [PICK])) is None

    def test_verify_image_treats_a_vanishing_file_as_missing(self, tmp_path, monkeypatch):
        """exists 与 getsize 之间的竞态：getsize 抛 FileNotFoundError 也要走补渲染。"""
        stale = str(tmp_path / "tarot_race.png")
        fresh = str(tmp_path / "tarot_fresh.png")
        open(stale, "wb").write(b"PNG")

        def render():
            open(fresh, "wb").write(b"PNG")
            return fresh

        p, calls = self._plugin(render)
        real_getsize = os.path.getsize

        def flaky(path):
            if path == stale:
                os.remove(stale)
                raise FileNotFoundError(path)
            return real_getsize(path)

        monkeypatch.setattr(os.path, "getsize", flaky)
        assert _run(p.container.renderer.ensure_image(
            stale, self.FORMATION, self.POSITIONS, [PICK])) == fresh
        assert calls == [self.FORMATION], "消失的图必须补渲染一次"

    def test_verify_image_treats_other_oserrors_as_missing(self, tmp_path, monkeypatch):
        """权限 / 路径非法等 OSError 同样走补渲染。"""
        stale = str(tmp_path / "tarot_unreadable.png")
        fresh = str(tmp_path / "tarot_fresh2.png")
        open(stale, "wb").write(b"PNG")

        def render():
            open(fresh, "wb").write(b"PNG")
            return fresh

        p, calls = self._plugin(render)
        real_getsize = os.path.getsize

        def denied(path):
            if path == stale:
                raise PermissionError(13, "denied", path)
            return real_getsize(path)

        monkeypatch.setattr(os.path, "getsize", denied)
        assert _run(p.container.renderer.ensure_image(
            stale, self.FORMATION, self.POSITIONS, [PICK])) == fresh
        assert calls == [self.FORMATION], "问不出大小的图必须补渲染一次"

    def test_verify_image_treats_valueerror_as_missing(self, tmp_path):
        """含 NUL 的路径会让 getsize 抛 ValueError，同样不能带走整条消息。"""
        fresh = str(tmp_path / "tarot_fresh3.png")

        def render():
            open(fresh, "wb").write(b"PNG")
            return fresh

        p, calls = self._plugin(render)
        assert _run(p.container.renderer.ensure_image(
            "a\0b.png", self.FORMATION, self.POSITIONS, [PICK])) == fresh
        assert calls == [self.FORMATION], "ValueError 也必须走补渲染"


# ============ ② 图片后置渲染：渲染排在两段 AI 之后（2026-10-12） ============
class TestRenderAfterBothAI:
    """顺序契约：渲染必须发生在牌灵的话与解读**之后**、发送之前。"""

    FORMATION = "羽签"
    POSITIONS = ["你的当下"]

    def _timeline(self):
        seen = []

        class Daily:
            async def pick_cached(self, uid, today=None):
                return None

            async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None,
                                    *, today=None):
                seen.append("spirit")
                return "牌灵今天懒得开口"

            async def interp_cached(self, *a, **kw):
                return "【第1张·你的当下】一段解读。"

        class Interp:
            def new_budget(self):
                from data.plugins.astrbot_plugin_star_feather.ai.chain import AiBudget
                return AiBudget(120)

            async def interpret(self, umo, formation, positions, picks, user_input,
                                detail, persona_eff=None, budget=None):
                seen.append("interp")
                return "【第1张·你的当下】一段解读。"

        class Renderer:
            async def card(self, formation, positions, picks):
                seen.append("render")
                return None

            async def daily_card(self, picks, uid, today, date_text):
                return None

            async def ensure_image(self, img, formation, positions, picks):
                return img

        p = _entry_plugin(settings=_plain_settings(), daily=Daily(),
                          interpreter=Interp(), renderer=Renderer())
        evt = _entry_evt()
        _run_entry(p, evt)
        return seen, evt

    def test_render_is_called_after_both_ai_stages(self):
        seen, evt = self._timeline()
        assert seen == ["spirit", "interp", "render"], (
            f"渲染必须在两段 AI 之后（实际时间线 {seen!r}）")

    def test_render_failure_still_sends_result(self):
        """渲染返回 None：结果照发，不因图扣下消息。"""
        _, evt = self._timeline()
        assert evt.result, "无图也要把解读发出去"
        texts = [getattr(c, "text", "") for c in evt.result]
        assert any(texts), "无图时走既有退路（补逐牌牌义）"


# ---------------- 窄口子：保险丝登记失败，不许带走主流程 ----------------
#
# 【这里为什么不再有「取本代模块」的机关】2026-10-12 之前，main.py 顶部会把
# 子模块从 sys.modules 里 pop 掉再导入（重载防御），于是同一个文件在进程里可能有两份
# 身份：「先 import 旧版平铺模块、再 import main」拿到的是旧一代副本，补丁打在旧那份上
# ——静默打空、用例假绿（本羽第一版就栽在这里）。现在插件模块只有包名一个身份，谁先
# 导入都是同一份，机关连同它的注释一起退役。

# 复用同形事件桩：就地再写一份只会多一个会过时的点。
# 名字必须带前缀——第一版顺手写成 _evt，把本文件顶部的模块级 _evt(uid) 覆盖掉了，
# 于是「对照组：存储正常时配额真的会扣」那条用例拿到别的 uid，KeyError 红。
# 别名 / 补丁 / 替换这类东西的射程，永远比你以为的宽一格。
_gone_evt = _entry_evt


def _live_modules():
    """返回（StarFeatherPlugin, services.render, render.store, services.daily）——同一份模块对象。

    插件内模块之间是包内相对导入，一条链上只有一个 services.render、一个 render.store，
    按包路径直接 import 即得。末尾留一条同源自检：补丁打空会变成永远绿的假证据。
    """
    from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
    from data.plugins.astrbot_plugin_star_feather.render import store as render_store
    from data.plugins.astrbot_plugin_star_feather.services import daily
    from data.plugins.astrbot_plugin_star_feather.services import (
        render as render_service,
    )

    fuse = render_service._schedule_image_cleanup
    assert fuse.__globals__ is render_store.__dict__, \
        "补丁会打空：services.render 与 render.store 不是同一份模块"
    return StarFeatherPlugin, render_service, render_store, daily


class _ShuttingDownLoop:
    """只废掉「排任务」一件事，其余照常转发真 asyncio。

    热重载 / 进程收尾时事件循环正是这样：get_running_loop 还答得出话，
    create_task 抛 RuntimeError('cannot schedule new futures after shutdown')。
    """

    def __getattr__(self, name):
        if name == "create_task":
            raise RuntimeError("cannot schedule new futures after shutdown")
        return getattr(asyncio, name)


def _loop_shutting_down(monkeypatch, *holders):
    """把保险丝眼里的事件循环换成收尾态。

    注入点刻意落在保险丝**内部那一步**，而不是替换 `_schedule_image_cleanup` 这个名字：
    替换名字等于绕过被测的那道兜底，测出来的是空气。holders 传调用点所在的模块。
    """
    for mod in holders:
        fuse = mod._schedule_image_cleanup
        monkeypatch.setitem(fuse.__globals__, "asyncio", _ShuttingDownLoop())


class TestCleanupFuseNeverCarriesTheReading:
    """2026-10-01 逐点穷举注入查出来的窄口子：牌面图渲染有 try/except + 文字回退，
    紧跟着的「登记清理」那一行却裸在兜底之外。保险丝失效的代价＝临时目录里多一张图，
    整卦丢掉的代价＝用户这一次占卜没了。前者永远不该换来后者。
    """

    FORMATION = "羽签"
    POSITIONS = ["你的当下"]

    def _reading_with_real_fuse(self, render_service, tmp_path):
        """真入口 + 真渲染器 + 真登记清理，只把渲染实现与 AI 换桩。"""
        from data.plugins.astrbot_plugin_star_feather.adapters.clock import Clock
        from data.plugins.astrbot_plugin_star_feather.ai.chain import AiInterpreter
        from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
        from data.plugins.astrbot_plugin_star_feather.services.render import Renderer

        path = str(tmp_path / "fuse_reading.png")

        def fake_render_sync(formation, positions, picks):
            open(path, "wb").write(b"PNG")
            return path

        renderer = Renderer(image_dir=str(tmp_path), send_mode="plain")
        renderer._render_sync = fake_render_sync
        settings = _plain_settings()
        interpreter = AiInterpreter(FakeContext(None), enable_ai=False)
        daily = DailyFortune(KvSpy(), clock=Clock(), interpreter=interpreter)
        p = _entry_plugin(settings=settings, daily=daily, interpreter=interpreter,
                          renderer=renderer)
        return p, path

    def test_reading_survives_when_fuse_cannot_register(self, monkeypatch, tmp_path):
        _Plugin, render_service, _cr, _daily = _live_modules()
        p, path = self._reading_with_real_fuse(render_service, tmp_path)
        _loop_shutting_down(monkeypatch, render_service)
        evt = _gone_evt()
        _run_entry(p, evt)
        assert evt.result, "登记删图失败只是少个保险丝；整条卦不能跟着丢"
        imgs = [c for c in evt.result if type(c).__name__ == "Image"]
        assert [i.file for i in imgs] == [path], "该发出去的图照样发"
        assert any(getattr(c, "text", "") for c in evt.result), "解读/牌义不能缺"

    def test_poster_survives_when_fuse_cannot_register(self, monkeypatch, tmp_path):
        """同一注入、同一条保险丝，走海报卡：登记失败不该把海报降级成普通牌面图。"""
        from data.plugins.astrbot_plugin_star_feather.services.render import Renderer
        _Plugin, render_service, _cr, _daily = _live_modules()
        path = str(tmp_path / "fuse_poster.png")

        def fake_poster(card, upright, signature, date_text, save_dir=None):
            open(path, "wb").write(b"PNG")
            return path

        monkeypatch.setattr(render_service, "render_daily_card", fake_poster)
        _loop_shutting_down(monkeypatch, render_service)
        renderer = Renderer(image_dir=str(tmp_path), send_mode="plain")
        got = _run(renderer.daily_card([PICK], UID, "20260827", "8月27日·周四"))
        assert got == path

    def test_fuse_swallows_and_leaves_no_dangling_coroutine(self, monkeypatch):
        """保险丝自己的契约：登记失败既不抛，也不留「never awaited」的告警噪声
        （create_task 抛之前那个协程已经建出来了，得 close 掉）。"""
        _Plugin, _tc, render_store, _daily = _live_modules()
        _loop_shutting_down(monkeypatch, render_store)

        async def call_it():
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                render_store._schedule_image_cleanup("C:/not/exist/tarot_fuse.png")
                gc.collect()
            return [str(w.message) for w in caught]

        msgs = _run(call_it())
        assert not [m for m in msgs if "never awaited" in m], msgs
