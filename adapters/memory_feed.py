"""羽忆共享目录的宿主适配：租约 / 授权 / 事件 / 上下文。

这一层是星羽核心唯一碰 `data/plugin_data` 的地方；core/ 只拿事件模型和
`MemoryEventPublisher` / `MemoryContextReader` 端口，不知道文件布局。
任何失败都按「无羽忆」处理，绝不影响占卜主流程。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.log import log
from ..core.memory_contracts import (
    AUTH_MAX_BYTES,
    CONTEXT_MAX_BYTES,
    EVENT_MAX_BYTES,
    LEASE_MAX_BYTES,
    SCHEMA_AUTH,
    SCHEMA_LEASE,
    parse_context,
    parse_ts,
)

EVENT_RETENTION_SECONDS = 7 * 24 * 3600
SWEEP_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class PublishOutcome:
    """一次投递的结果：是否落盘，以及是否需要向用户展示首次登记提示。"""

    published: bool = False
    notice_needed: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_hash(user_key: str) -> str:
    return hashlib.sha256(str(user_key).encode("utf-8")).hexdigest()


class MemoryFeed:
    """实现 MemoryEventPublisher + MemoryContextReader 两个端口。"""

    def __init__(self, version: str, root: Path | None = None):
        self.version = str(version)
        self.root = Path(root) if root is not None else self._default_root()
        self._last_sweep = 0.0
        self._warned: set[str] = set()

    @staticmethod
    def _default_root() -> Path:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        return Path(get_astrbot_plugin_data_path()) / "_shared" / "feather_memory" / "v1"

    # ---- 端口实现 -----------------------------------------------------

    async def publish(self, event) -> bool:
        return (await self.publish_event(event)).published

    async def publish_event(self, event, *,
                            enrollment_notified: bool = False) -> PublishOutcome:
        """投递事件；没有授权但羽忆声明默认登记时，返回是否需要先提示用户。"""
        try:
            return await asyncio.to_thread(
                self._publish_sync, event, enrollment_notified)
        except Exception as exc:
            self._warn_once("publish", f"羽忆事件投递失败（不影响占卜）: {exc}")
            return PublishOutcome()

    async def load_context(self, user_key: str, conversation_scope: str) -> str | None:
        if str(conversation_scope) != "private":
            return None
        try:
            return await asyncio.to_thread(self._load_context_sync, user_key)
        except Exception as exc:
            self._warn_once("context", f"羽忆上下文读取失败（按无记忆处理）: {exc}")
            return None

    def sweep(self) -> None:
        try:
            self._sweep_sync()
        except Exception as exc:
            self._warn_once("sweep", f"羽忆共享目录清扫失败: {exc}")

    # ---- 内部同步实现 -------------------------------------------------

    def _publish_sync(self, event, enrollment_notified: bool) -> PublishOutcome:
        lease = self._read_lease()
        if lease is None:
            return PublishOutcome()
        payload = event.to_dict()
        if str(payload.get("conversation_scope")) != "private":
            return PublishOutcome()
        if str(payload.get("kind")) not in ("spread", "single"):
            return PublishOutcome()
        auto_enroll = lease.get("auto_enroll") is True
        user_key = str(event.user_key)
        state, auth = self._auth_state(user_key)
        if state == "invalid":
            # 文件存在但过期/损坏：用户曾经有授权，不能当作新用户重新登记。
            return PublishOutcome()
        if state == "missing":
            if not auto_enroll:
                return PublishOutcome()
            if not enrollment_notified:
                return PublishOutcome(notice_needed=True)
        elif not auth or not auth.get("auto_history"):
            return PublishOutcome()
        elif not auth.get("store_topic"):
            payload["topic_key"] = ""
        path = self.root / "events" / f"{event.event_id}.json"
        self._write_json(path, payload, EVENT_MAX_BYTES)
        # 写入期间授权可能被撤销或被羽忆补建：以落盘后的最新状态为准。
        fresh_state, fresh = self._auth_state(user_key)
        if fresh_state == "valid":
            if not fresh or not fresh.get("auto_history"):
                path.unlink(missing_ok=True)
                return PublishOutcome()
            if not fresh.get("store_topic") and payload.get("topic_key"):
                payload["topic_key"] = ""
                self._write_json(path, payload, EVENT_MAX_BYTES)
        elif fresh_state == "missing":
            # 只有默认登记的首条事件允许没有授权；租约必须仍然允许登记。
            if not (state == "missing" and auto_enroll and self._auto_enroll_now()):
                path.unlink(missing_ok=True)
                return PublishOutcome()
        else:
            path.unlink(missing_ok=True)
            return PublishOutcome()
        self._maybe_sweep()
        return PublishOutcome(published=True)

    def _load_context_sync(self, user_key: str) -> str | None:
        if not self._lease_valid():
            return None
        # 关闭 / 清空可能写墓碑或失效时间：授权缺失、停用、或 context 早于
        # auth.context_valid_after 时，即使文件因清理失败而残留也不得注入。
        auth = self._read_auth(user_key)
        if auth is None:
            return None
        cutoff = auth.get("context_valid_after")
        path = self.root / "context" / f"{_user_hash(user_key)}.json"
        if not path.exists():
            return None
        payload = self._read_json(path, CONTEXT_MAX_BYTES, require_dict=False)
        if payload is None:
            self._warn_once(str(path), f"羽忆 context 无法解析，已忽略: {path.name}")
            return None
        if not isinstance(payload, dict):
            self._warn_once(str(path), f"羽忆 context 不是 JSON 对象，已忽略: {path.name}")
            return None
        context = parse_context(payload)
        if context is None:
            self._warn_once(str(path), f"羽忆 context 字段不合法，已忽略: {path.name}")
            return None
        if not context.is_usable(_now()):
            return None
        if isinstance(cutoff, str) and cutoff.strip():
            try:
                if parse_ts(context.generated_at) <= parse_ts(cutoff):
                    return None
            except (TypeError, ValueError):
                return None
        self._maybe_sweep()
        return context.text

    def _lease_valid(self) -> bool:
        return self._read_lease() is not None

    def _read_lease(self) -> dict | None:
        payload = self._read_json(self.root / "lease.json", LEASE_MAX_BYTES)
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_LEASE:
            return None
        required = ("instance_id", "started_at", "refreshed_at", "expires_at", "source")
        for key in required:
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                return None
        try:
            parse_ts(str(payload["started_at"]))
            parse_ts(str(payload["refreshed_at"]))
            return payload if _now() < parse_ts(str(payload["expires_at"])) else None
        except (TypeError, ValueError):
            return None

    def _auto_enroll_now(self) -> bool:
        lease = self._read_lease()
        return bool(lease and lease.get("auto_enroll") is True)

    def _read_auth(self, user_key: str) -> dict | None:
        state, payload = self._auth_state(user_key)
        return payload if state == "valid" else None

    def _auth_state(self, user_key: str) -> tuple[str, dict | None]:
        """区分「没有文件」与「文件在但不可用」：只有前者允许自动登记。"""
        path = self.root / "auth" / f"{_user_hash(user_key)}.json"
        if not path.exists():
            return "missing", None
        payload = self._read_json(path, AUTH_MAX_BYTES)
        if payload is None and not path.exists():
            # exists() 与 read 之间被删除：按「没有文件」处理，下一轮可重新提示。
            return "missing", None
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_AUTH:
            return "invalid", None
        hash_value = payload.get("user_key_hash")
        if not isinstance(hash_value, str) or not hash_value.strip():
            return "invalid", None
        if hash_value != _user_hash(user_key):
            return "invalid", None
        if not isinstance(payload.get("auto_history"), bool):
            return "invalid", None
        if not isinstance(payload.get("store_topic"), bool):
            return "invalid", None
        for key in ("generated_at", "expires_at", "source"):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                return "invalid", None
        cutoff = payload.get("context_valid_after")
        if cutoff is not None and not isinstance(cutoff, str):
            return "invalid", None
        if isinstance(cutoff, str) and cutoff.strip():
            try:
                parse_ts(cutoff)
            except (TypeError, ValueError):
                return "invalid", None
        try:
            parse_ts(str(payload["generated_at"]))
            if _now() >= parse_ts(str(payload["expires_at"])):
                return "invalid", None
        except (TypeError, ValueError):
            return "invalid", None
        return "valid", payload

    def _read_json(self, path: Path, max_bytes: int, *,
                   require_dict: bool = True) -> object | None:
        try:
            if path.stat().st_size > max_bytes:
                self._warn_once(str(path), f"羽忆文件超限，已忽略: {path.name}")
                return None
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._warn_once(str(path), f"羽忆文件读取失败，已忽略: {path.name} ({exc})")
            return None
        if require_dict and not isinstance(payload, dict):
            return None
        return payload

    def _write_json(self, path: Path, payload: dict, max_bytes: int) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(data) > max_bytes:
            raise ValueError(f"{path.name}: payload {len(data)} bytes exceeds {max_bytes}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def _maybe_sweep(self) -> None:
        now = time.monotonic()
        if now - self._last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        self.sweep()

    def _sweep_sync(self) -> None:
        now = time.time()
        for path in (self.root / "events").glob("*.json"):
            try:
                if now - path.stat().st_mtime > EVENT_RETENTION_SECONDS:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        log.warning(message)
