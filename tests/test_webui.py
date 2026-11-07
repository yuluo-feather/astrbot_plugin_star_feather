"""可视化视图后端（app/webui.py）：今日列表、详情序列化、牌面缩略图与注册守卫。

视图本身是静态页（views/star-feather）；这一域只锁后端数据面，不测 JS。
宿主 KV 不能枚举，今日名单依赖 DailyFortune 的日索引（另一条红线在 test_daily_cache）。
"""
import asyncio
import base64
import io
import sys
import types

import pytest
from data.plugins.astrbot_plugin_star_feather.app.webui import WebUI
from data.plugins.astrbot_plugin_star_feather.core.models import Interpretation
from data.plugins.astrbot_plugin_star_feather.core.settings import (
    STRUCTURE_CHECK_LOOSE,
    STRUCTURE_CHECK_STRICT,
)
from data.plugins.astrbot_plugin_star_feather.core.spreads import FORMATIONS
from data.plugins.astrbot_plugin_star_feather.core.tarot_data import TAROT_CARDS
from data.plugins.astrbot_plugin_star_feather.services.daily import DailyFortune
from PIL import Image
from stubs import PICK

DAY = "20260827"
TOPIC = "事业运势"


class _Ctx:
    def __init__(self, kv):
        self.kv = kv

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


class _Interp:
    async def interpret(self, *a, **kw):
        return "【第1张·你的当下】新解读。"

    async def spirit_line(self, *a, **kw):
        return "牌灵的一句话"


def _make(kv, structure_check=STRUCTURE_CHECK_LOOSE):
    daily = DailyFortune(_Ctx(kv), persona="", interpreter=_Interp(),
                         structure_check=structure_check)
    settings = types.SimpleNamespace(
        ai=types.SimpleNamespace(enable_ai=True, spirit_line=True,
                                 structure_check=STRUCTURE_CHECK_LOOSE,
                                 persona="random"),
        output=types.SimpleNamespace(send_mode="forward", daily_card=True),
    )
    clock = types.SimpleNamespace(today=lambda: DAY)
    return daily, WebUI(daily, settings, clock, version="0.10.0")


def _ui_with_today(ui, today):
    return WebUI(ui.daily, ui.settings,
                 types.SimpleNamespace(today=lambda: today), version=ui.version)


def _seed(daily, uid):
    picks = asyncio.run(daily.pick_cached(uid, DAY))[2]
    asyncio.run(daily.spirit_cached(None, uid, picks, TOPIC, None, today=DAY))
    asyncio.run(daily.interp_cached(None, uid, "羽签", ["你的当下"], picks,
                                    TOPIC, today=DAY))
    return picks


def test_daily_list_lists_indexed_users():
    kv = {}
    daily, ui = _make(kv)
    picks = _seed(daily, "u1")
    data = asyncio.run(ui.daily_list())
    assert data["date"] == DAY
    assert [item["uid"] for item in data["items"]] == ["u1"]
    item = data["items"][0]
    assert item["card_cn"] == picks[0]["card"]["cn"]
    assert item["upright"] is picks[0]["upright"]
    assert item["topics"] == 1
    assert item["stale"] is False


def test_daily_list_reports_indexed_total_and_no_truncation():
    kv = {}
    daily, ui = _make(kv)
    _seed(daily, "u1")
    data = asyncio.run(ui.daily_list())
    assert data["indexed_total"] == 1
    assert data["shown"] == 1
    assert data["truncated"] is False


def test_daily_list_skips_invalid_records_until_the_cap_is_filled(monkeypatch):
    """最近的条目没有有效记录时继续往前找，不能让无效条目占掉名额。（#119）"""
    from data.plugins.astrbot_plugin_star_feather.app import webui as webui_module

    monkeypatch.setattr(webui_module, "_MAX_LIST", 2)
    uids = ["u0", "u1", "u2", "u3"]
    kv = {"sf_daily_index": {"date": DAY, "uids": uids}}
    daily, ui = _make(kv)
    _seed(daily, "u0")   # 较早，但有当天记录
    _seed(daily, "u1")   # 较早，但有当天记录
    data = asyncio.run(ui.daily_list())
    assert data["indexed_total"] == 4
    assert data["shown"] == 2
    assert data["truncated"] is False
    # 保持扫描序（最近登记在前），不按 uid 重排
    assert [item["uid"] for item in data["items"]] == ["u1", "u0"]


