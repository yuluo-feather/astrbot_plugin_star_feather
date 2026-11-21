"""羽忆共享文件契约 v1 的核心模型：不 import astrbot。

字段与失败语义见 `D:/astrbot/workspaces/project_feather_memory/.../docs/contracts_v1.md`；
本文件只保留星羽核心需要产生 / 读取的那部分。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA_LEASE = "star_feather.memory_lease.v1"
SCHEMA_AUTH = "star_feather.memory_auth.v1"
SCHEMA_EVENT = "star_feather.reading_event.v1"
SCHEMA_CONTEXT = "star_feather.memory_context.v1"

EVENT_MAX_BYTES = 16 * 1024
CONTEXT_MAX_BYTES = 8 * 1024
LEASE_MAX_BYTES = 4 * 1024
AUTH_MAX_BYTES = 4 * 1024
CONTEXT_TEXT_MAX_CHARS = 600
CONTEXT_ITEMS_MAX = 5

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid_encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ULID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_event_id() -> str:
    """26 字符 ULID：48-bit 毫秒时间 + 80-bit 随机，按时间字典序可排。"""
    millis = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _ulid_encode(millis, 10) + _ulid_encode(randomness, 16)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReadingEvent:
    event_id: str
    created_at: str
    source_version: str
    platform_id: str
    user_key: str
    conversation_scope: str
    kind: str
    spread: str
    cards: tuple[dict, ...] = field(default_factory=tuple)
    topic_key: str = ""
    spirit_line_present: bool = False

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_EVENT,
            "event_id": self.event_id,
            "created_at": self.created_at,
            "source_version": self.source_version,
            "platform_id": self.platform_id,
            "user_key": self.user_key,
            "conversation_scope": self.conversation_scope,
            "kind": self.kind,
            "spread": self.spread,
            "cards": [{"id": str(c.get("id", "")), "upright": bool(c.get("upright"))}
                      for c in self.cards],
            "topic_key": self.topic_key,
            "spirit_line_present": bool(self.spirit_line_present),
        }


def event_from_reading(reading, incoming, version: str, topic_key: str = "") -> ReadingEvent:
    """从一次成功送达的 Reading 构造事件；调用方保证非日报 / 私聊。"""
    picks = tuple(
        {"id": str((p.get("card") or {}).get("id", "")), "upright": bool(p.get("upright"))}
        for p in reading.picks
    )
    return ReadingEvent(
        event_id=new_event_id(),
        created_at=utc_now_iso(),
        source_version=str(version),
        platform_id=str(incoming.platform_id),
        user_key=str(incoming.user_id),
        conversation_scope=str(incoming.conversation_scope),
        kind=str(reading.request.kind),
        spread=str(reading.formation),
        cards=picks,
        topic_key=topic_key,
        spirit_line_present=bool(reading.spirit),
    )


@dataclass(frozen=True)
class MemoryContext:
    revision: int
    generated_at: str
    expires_at: str
    source: str
    scope: str
    items: tuple[dict, ...] = field(default_factory=tuple)
    text: str = ""

    def is_usable(self, now: datetime) -> bool:
        try:
            fresh = now < parse_ts(self.expires_at)
        except ValueError:
            return False
        return (
            fresh
            and self.scope == "private"
            and bool(self.text.strip())
            and len(self.text) <= CONTEXT_TEXT_MAX_CHARS
            and len(self.items) <= CONTEXT_ITEMS_MAX
        )


def parse_context(payload: object) -> MemoryContext | None:
    """严格按契约读取；任何不符都返回 None，由调用方按无记忆处理。"""
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_CONTEXT:
        return None
    try:
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return None
        generated_at = str(payload.get("generated_at") or "")
        expires_at = str(payload.get("expires_at") or "")
        source = str(payload.get("source") or "")
        scope = str(payload.get("scope") or "")
        text = payload.get("text")
        items_raw = payload.get("items")
        if not isinstance(text, str) or not isinstance(items_raw, list):
            return None
        items = tuple(
            {"kind": str(item.get("kind", "")), "text": str(item.get("text", ""))}
            for item in items_raw if isinstance(item, dict)
        )
        if len(items) != len(items_raw):
            return None
        return MemoryContext(revision, generated_at, expires_at, source, scope, items, text)
    except Exception:
        return None
