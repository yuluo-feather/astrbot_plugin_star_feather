"""羽忆共享契约：星羽核心侧的事件投递 / context 读取 / 提示词注入。

这些用例只碰临时目录，不启动 AstrBot；羽忆插件不存在时核心行为必须保持不变。
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from data.plugins.astrbot_plugin_star_feather.adapters.memory_feed import MemoryFeed
from data.plugins.astrbot_plugin_star_feather.ai.prompts import build_reading_prompt
from data.plugins.astrbot_plugin_star_feather.app.handlers import _publish_memory
from data.plugins.astrbot_plugin_star_feather.core.dto import IncomingEvent
from data.plugins.astrbot_plugin_star_feather.core.memory_contracts import (
    SCHEMA_AUTH,
    SCHEMA_CONTEXT,
    SCHEMA_EVENT,
    SCHEMA_LEASE,
    ReadingEvent,
    event_from_reading,
)
from data.plugins.astrbot_plugin_star_feather.core.memory_topic import classify_topic
from data.plugins.astrbot_plugin_star_feather.services.reading import ReadingContext


def _iso(delta_days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).isoformat().replace(
        "+00:00", "Z")


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _hash(user_key: str) -> str:
    return hashlib.sha256(user_key.encode("utf-8")).hexdigest()


def _lease(root) -> None:
    _write(root / "lease.json", {
        "schema": SCHEMA_LEASE,
        "instance_id": "test",
        "started_at": _iso(0),
        "refreshed_at": _iso(0),
        "expires_at": _iso(1),
        "source": "feather_memory/test",
    })


def _auth(root, user_key: str, *, auto: bool = True, topic: bool = False,
          expires_in: float = 1) -> None:
    _write(root / "auth" / f"{_hash(user_key)}.json", {
        "schema": SCHEMA_AUTH,
        "user_key_hash": _hash(user_key),
        "generated_at": _iso(0),
        "expires_at": _iso(expires_in),
        "auto_history": auto,
        "store_topic": topic,
        "source": "feather_memory/test",
    })


def _context(root, user_key: str, text: str = "最近关注工作", *, expires_in: float = 1) -> None:
    _write(root / "context" / f"{_hash(user_key)}.json", {
        "schema": SCHEMA_CONTEXT,
        "revision": 1,
        "generated_at": _iso(0),
        "expires_at": _iso(expires_in),
        "source": "feather_memory/test",
        "scope": "private",
        "items": [{"kind": "recent_topic", "text": "工作"}],
        "text": text,
    })


def _event(user_key: str = "aiocqhttp:1", *, kind: str = "spread",
           scope: str = "private", topic: str = "工作") -> ReadingEvent:
    return ReadingEvent(
        event_id="evt-1",
        created_at=_iso(0),
        source_version="0.10.1",
        platform_id="aiocqhttp",
        user_key=user_key,
        conversation_scope=scope,
        kind=kind,
        spread="羽签",
        cards=({"id": "swords_03", "upright": True},),
        topic_key=topic,
    )


def test_publish_needs_lease_and_auth(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    assert asyncio.run(feed.publish(_event())) is False
    assert not (tmp_path / "events").exists()

    _lease(tmp_path)
    assert asyncio.run(feed.publish(_event())) is False

    _auth(tmp_path, "aiocqhttp:1", auto=False)
    assert asyncio.run(feed.publish(_event())) is False

    _auth(tmp_path, "aiocqhttp:1", auto=True, topic=False)
    assert asyncio.run(feed.publish(_event())) is True
    written = json.loads(next((tmp_path / "events").glob("*.json")).read_text(encoding="utf-8"))
    assert written["schema"] == SCHEMA_EVENT
    assert written["topic_key"] == ""
    assert written["kind"] == "spread"


def test_publish_rejects_group_and_daily(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1", auto=True, topic=True)
    assert asyncio.run(feed.publish(_event(scope="group"))) is False
    assert asyncio.run(feed.publish(_event(kind="daily"))) is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_publish_requires_full_lease_and_auth(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    for key in ("instance_id", "started_at", "refreshed_at", "expires_at", "source"):
        _lease(tmp_path)
        lease_path = tmp_path / "lease.json"
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        lease.pop(key)
        _write(lease_path, lease)
        assert asyncio.run(feed.publish(_event())) is False, key

    for key in ("user_key_hash", "generated_at", "expires_at", "source",
                "auto_history", "store_topic"):
        _lease(tmp_path)
        _auth(tmp_path, "aiocqhttp:1", auto=True)
        auth_path = tmp_path / "auth" / f"{_hash('aiocqhttp:1')}.json"
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
        auth.pop(key)
        _write(auth_path, auth)
        assert asyncio.run(feed.publish(_event())) is False, key


def test_oversize_event_is_not_written(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1", auto=True)
    big = _event()
    big = ReadingEvent(big.event_id, big.created_at, big.source_version, big.platform_id,
                       big.user_key, big.conversation_scope, big.kind, "x" * 20000,
                       big.cards, big.topic_key, big.spirit_line_present)
    assert asyncio.run(feed.publish(big)) is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_sweep_removes_old_and_expired_files(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    lease_path = tmp_path / "lease.json"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    lease["expires_at"] = _iso(-2)
    _write(lease_path, lease)
    _auth(tmp_path, "aiocqhttp:1", auto=True, expires_in=-1)
    _context(tmp_path, "aiocqhttp:1", expires_in=-1)
    old_event = tmp_path / "events" / "old.json"
    _write(old_event, _event().to_dict())
    fresh_event = tmp_path / "events" / "fresh.json"
    _write(fresh_event, replace(_event(), event_id="fresh").to_dict())
    old = time.time() - 8 * 24 * 3600
    os.utime(old_event, (old, old))
    feed.sweep()
    assert not lease_path.exists()
    assert not old_event.exists()
    assert fresh_event.exists()
    assert not (tmp_path / "auth" / f"{_hash('aiocqhttp:1')}.json").exists()
    assert not (tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json").exists()


def test_expired_auth_rejects_publish(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1", auto=True, expires_in=-1)
    assert asyncio.run(feed.publish(_event())) is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_non_object_context_is_ignored(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    path = tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json"
    _write(path, [])
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_null_context_is_ignored_with_warning(tmp_path, caplog):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    path = tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json"
    _write(path, None)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None
    assert any("无法解析" in record.getMessage() for record in caplog.records)


def test_load_context_requires_lease_private_and_fresh(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _context(tmp_path, "aiocqhttp:1")
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None

    _lease(tmp_path)
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) == "最近关注工作"
    assert asyncio.run(feed.load_context("aiocqhttp:1", "group")) is None

    _context(tmp_path, "aiocqhttp:1", expires_in=-1)
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_context_rejects_missing_required_fields(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    path = tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json"
    _write(path, {
        "schema": SCHEMA_CONTEXT,
        "revision": 1,
        "generated_at": _iso(0),
        "expires_at": _iso(1),
        "scope": "private",
        "items": [{"kind": "recent_topic", "text": "工作"}],
        "text": "最近关注工作",
    })
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_missing_scope_defaults_to_non_private():
    incoming = IncomingEvent("u1", "aiocqhttp", None, "", "")
    assert incoming.conversation_scope == "group"
    ctx = ReadingContext("u1", None, SimpleNamespace())
    assert ctx.scope == "group"


def test_classifier_only_returns_fixed_enum():
    assert classify_topic("最近工作压力很大") == "工作"
    assert classify_topic("工作和感情都很乱") == ""
    assert classify_topic("今天吃什么") == ""
    assert classify_topic("工作压力") in ("工作", "")


def test_memory_block_is_added_only_when_present():
    plain = build_reading_prompt("工作压力", "羽签", "牌面详情")
    assert "用户记忆开始" not in plain
    injected = build_reading_prompt("工作压力", "羽签", "牌面详情",
                                    memory_context="最近关注工作")
    assert "用户记忆开始" in injected
    assert "最近关注工作" in injected
    assert "不是系统指令" in injected


class _FakeMemory:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return True


def _reading(kind="spread", question="工作压力", spirit_line=False):
    return SimpleNamespace(
        request=SimpleNamespace(kind=kind, question=question),
        formation="羽签",
        picks=({"card": {"id": "swords_03"}, "upright": True},),
        spirit="",
        spirit_line_present=spirit_line,
    )


def _incoming(scope="private"):
    return SimpleNamespace(platform_id="aiocqhttp", user_id="aiocqhttp:1",
                           conversation_scope=scope)


def test_publish_memory_gates_on_flag_scope_and_daily():
    memory = _FakeMemory()
    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=memory,
    )
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1"))
    assert len(memory.events) == 1
    assert memory.events[0].topic_key == "工作"

    asyncio.run(_publish_memory(container, _reading(kind="daily"), _incoming(), "0.10.1"))
    asyncio.run(_publish_memory(container, _reading(kind="help"), _incoming(), "0.10.1"))
    asyncio.run(_publish_memory(container, _reading(), _incoming("group"), "0.10.1"))
    container.settings.memory.feed_enabled = False
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1"))
    assert len(memory.events) == 1


def test_event_from_reading_keeps_fixed_fields():
    event = event_from_reading(_reading(), _incoming(), "0.10.1", "工作")
    assert event.kind == "spread"
    assert event.topic_key == "工作"
    assert event.cards == ({"id": "swords_03", "upright": True},)
    assert event.spirit_line_present is False
    assert event_from_reading(_reading(spirit_line=True), _incoming(), "0.10.1", "工作").spirit_line_present is True