def test_daily_list_prefers_the_most_recent_valid_users_past_the_cap(monkeypatch):
    """凑满上限后没扫到的旧用户才算截断，且展示的是最近那批。（SF-WEB-02）"""
    from data.plugins.astrbot_plugin_star_feather.app import webui as webui_module

    monkeypatch.setattr(webui_module, "_MAX_LIST", 2)
    uids = ["u0", "u1", "u2", "u3"]
    kv = {"sf_daily_index": {"date": DAY, "uids": uids}}
    daily, ui = _make(kv)
    for uid in uids:
        _seed(daily, uid)
    data = asyncio.run(ui.daily_list())
    assert data["indexed_total"] == 4
    assert data["shown"] == 2
    assert data["truncated"] is True
    # 展示最近登记的那批，且保持最近在前（前端默认打开第一条）
    assert [item["uid"] for item in data["items"]] == ["u3", "u2"]


def test_detail_serializes_topics_and_stale_flag():
    kv = {}
    daily, ui = _make(kv)
    picks = _seed(daily, "u1")
    kv["sf_daily_u1"]["prompt_v"] = "old-version"
    data = asyncio.run(ui.daily_detail("u1"))
    assert data["stale"] is True
    assert data["card"]["id"] == picks[0]["card"]["id"]
    assert data["spirit"] == "牌灵的一句话"
    assert [topic["topic"] for topic in data["topics"]] == [TOPIC]
    assert "新解读" in data["topics"][0]["text"]


def test_detail_missing_user_is_none():
    _, ui = _make({})
    assert asyncio.run(ui.daily_detail("nobody")) is None


def test_detail_hides_non_today_record():
    kv = {}
    daily, ui = _make(kv)
    _seed(daily, "u1")
    stale = _ui_with_today(ui, "20260828")
    assert asyncio.run(stale.daily_detail("u1")) is None


def test_detail_handler_returns_404_for_non_today_record(monkeypatch):
    """Handler-level 404 mapping; full HTTP dispatch belongs to AstrBot's web layer."""
    kv = {}
    daily, ui = _make(kv)
    _seed(daily, "u1")
    stale = _ui_with_today(ui, "20260828")
    web = types.ModuleType("astrbot.api.web")
    web.request = types.SimpleNamespace(query={"uid": "u1"})
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
    monkeypatch.setattr(sys.modules["astrbot.api"], "web", web, raising=False)
    data, status = asyncio.run(stale.h_daily_detail())
    assert status == 404
    assert data["status"] == "error"


def test_preview_picks_reject_duplicate_cards():
    spread = "羽时三刻"
    card_id = TAROT_CARDS[0]["id"]
    raw = [{"id": card_id, "upright": True} for _ in FORMATIONS[spread]]
    with pytest.raises(ValueError, match="不能重复"):
        WebUI._picks_from_payload(spread, raw)


def test_overview_counts_today_records():
    kv = {}
    daily, ui = _make(kv)
    _seed(daily, "u1")
    _seed(daily, "u2")
    data = asyncio.run(ui.overview())
    assert data["users"] == 2
    assert data["with_spirit"] == 2
    assert data["topics"] == 2
    assert data["stale"] == 0
    assert data["version"] == "0.10.0"
    assert data["settings"]["send_mode"] == "forward"


def test_cards_returns_the_whole_deck():
    _, ui = _make({})
    cards = ui.cards()
    assert len(cards) == len(TAROT_CARDS) == 78
    assert len({card["id"] for card in cards}) == 78


def test_cards_expose_english_names():
    _, ui = _make({})
    cards = ui.cards()
    assert all(isinstance(card.get("en"), str) for card in cards)
    assert cards[0]["en"] == TAROT_CARDS[0]["en"]


def test_card_detail_exposes_both_orientations():
    _, ui = _make({})
    detail = ui.card_detail(PICK["card"]["id"])
    assert detail["cn"] == PICK["card"]["cn"]
    assert detail["up"]["keywords"] and detail["up"]["what"] and detail["up"]["how"]
    assert detail["down"]["keywords"] and detail["down"]["turn"]


def test_card_detail_rejects_unknown_card():
    _, ui = _make({})
    with pytest.raises(FileNotFoundError):
        ui.card_detail("not-a-card")


def test_spreads_reuse_formation_positions():
    _, ui = _make({})
    spreads = ui.spreads()
    assert [spread["name"] for spread in spreads] == list(FORMATIONS)
    for spread in spreads:
        assert [position["name"] for position in spread["positions"]] == \
            FORMATIONS[spread["name"]]
        assert spread["scenario"]


