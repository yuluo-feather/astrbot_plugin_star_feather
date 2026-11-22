"""羽忆共享契约：星羽核心侧的事件投递 / context 读取 / 提示词注入。

这些用例只碰临时目录，不启动 AstrBot；羽忆插件不存在时核心行为必须保持不变。
"""
import asyncio
import gc
import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from data.plugins.astrbot_plugin_star_feather.adapters.memory_feed import (
    MemoryFeed,
    PublishOutcome,
)
from data.plugins.astrbot_plugin_star_feather.ai.prompts import build_reading_prompt
from data.plugins.astrbot_plugin_star_feather.app import handlers
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
from data.plugins.astrbot_plugin_star_feather.core.texts import MEMORY_ENROLL_NOTICE
from data.plugins.astrbot_plugin_star_feather.services.reading import ReadingContext


def _iso(delta_days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).isoformat().replace(
        "+00:00", "Z")


@pytest.fixture(autouse=True)
def _reset_notice_state():
    handlers._notice_sent.clear()
    handlers._notice_locks.clear()
    yield
    handlers._notice_sent.clear()
    handlers._notice_locks.clear()


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _hash(user_key: str) -> str:
    return hashlib.sha256(user_key.encode("utf-8")).hexdigest()


def _lease(root, *, auto_enroll: bool | None = None) -> None:
    payload = {
        "schema": SCHEMA_LEASE,
        "instance_id": "test",
        "started_at": _iso(0),
        "refreshed_at": _iso(0),
        "expires_at": _iso(1),
        "source": "feather_memory/test",
    }
    if auto_enroll is not None:
        payload["auto_enroll"] = auto_enroll
    _write(root / "lease.json", payload)


def _auth(root, user_key: str, *, auto: bool = True, topic: bool = False,
          expires_in: float = 1, context_valid_after: str | None = None) -> None:
    payload = {
        "schema": SCHEMA_AUTH,
        "user_key_hash": _hash(user_key),
        "generated_at": _iso(0),
        "expires_at": _iso(expires_in),
        "auto_history": auto,
        "store_topic": topic,
        "source": "feather_memory/test",
    }
    if context_valid_after is not None:
        payload["context_valid_after"] = context_valid_after
    _write(root / "auth" / f"{_hash(user_key)}.json", payload)


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


def test_auto_enroll_notice_comes_before_publish(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path, auto_enroll=True)

    notice = asyncio.run(feed.publish_event(_event(), enrollment_notified=False))
    assert notice.published is False
    assert notice.notice_needed is True
    assert not list((tmp_path / "events").glob("*.json"))

    first = asyncio.run(feed.publish_event(_event(), enrollment_notified=True))
    assert first.published is True
    assert first.notice_needed is False
    written = json.loads(next((tmp_path / "events").glob("*.json")).read_text(encoding="utf-8"))
    assert written["topic_key"] == "工作"


def test_auto_enroll_absent_keeps_old_gate(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    outcome = asyncio.run(feed.publish_event(_event(), enrollment_notified=True))
    assert outcome.published is False
    assert outcome.notice_needed is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_invalid_or_expired_auth_never_triggers_auto_enroll(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path, auto_enroll=True)
    _auth(tmp_path, "aiocqhttp:1", auto=True, expires_in=-1)
    expired = asyncio.run(feed.publish_event(_event(), enrollment_notified=False))
    assert expired.published is False and expired.notice_needed is False

    path = tmp_path / "auth" / f"{_hash('aiocqhttp:1')}.json"
    path.write_text("{not json", encoding="utf-8")
    broken = asyncio.run(feed.publish_event(_event(), enrollment_notified=True))
    assert broken.published is False and broken.notice_needed is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_opted_out_auth_blocks_auto_enroll(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path, auto_enroll=True)
    _auth(tmp_path, "aiocqhttp:1", auto=False)
    outcome = asyncio.run(feed.publish_event(_event(), enrollment_notified=True))
    assert outcome.published is False
    assert outcome.notice_needed is False


def test_post_write_revocation_removes_event(tmp_path, monkeypatch):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path, auto_enroll=True)
    _auth(tmp_path, "aiocqhttp:1", auto=True, topic=True)
    real = feed._auth_state
    calls = {"n": 0}

    def fake(user_key):
        calls["n"] += 1
        return real(user_key) if calls["n"] == 1 else ("missing", None)

    monkeypatch.setattr(feed, "_auth_state", fake)
    outcome = asyncio.run(feed.publish_event(_event(), enrollment_notified=True))
    assert outcome.published is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_auto_enroll_flip_removes_bootstrap_event(tmp_path, monkeypatch):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path, auto_enroll=True)
    monkeypatch.setattr(feed, "_auto_enroll_now", lambda: False)
    outcome = asyncio.run(feed.publish_event(_event(), enrollment_notified=True))
    assert outcome.published is False
    assert not list((tmp_path / "events").glob("*.json"))


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


