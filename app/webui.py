"""插件可视化视图（``views/star-feather``）的只读后端。

视图跑在 Dashboard 的受限 iframe 里，只能通过 bridge 调这里注册的接口。
第一版只读：不改 KV、不改配置，也不删除任何记录。

宿主 KV 不支持枚举，今日用户名单来自 ``DailyFortune`` 维护的日索引；
牌面缩略图以 base64 JSON 返回——iframe 不能直接访问插件素材目录。
"""
from __future__ import annotations

import base64
import io
import random
from collections import OrderedDict
from typing import TypedDict

from PIL import Image

from ..ai.parse import interpretation_from_record, validate_interpret_structure_strict
from ..ai.persona import resolve_persona
from ..core.draw import draw, render_detail
from ..core.settings import STRUCTURE_CHECK_STRICT
from ..core.spreads import FORMATIONS
from ..core.tarot_data import TAROT_CARDS
from ..render.assets import asset_path_by_id

PLUGIN_NAME = "astrbot_plugin_star_feather"
_CARDS_BY_ID = {c["id"]: c for c in TAROT_CARDS}
_MAX_LIST = 200
_MAX_WIDTH = 640
_THUMB_CACHE_MAX = 96

# 牌阵的适用场景与牌位说明是视图文案：牌位名只从 core/spreads.py 取，
# 这里只补「这个阵拿来问什么」和每个牌位的一句话含义。
class SpreadInfo(TypedDict):
    scenario: str
    positions: dict[str, str]


SPREAD_INFO: dict[str, SpreadInfo] = {
    "羽签": {
        "scenario": "一句话、一件小事、此刻的状态。抽一张，看当下最关键的那点。",
        "positions": {"你的当下": "此刻最需要被看见的状态"},
    },
    "羽时三刻": {
        "scenario": "想看一件事怎么走到现在、接下来往哪去。三张牌按时间线排开。",
        "positions": {
            "过去": "事情的来处",
            "现在": "此刻的落点",
            "未来": "照当前趋势的走向",
        },
    },
    "羽镜": {
        "scenario": "拿不定主意、想看清卡点与下一步。像照镜子一样照出阻力。",
        "positions": {
            "现状": "事情现在的样子",
            "阻碍": "卡住你的那一点",
            "建议": "可以试着挪动的地方",
        },
    },
    "恋羽十字": {
        "scenario": "两个人的关系：各自的状态、关系本身，以及照当前趋势的走向。",
        "positions": {
            "你": "你在这段关系里的状态",
            "对方": "对方此刻的状态",
            "关系现状": "两个人之间的实际温度",
            "未来走向": "照当前趋势的走向",
        },
    },
}