def test_card_image_is_webp_and_reversed_differs():
    _, ui = _make({})
    upright = ui.card_image(PICK["card"]["id"], True, 120)
    reversed_ = ui.card_image(PICK["card"]["id"], False, 120)
    assert upright["mime"] == "image/webp"
    assert upright["width"] == 120
    raw = base64.b64decode(upright["base64"])
    with Image.open(io.BytesIO(raw)) as img:
        assert img.size == (120, upright["height"])
    assert upright["base64"] != reversed_["base64"]
    assert ui.card_image(PICK["card"]["id"], True, 120) is upright  # 同尺寸只编码一次


def test_card_back_image_uses_the_webp_asset():
    from data.plugins.astrbot_plugin_star_feather.render.assets import card_back_path

    path = card_back_path()
    assert path.endswith("背景.webp")
    _, ui = _make({})
    back = ui.card_image("__back__", True, 120)
    assert back["mime"] == "image/webp"
    assert back["width"] == 120
    raw = base64.b64decode(back["base64"])
    with Image.open(io.BytesIO(raw)) as img:
        assert img.size == (120, back["height"])
    # 牌背不随正逆位旋转，且同尺寸复用同一份缓存
    assert ui.card_image("__back__", False, 120) is back


def test_card_image_rejects_unknown_card():
    _, ui = _make({})
    with pytest.raises(FileNotFoundError):
        ui.card_image("not-a-card")


class _Host:
    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, methods, desc))


def test_register_needs_host_api_and_uses_plugin_prefix():
    _, ui = _make({})
    assert ui.register(types.SimpleNamespace()) is False

    host = _Host()
    assert ui.register(host) is True
    routes = [route for route, _methods, _desc in host.routes]
    assert len(routes) == 9
    assert all(route.startswith("/astrbot_plugin_star_feather/") for route in routes)
    assert "/astrbot_plugin_star_feather/daily/list" in routes
    assert "/astrbot_plugin_star_feather/card-detail" in routes
    assert "/astrbot_plugin_star_feather/spreads" in routes
    assert "/astrbot_plugin_star_feather/preview/draw" in routes
    assert "/astrbot_plugin_star_feather/preview/interpret" in routes


class _PreviewInterp:
    def __init__(self):
        self.calls = 0

    def new_budget(self):
        return None

    async def interpret(self, *args, **kwargs):
        self.calls += 1
        text = "【第1张·你的当下】预览解读。"
        return Interpretation(text=text, sections=(text,), summary="")


def _make_preview(kv, enable_ai=True):
    interp = _PreviewInterp()
    daily = DailyFortune(_Ctx(kv), persona="", interpreter=interp)
    settings = types.SimpleNamespace(
        ai=types.SimpleNamespace(enable_ai=enable_ai, spirit_line=True,
                                 structure_check=STRUCTURE_CHECK_LOOSE,
                                 persona="random"),
        output=types.SimpleNamespace(send_mode="forward", daily_card=True),
    )
    clock = types.SimpleNamespace(today=lambda: DAY)
    return WebUI(daily, settings, clock, version="0.10.0"), interp


def test_preview_draw_uses_the_requested_spread():
    ui, _ = _make_preview({})
    data = ui.draw_preview("羽时三刻", "最近怎么样")
    assert data["positions"] == FORMATIONS["羽时三刻"]
    assert len(data["picks"]) == 3
    ids = {card["id"] for card in TAROT_CARDS}
    for pick in data["picks"]:
        assert pick["id"] in ids
        assert pick["keywords"] and pick["what"] and pick["advice"]


def test_preview_draw_rejects_unknown_spread():
    ui, _ = _make_preview({})
    with pytest.raises(ValueError):
        ui.draw_preview("不存在的阵")


def test_preview_interpret_is_opt_in_and_writes_nothing():
    kv = {}
    ui, interp = _make_preview(kv)
    picks = ui.draw_preview("羽签")["picks"]
    raw = [{"id": pick["id"], "upright": pick["upright"]} for pick in picks]
    result = asyncio.run(ui.interpret_preview("羽签", "预览", raw))
    assert result["text"].startswith("【第1张")
    assert interp.calls == 1
    assert kv == {}


def test_preview_interpret_honors_ai_switch():
    ui, interp = _make_preview({}, enable_ai=False)
    with pytest.raises(RuntimeError):
        asyncio.run(ui.interpret_preview(
            "羽签", "", [{"id": "major_00", "upright": True}]))
    assert interp.calls == 0


def test_preview_interpret_rejects_tampered_picks():
    ui, _ = _make_preview({})
    with pytest.raises(ValueError):
        asyncio.run(ui.interpret_preview(
            "羽签", "", [{"id": "not-a-card", "upright": True}]))
    with pytest.raises(ValueError):
        asyncio.run(ui.interpret_preview(
            "羽时三刻", "", [{"id": "major_00", "upright": True}]))