def test_sweep_removes_only_old_events(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1", auto=True, expires_in=-1)
    _context(tmp_path, "aiocqhttp:1", expires_in=-1)
    old_event = tmp_path / "events" / "old.json"
    _write(old_event, _event().to_dict())
    fresh_event = tmp_path / "events" / "fresh.json"
    _write(fresh_event, replace(_event(), event_id="fresh").to_dict())
    old = time.time() - 8 * 24 * 3600
    os.utime(old_event, (old, old))
    feed.sweep()
    assert not old_event.exists()
    assert fresh_event.exists()
    # auth / context / lease 由羽忆负责清扫，星羽只处理自己写的事件。
    assert (tmp_path / "lease.json").exists()
    assert (tmp_path / "auth" / f"{_hash('aiocqhttp:1')}.json").exists()
    assert (tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json").exists()


def test_load_context_requires_valid_auth(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _context(tmp_path, "aiocqhttp:1")
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None

    _auth(tmp_path, "aiocqhttp:1", auto=True)
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) == "最近关注工作"

    _auth(tmp_path, "aiocqhttp:1", auto=True, expires_in=-1)
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_expired_auth_rejects_publish(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1", auto=True, expires_in=-1)
    assert asyncio.run(feed.publish(_event())) is False
    assert not list((tmp_path / "events").glob("*.json"))


def test_non_object_context_is_ignored(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1")
    path = tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json"
    _write(path, [])
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_null_context_is_ignored_with_warning(tmp_path, caplog):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1")
    path = tmp_path / "context" / f"{_hash('aiocqhttp:1')}.json"
    _write(path, None)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None
    assert any("无法解析" in record.getMessage() for record in caplog.records)


def test_context_hidden_after_context_valid_after(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _context(tmp_path, "aiocqhttp:1")

    # 墓碑：停用 + 失效时间，即使 context 残留也不注入
    _auth(tmp_path, "aiocqhttp:1", auto=False, topic=False,
          context_valid_after=_iso(0))
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None

    # 清空但仍在记录：同样由失效时间挡住旧 context
    _auth(tmp_path, "aiocqhttp:1", auto=True, topic=True,
          context_valid_after=_iso(0))
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None

    # 失效时间早于 context 生成时间：继续注入
    _auth(tmp_path, "aiocqhttp:1", auto=True, topic=True,
          context_valid_after=_iso(-1))
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) == "最近关注工作"


def test_context_still_injected_when_auto_history_off(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _context(tmp_path, "aiocqhttp:1")
    _auth(tmp_path, "aiocqhttp:1", auto=False, topic=True)
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) == "最近关注工作"


def test_unparseable_context_cutoff_invalidates_auth(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _context(tmp_path, "aiocqhttp:1")
    _auth(tmp_path, "aiocqhttp:1", context_valid_after="not-a-time")
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_load_context_requires_lease_private_and_fresh(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _context(tmp_path, "aiocqhttp:1")
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None

    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1")
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) == "最近关注工作"
    assert asyncio.run(feed.load_context("aiocqhttp:1", "group")) is None

    _context(tmp_path, "aiocqhttp:1", expires_in=-1)
    assert asyncio.run(feed.load_context("aiocqhttp:1", "private")) is None


def test_context_rejects_missing_required_fields(tmp_path):
    feed = MemoryFeed("0.10.1", root=tmp_path)
    _lease(tmp_path)
    _auth(tmp_path, "aiocqhttp:1")
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


class _FakeKV:
    def __init__(self):
        self.data = {}

    async def get_kv_data(self, key, default=None):
        return self.data.get(key, default)

    async def put_kv_data(self, key, value):
        self.data[key] = value


class _NoticeMemory:
    def __init__(self):
        self.calls = []

    async def publish(self, event):
        return True

    async def publish_event(self, event, *, enrollment_notified=False):
        self.calls.append(enrollment_notified)
        return PublishOutcome(published=enrollment_notified,
                              notice_needed=not enrollment_notified)


class _SlowNoticeMemory(_NoticeMemory):
    async def publish_event(self, event, *, enrollment_notified=False):
        self.calls.append(enrollment_notified)
        await asyncio.sleep(0.02)
        return PublishOutcome(published=enrollment_notified,
                              notice_needed=not enrollment_notified)


class _BrokenKV:
    async def get_kv_data(self, key, default=None):
        raise RuntimeError("kv down")

    async def put_kv_data(self, key, value):
        raise RuntimeError("kv down")


class _Messenger:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)
        return self.ok


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


def test_notice_is_sent_once_and_marked_after_success():
    memory = _NoticeMemory()
    kv = _FakeKV()
    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=memory,
        kv=kv,
    )
    messenger = _Messenger(ok=True)
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1", messenger))
    assert messenger.sent == [MEMORY_ENROLL_NOTICE]
    assert kv.data["sf_memory_notice:aiocqhttp:1"] is True
    assert memory.calls == [False]

    again = _Messenger(ok=True)
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1", again))
    assert again.sent == []
    assert memory.calls == [False, True]


def test_notice_send_failure_does_not_consume_the_marker():
    memory = _NoticeMemory()
    kv = _FakeKV()
    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=memory,
        kv=kv,
    )
    messenger = _Messenger(ok=False)
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1", messenger))
    assert messenger.sent == [MEMORY_ENROLL_NOTICE]
    assert kv.data == {}
    assert memory.calls == [False]


def test_notice_never_sent_in_group_or_daily():
    memory = _NoticeMemory()
    kv = _FakeKV()
    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=memory,
        kv=kv,
    )
    messenger = _Messenger()
    asyncio.run(_publish_memory(container, _reading(), _incoming("group"), "0.10.1", messenger))
    asyncio.run(_publish_memory(container, _reading(kind="daily"), _incoming(), "0.10.1", messenger))
    assert messenger.sent == []
    assert memory.calls == []


def test_concurrent_notice_is_sent_once():
    memory = _SlowNoticeMemory()
    kv = _FakeKV()
    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=memory,
        kv=kv,
    )
    messenger = _Messenger()

    async def run():
        await asyncio.gather(
            _publish_memory(container, _reading(), _incoming(), "0.10.1", messenger),
            _publish_memory(container, _reading(), _incoming(), "0.10.1", messenger),
        )

    asyncio.run(run())
    assert messenger.sent == [MEMORY_ENROLL_NOTICE]
    assert memory.calls == [False, True]