class WebUI:
    """今日牌运浏览 + 牌面预览的只读数据面。"""

    def __init__(self, daily, settings, clock, version: str = ""):
        self.daily = daily
        self.settings = settings
        self.clock = clock
        self.version = version
        self._thumbs: OrderedDict[tuple, dict] = OrderedDict()

    def register(self, context) -> bool:
        """把接口挂到宿主；老版本 AstrBot 没有这个能力时静默跳过。"""
        register = getattr(context, "register_web_api", None)
        if not callable(register):
            return False
        prefix = f"/{PLUGIN_NAME}"
        register(f"{prefix}/overview", self.h_overview, ["GET"], "Star Feather overview")
        register(f"{prefix}/daily/list", self.h_daily_list, ["GET"], "Daily fortune list")
        register(f"{prefix}/daily/detail", self.h_daily_detail, ["GET"], "Daily fortune detail")
        register(f"{prefix}/cards", self.h_cards, ["GET"], "Tarot card list")
        register(f"{prefix}/card-detail", self.h_card_detail, ["GET"], "Tarot card meanings")
        register(f"{prefix}/spreads", self.h_spreads, ["GET"], "Tarot spread list")
        register(f"{prefix}/preview/draw", self.h_preview_draw, ["POST"], "Preview draw")
        register(f"{prefix}/preview/interpret", self.h_preview_interpret, ["POST"],
                 "Preview AI reading")
        register(f"{prefix}/card-image", self.h_card_image, ["GET"], "Tarot card thumbnail")
        return True

    # ---- 数据面 ----

    def _settings_summary(self) -> dict:
        s = self.settings
        return {
            "enable_ai": bool(s.ai.enable_ai),
            "spirit_line": bool(s.ai.spirit_line),
            "structure_check": s.ai.structure_check,
            "persona": s.ai.persona,
            "send_mode": s.output.send_mode,
            "daily_card": bool(s.output.daily_card),
        }

    async def overview(self) -> dict:
        today = self.clock.today()
        # 统计覆盖整个索引（最多 500），列表才截到 _MAX_LIST
        users = await self.daily.users_for(today)
        active = 0
        with_spirit = 0
        stale = 0
        topics = 0
        for uid in users:
            record = await self.daily.read_record(uid)
            if record is None or record.date != today:
                continue
            active += 1
            if record.spirit_line:
                with_spirit += 1
            if self._record_stale(record):
                stale += 1
            topics += len(record.interps)
        return {
            "version": self.version,
            "date": today,
            "users": active,
            "with_spirit": with_spirit,
            "stale": stale,
            "topics": topics,
            "settings": self._settings_summary(),
        }

    async def daily_list(self) -> dict:
        today = self.clock.today()
        # 索引最多 500 位，列表只取最近 _MAX_LIST 位：把索引数量与展示数量一并返回，
        # 前端才能说明「还有记录没显示」，也不会把索引数量误报成当天总人数。
        users = await self.daily.users_for(today)
        shown = users[-_MAX_LIST:]
        items = []
        for uid in shown:
            record = await self.daily.read_record(uid)
            if record is None or record.date != today:
                continue
            card = _CARDS_BY_ID.get(record.card_id, {})
            items.append({
                "uid": uid,
                "card_id": record.card_id,
                "card_cn": card.get("cn", record.card_id),
                "upright": record.upright,
                "spirit": record.spirit_line,
                "topics": len(record.interps),
                "stale": self._record_stale(record),
            })
        items.sort(key=lambda item: item["uid"])
        return {
            "date": today,
            "items": items,
            "indexed_total": len(users),
            "shown": len(shown),
            "truncated": len(users) > _MAX_LIST,
        }

    async def daily_detail(self, uid: str) -> dict | None:
        record = await self.daily.read_record(uid)
        if record is None:
            return None
        card = _CARDS_BY_ID.get(record.card_id, {})
        topics = []
        for topic, value in record.interps.items():
            interp = interpretation_from_record(value)
            topics.append({
                "topic": topic,
                "text": interp.text,
                "sections": list(interp.sections),
                "summary": interp.summary,
            })
        return {
            "uid": uid,
            "date": record.date,
            "today": self.clock.today(),
            "card": {
                "id": record.card_id,
                "cn": card.get("cn", record.card_id),
                "suit": card.get("suit", ""),
                "num": card.get("num", ""),
            },
            "upright": record.upright,
            "spirit": record.spirit_line,
            "sign": record.sign,
            "persona": record.persona,
            "stale": self._record_stale(record),
            "topics": topics,
        }

    def _record_stale(self, record) -> bool:
        """缓存是否已过期：提示词/人设不一致，或 strict 下已有解读不合规。"""
        if (record.prompt_v != self.daily.prompt_version
                or record.persona != self.daily.persona):
            return True
        if self.daily.structure_check != STRUCTURE_CHECK_STRICT:
            return False
        for value in record.interps.values():
            interp = interpretation_from_record(value)
            if not validate_interpret_structure_strict(interp.text, 1)[0]:
                return True
        return False

    def cards(self) -> list[dict]:
        return [
            {"id": c["id"], "cn": c["cn"], "suit": c.get("suit", ""),
             "num": str(c.get("num", ""))}
            for c in TAROT_CARDS
        ]

    def card_detail(self, card_id: str) -> dict:
        """单张牌的完整牌义（图鉴详情用）。"""
        card = _CARDS_BY_ID.get(card_id)
        if card is None:
            raise FileNotFoundError(f"没有这张牌: {card_id!r}")
        return {
            "id": card["id"],
            "cn": card["cn"],
            "en": card.get("en", ""),
            "suit": card.get("suit", ""),
            "num": str(card.get("num", "")),
            "up": card.get("up", {}),
            "down": card.get("down", {}),
        }

    def spreads(self) -> list[dict]:
        """牌阵清单：牌位名只从 FORMATIONS 取，场景说明在这里补。"""
        result = []
        for name, positions in FORMATIONS.items():
            info = SPREAD_INFO.get(name)
            notes = info["positions"] if info else {}
            result.append({
                "name": name,
                "scenario": info["scenario"] if info else "",
                "positions": [{"name": p, "meaning": notes.get(p, "")}
                              for p in positions],
            })
        return result

    # ---- 占卜预览：独立 RNG，不写缓存、不计次数、不走消息投递 ----

    def draw_preview(self, spread: str, question: str = "") -> dict:
        if spread not in FORMATIONS:
            raise ValueError(f"未知牌阵: {spread!r}")
        positions, picks = draw(spread, random.Random())
        return {
            "spread": spread,
            "question": str(question or "").strip(),
            "positions": positions,
            "picks": [self._preview_pick(p) for p in picks],
        }

    @staticmethod
    def _preview_pick(pick) -> dict:
        card = pick["card"]
        upright = bool(pick["upright"])
        side = card.get("up" if upright else "down", {})
        return {
            "id": card["id"],
            "cn": card["cn"],
            "en": card.get("en", ""),
            "suit": card.get("suit", ""),
            "num": str(card.get("num", "")),
            "upright": upright,
            "keywords": list(side.get("keywords", [])),
            "what": side.get("what", ""),
            "advice": side.get("how", "") or side.get("turn", ""),
            "energy": side.get("energy", ""),
        }

    @staticmethod
    def _picks_from_payload(spread: str, raw_picks) -> tuple[list[str], list[dict]]:
        positions = FORMATIONS[spread]
        if not isinstance(raw_picks, list) or len(raw_picks) != len(positions):
            raise ValueError("抽牌结果与牌阵不一致")
        picks = []
        for item in raw_picks:
            card = _CARDS_BY_ID.get(str(item.get("id", ""))) if isinstance(item, dict) else None
            if card is None:
                raise ValueError("牌库里没有这张牌")
            picks.append({"card": card, "upright": bool(item.get("upright", True))})
        return list(positions), picks

    async def interpret_preview(self, spread: str, question, raw_picks) -> dict | None:
        """预览解读：只在用户点击后调用；不读写任何缓存、不发送消息。"""
        if spread not in FORMATIONS:
            raise ValueError(f"未知牌阵: {spread!r}")
        if not self.settings.ai.enable_ai or self.daily.interpreter is None:
            raise RuntimeError("AI 深度解读已关闭，预览可先看本地牌义")
        positions, picks = self._picks_from_payload(spread, raw_picks)
        user_input = str(question or "").strip() or "（预览）"
        interp = await self.daily.interpreter.interpret(
            None, spread, positions, picks, user_input,
            render_detail(positions, picks),
            persona_eff=resolve_persona(self.settings.ai.persona),
            budget=self.daily.interpreter.new_budget())
        if interp is None:
            return None
        return {
            "text": interp.text,
            "sections": list(interp.sections),
            "summary": interp.summary,
        }

    def card_image(self, card_id: str, upright: bool = True, width: int = 180) -> dict:
        """牌面缩略图（WebP base64）；同一尺寸只编码一次。"""
        if card_id not in _CARDS_BY_ID:
            raise FileNotFoundError(f"没有这张牌: {card_id!r}")
        width = max(60, min(int(width or 180), _MAX_WIDTH))
        key = (card_id, bool(upright), width)
        cached = self._thumbs.get(key)
        if cached is not None:
            self._thumbs.move_to_end(key)
            return cached
        with Image.open(asset_path_by_id(card_id)) as src:
            img = src.convert("RGBA")
        if not upright:
            img = img.rotate(180)
        height = max(1, round(img.height * width / img.width))
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=82, method=4)
        payload = {
            "mime": "image/webp",
            "width": img.width,
            "height": img.height,
            # 别叫 data：bridge 会把响应里顶层的 data 当成 {status, data} 包装拆掉
            "base64": base64.b64encode(buf.getvalue()).decode("ascii"),
        }
        self._thumbs[key] = payload
        while len(self._thumbs) > _THUMB_CACHE_MAX:
            self._thumbs.popitem(last=False)
        return payload

    # ---- HTTP 入口（薄壳，参数解析只在这里） ----

    async def h_overview(self):
        return await self.overview()

    async def h_daily_list(self):
        return await self.daily_list()

    async def h_daily_detail(self):
        from astrbot.api.web import request

        uid = str(request.query.get("uid", "") or "").strip()
        if not uid:
            return {"status": "error", "message": "缺少 uid"}, 400
        data = await self.daily_detail(uid)
        if data is None:
            return {"status": "error", "message": "没有这条记录"}, 404
        return data

    async def h_cards(self):
        return {"items": self.cards()}

    async def h_card_detail(self):
        from astrbot.api.web import request

        card_id = str(request.query.get("id", "") or "").strip()
        try:
            return self.card_detail(card_id)
        except FileNotFoundError as e:
            return {"status": "error", "message": str(e)}, 404

    async def h_spreads(self):
        return {"items": self.spreads()}

    async def h_preview_draw(self):
        from astrbot.api.web import request

        payload = await request.json(default={})
        payload = payload if isinstance(payload, dict) else {}
        spread = str(payload.get("spread", "") or "")
        try:
            return self.draw_preview(spread, payload.get("question", ""))
        except ValueError as e:
            return {"status": "error", "message": str(e)}, 400

    async def h_preview_interpret(self):
        from astrbot.api.web import request

        payload = await request.json(default={})
        payload = payload if isinstance(payload, dict) else {}
        spread = str(payload.get("spread", "") or "")
        try:
            result = await self.interpret_preview(
                spread, payload.get("question", ""), payload.get("picks"))
        except RuntimeError as e:
            return {"status": "error", "message": str(e)}, 409
        except ValueError as e:
            return {"status": "error", "message": str(e)}, 400
        if result is None:
            return {"status": "error", "message": "AI 本次没有产出解读，可先看本地牌义"}, 502
        return result

    async def h_card_image(self):
        from astrbot.api.web import request

        card_id = str(request.query.get("id", "") or "").strip()
        upright = str(request.query.get("upright", "1")) != "0"
        try:
            width = int(request.query.get("w", 180))
        except (TypeError, ValueError):
            width = 180
        try:
            return self.card_image(card_id, upright, width)
        except FileNotFoundError as e:
            return {"status": "error", "message": str(e)}, 404


def register_web_apis(context, container, version: str = "") -> bool:
    """装配处调用：把只读视图接口挂到宿主（宿主不支持时返回 False）。"""
    ui = WebUI(container.daily, container.settings, container.clock, version)
    return ui.register(context)