def test_preview_rejects_non_boolean_upright():
    """正逆位只收真布尔：字符串 "false" 不能被 bool() 悄悄转成正位。"""
    ui, _ = _make_preview({})
    card_id = ui.draw_preview("羽签")["picks"][0]["id"]
    for bad in ("false", "true", 0, 1, None):
        with pytest.raises(ValueError):
            ui._picks_from_payload("羽签", [{"id": card_id, "upright": bad}])
    # 字段缺失仍按正位处理（与前端默认一致）
    _, picks = ui._picks_from_payload("羽签", [{"id": card_id}])
    assert picks[0]["upright"] is True


def test_persona_change_is_marked_stale_everywhere():
    kv = {}
    daily, ui = _make(kv)
    _seed(daily, "u1")
    kv["sf_daily_u1"]["persona"] = "gentle"  # 当前实例 persona 是 ""
    detail = asyncio.run(ui.daily_detail("u1"))
    assert detail["stale"] is True
    listing = asyncio.run(ui.daily_list())
    assert listing["items"][0]["stale"] is True
    overview = asyncio.run(ui.overview())
    assert overview["stale"] == 1


def test_overview_counts_beyond_the_list_cap():
    kv = {}
    daily, ui = _make(kv)
    for index in range(201):
        asyncio.run(daily.pick_cached(f"u{index}", DAY))
    data = asyncio.run(ui.overview())
    assert data["users"] == 201


def test_overview_bounds_concurrent_record_reads(monkeypatch):
    """索引再大也只维持固定并发；统计口径与串行版本逐项一致。"""
    daily, ui = _make({})
    uids = [f"u{index}" for index in range(64)]
    inflight = 0
    peak = 0

    async def users_for(today):
        return uids

    async def read_record(uid):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0)
        inflight -= 1
        number = int(uid[1:])
        if number % 4 == 0:
            return None
        return types.SimpleNamespace(
            date=DAY if number % 3 else "20260828",
            spirit_line="牌灵" if number % 2 else "",
            interps={"事业": "x", "感情": "y"} if number % 5 == 0 else {},
            prompt_v="old" if number % 7 == 0 else daily.prompt_version,
            persona=daily.persona,
        )

    monkeypatch.setattr(daily, "users_for", users_for)
    monkeypatch.setattr(daily, "read_record", read_record)
    data = asyncio.run(ui.overview())

    expected_active = expected_spirit = expected_topics = expected_stale = 0
    for number in range(len(uids)):
        if number % 4 == 0 or number % 3 == 0:
            continue
        expected_active += 1
        if number % 2:
            expected_spirit += 1
        if number % 5 == 0:
            expected_topics += 2
        if number % 7 == 0:
            expected_stale += 1

    assert 1 < peak <= 8
    assert data["users"] == expected_active
    assert data["with_spirit"] == expected_spirit
    assert data["topics"] == expected_topics
    assert data["stale"] == expected_stale


def test_bounded_reads_keep_index_order(monkeypatch):
    """并发读取按 uids 下标回填，返回顺序不受完成先后影响。"""
    daily, ui = _make({})
    uids = [f"u{index}" for index in range(20)]

    async def read_record(uid):
        index = int(uid[1:])
        # 越靠前的用户越慢，完成顺序与索引顺序相反
        await asyncio.sleep(0.002 * (len(uids) - index) / 10)
        return types.SimpleNamespace(uid=uid)

    monkeypatch.setattr(daily, "read_record", read_record)
    records = asyncio.run(ui._read_records_bounded(uids))
    assert [record.uid for record in records] == uids


def test_card_image_cache_stays_bounded():
    _, ui = _make({})
    for width in range(60, 160):
        ui.card_image(PICK["card"]["id"], True, width)
    assert len(ui._thumbs) == 96


def test_strict_structure_failure_marks_record_stale():
    kv = {}
    daily, ui = _make(kv, structure_check=STRUCTURE_CHECK_STRICT)
    _seed(daily, "u1")
    assert asyncio.run(ui.daily_detail("u1"))["stale"] is False
    topic = next(iter(kv["sf_daily_u1"]["interps"]))
    kv["sf_daily_u1"]["interps"][topic] = "【第1张·】没有位置名的旧解读"
    assert asyncio.run(ui.daily_detail("u1"))["stale"] is True
    assert asyncio.run(ui.daily_list())["items"][0]["stale"] is True
    assert asyncio.run(ui.overview())["stale"] == 1