def test_kv_failure_falls_back_to_process_marker():
    memory = _NoticeMemory()
    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=memory,
        kv=_BrokenKV(),
    )
    messenger = _Messenger()
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1", messenger))
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1", messenger))
    assert messenger.sent == [MEMORY_ENROLL_NOTICE]
    assert memory.calls == [False, True]


def test_event_from_reading_keeps_fixed_fields():
    event = event_from_reading(_reading(), _incoming(), "0.10.1", "工作")
    assert event.kind == "spread"
    assert event.topic_key == "工作"
    assert event.cards == ({"id": "swords_03", "upright": True},)
    assert event.spirit_line_present is False
    assert event_from_reading(_reading(spirit_line=True), _incoming(), "0.10.1", "工作").spirit_line_present is True


def test_notice_sent_cache_is_bounded():
    assert handlers._NOTICE_SENT_MAX == 4096
    for index in range(handlers._NOTICE_SENT_MAX + 5):
        handlers._remember_notice(f"user-{index}")
    assert len(handlers._notice_sent) == handlers._NOTICE_SENT_MAX
    assert "user-0" not in handlers._notice_sent
    assert f"user-{handlers._NOTICE_SENT_MAX + 4}" in handlers._notice_sent


def test_notice_sent_cache_keeps_recent_hits():
    for index in range(handlers._NOTICE_SENT_MAX):
        handlers._remember_notice(f"user-{index}")
    handlers._remember_notice("user-0")  # 命中也要移动到队尾
    handlers._remember_notice("new-user")
    assert "user-0" in handlers._notice_sent
    assert "user-1" not in handlers._notice_sent


def test_notice_cache_hit_in_publish_moves_user_to_the_end():
    """_publish_memory 的命中分支也要 move；否则活跃用户会被新用户挤出。"""
    handlers._remember_notice("aiocqhttp:1")  # 放成最旧
    for index in range(handlers._NOTICE_SENT_MAX - 1):
        handlers._remember_notice(f"user-{index}")
    assert len(handlers._notice_sent) == handlers._NOTICE_SENT_MAX

    container = SimpleNamespace(
        settings=SimpleNamespace(memory=SimpleNamespace(feed_enabled=True)),
        memory=_NoticeMemory(),
        kv=_BrokenKV(),
    )
    asyncio.run(_publish_memory(container, _reading(), _incoming(), "0.10.1"))

    handlers._remember_notice("new-user")  # 未移动的话这里会把最旧的 aiocqhttp:1 挤掉
    assert "aiocqhttp:1" in handlers._notice_sent
    assert len(handlers._notice_sent) == handlers._NOTICE_SENT_MAX


def test_notice_lock_is_released_after_use():
    async def use():
        first = handlers._notice_locks.setdefault("user-1", asyncio.Lock())
        second = handlers._notice_locks.setdefault("user-1", asyncio.Lock())
        assert first is second, "同一用户并发时必须拿到同一把锁"
        async with first:
            pass

    asyncio.run(use())
    gc.collect()
    assert "user-1" not in handlers._notice_locks
