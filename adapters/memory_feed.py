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
from datetime import datetime, timedelta, timezone
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
LEASE_STALE_SECONDS = 24 * 3600
SWEEP_INTERVAL_SECONDS = 3600


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
        try:
            return await asyncio.to_thread(self._publish_sync, event)
        except Exception as exc:
            self._warn_once("publish", f"羽忆事件投递失败（不影响占卜）: {exc}")
            return False

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

    def _publish_sync(self, event) -> bool:
        if not self._lease_valid():
            return False
        payload = event.to_dict()
        if str(payload.get("conversation_scope")) != "private":
            return False
        if str(payload.get("kind")) not in ("spread", "single"):
            return False
        auth = self._read_auth(str(event.user_key))
        if auth is None or not auth.get("auto_history"):
            return False
        if not auth.get("store_topic"):
            payload["topic_key"] = ""
        path = self.root / "events" / f"{event.event_id}.json"
        self._write_json(path, payload, EVENT_MAX_BYTES)
        # 写入期间用户可能撤销授权：以落盘后的最新授权为准，必要时撤回事件。
        fresh = self._read_auth(str(event.user_key))
        if fresh is None or not fresh.get("auto_history"):
            path.unlink(missing_ok=True)
            return False
        if not fresh.get("store_topic") and payload.get("topic_key"):
            payload["topic_key"] = ""
            self._write_json(path, payload, EVENT_MAX_BYTES)
        self._maybe_sweep()
        return True

    def _load_context_sync(self, user_key: str) -> str | None:
        if not self._lease_valid():
            return None
        path = self.root / "context" / f"{_user_hash(user_key)}.json"
        payload = self._read_json(path, CONTEXT_MAX_BYTES, require_dict=False)
        if payload is None:
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
        self._maybe_sweep()
        return context.text

    def _lease_valid(self) -> bool:
        payload = self._read_json(self.root / "lease.json", LEASE_MAX_BYTES)
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_LEASE:
            return False
        required = ("instance_id", "started_at", "refreshed_at", "expires_at", "source")
        for key in required:
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                return False
        try:
            parse_ts(str(payload["started_at"]))
            parse_ts(str(payload["refreshed_at"]))
            return _now() < parse_ts(str(payload["expires_at"]))
        except (TypeError, ValueError):
            return False

    def _read_auth(self, user_key: str) -> dict | None:
        path = self.root / "auth" / f"{_user_hash(user_key)}.json"
        payload = self._read_json(path, AUTH_MAX_BYTES)
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_AUTH:
            return None
        hash_value = payload.get("user_key_hash")
        if not isinstance(hash_value, str) or not hash_value.strip():
            return None
        if hash_value != _user_hash(user_key):
            return None
        if not isinstance(payload.get("auto_history"), bool):
            return None
        if not isinstance(payload.get("store_topic"), bool):
            return None
        for key in ("generated_at", "expires_at", "source"):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                return None
        try:
            parse_ts(str(payload["generated_at"]))
            if _now() >= parse_ts(str(payload["expires_at"])):
                return None
        except (TypeError, ValueError):
            return None
        return payload

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
        for sub in ("auth", "context"):
            for path in (self.root / sub).glob("*.json"):
                try:
                    before = path.stat()
                except OSError:
                    continue
                payload = self._read_json(path, AUTH_MAX_BYTES if sub == "auth" else CONTEXT_MAX_BYTES)
                if not isinstance(payload, dict):
                    continue
                try:
                    expired = _now() >= parse_ts(str(payload.get("expires_at") or ""))
                except (TypeError, ValueError):
                    continue
                if not expired:
                    continue
                try:
                    after = path.stat()
                except OSError:
                    continue
                if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                    continue  # 读取后被羽忆替换：不删新文件
                path.unlink(missing_ok=True)
        lease = self.root / "lease.json"
        try:
            before = lease.stat()
            payload = json.loads(lease.read_text(encoding="utf-8"))
            expires = parse_ts(str(payload.get("expires_at") or ""))
            after = lease.stat()
            if ((before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
                    and _now() >= expires + timedelta(days=1)):
                lease.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, json.JSONDecodeError):
            try:
                if now - lease.stat().st_mtime > LEASE_STALE_SECONDS:
                    lease.unlink(missing_ok=True)
            except OSError:
                pass

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        log.warning(message)
