"""真框架冒烟：把插件装进一个真的 AstrBot，看它到底加载不加载得起来。

这一格买的是单测买不到的东西——单测跑在 `tests/conftest.py` 的框架桩上，
桩只保证「我们以为的框架面」叫得通；下面这些事桩测全绿也抓不出来：

  - `metadata.yaml` 被框架解析失败（字段名/版本号写法不合规）；
  - 插件目录命名不合规，框架扫不到；
  - `main.py` import 了没写进 `requirements.txt` 的东西（用户装上就 ImportError）；
  - `@register` 的名字与别的插件（或内置插件）撞车；
  - 上游把 `initialize` 这个钩子名改掉——我们的启动逻辑整段静默失效。

**断言只认一件事**：插件自己的启动横幅（`StarFeatherPlugin.initialize` 里那句）
出现在真框架的日志里。它同时证明了一整条链：目录被扫到 → metadata 解析成功 →
模块 import 成功 → `@register` 生效 → 类被实例化 → `initialize` 钩子被调用。
横幅里的版本号现采自 `metadata.yaml`，不写死（写死等于把版本漂移当成绿）。

为什么不是照抄上游的 `smoke_test.yml`：真框架 4.28.1 的 wheel 里**没有 `main.py`**
（那是源码部署的入口），官方脚本那套 `python main.py --webui-dir …` 在这里必死；
wheel 的 CLI 是 `astrbot init` / `astrbot run`。另一条实测：wheel 自带
`astrbot/dashboard/dist`，`check_dashboard` 看见 bundled 资产就直接返回、不碰网络，
所以这一格只依赖 PyPI 装包，不依赖任何外部内容源。

装进临时目录的那份**只收 git 已跟踪的文件**——也就是市场真的发给用户的那份，
本机开发目录里的高清 PNG 之类不该混进冒烟对象。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Windows CI 控制台默认是 cp1252：本脚本大量 print 中文，不显式切 UTF-8 会直接
# UnicodeEncodeError（2026-11-03 手动冒烟第一次在 GitHub 上跑就栽在这）。
# 子进程已经由 env["PYTHONUTF8"] 统一成 UTF-8，父进程这边也要对齐。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 目标插件目录名：市场装出来的名字（框架用它推导模块名），不跟着本机目录名走
PLUGIN_DIR_NAME = "astrbot_plugin_star_feather"
# 端口故意错开 6185：本机跑冒烟时，真人在用的那个 AstrBot 正占着默认端口
SMOKE_PORT = "6199"
DASHBOARD_USER = "astrbot"
# dashboard 初始密码：init 认这个环境变量并写进临时配置，后面登录靠它，
# 不去解析配置文件里的哈希。
SMOKE_PASSWORD = "StarFeather-Smoke-1"
DASHBOARD_BASE = f"http://127.0.0.1:{SMOKE_PORT}"
# 真框架冷启动要建配置、扫插件、拉 t2i 模板，给足时间；红了靠日志尾巴定位
STARTUP_TIMEOUT_SECONDS = 180
BANNER_MARK = "星羽塔罗 v"
_VERSION_RE = re.compile(r"^version:\s*(\S+)\s*$", re.MULTILINE)

_COPY_IGNORE = (".git", "__pycache__", ".pytest_cache", ".ruff_cache")


def _read_declared_version() -> str:
    """现采 metadata.yaml 里的版本号——横幅断言靠它，不写死。"""
    text = (REPO_ROOT / "metadata.yaml").read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise SystemExit("metadata.yaml 里没找到 version:，先去看文件是不是被改坏了")
    return match.group(1).strip().strip("\"'")


def _copy_plugin(dest: Path) -> int:
    """把插件本体复制进临时根：优先按 git 已跟踪清单（= 用户装到的那份）。"""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if tracked.returncode == 0 and tracked.stdout.strip():
        names = [n for n in tracked.stdout.decode("utf-8").split("\0") if n]
        for rel in names:
            src = REPO_ROOT / rel
            if not src.is_file():
                continue
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return len(names)

    # 没有 git 时的退路：整目录抄一份，只躲开版本库与缓存
    shutil.copytree(REPO_ROOT, dest, ignore=shutil.ignore_patterns(*_COPY_IGNORE))
    return sum(1 for p in dest.rglob("*") if p.is_file())


def _run_cli(smoke_root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "astrbot.cli", *args],
        cwd=smoke_root,
        env=env,
        capture_output=True,
        check=False,
    )


def _read_log(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读不到冒烟日志: {exc}"


def _tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


def _stop_gracefully(proc: subprocess.Popen[bytes]) -> tuple[int, bool]:
    """尽量走核心的优雅收尾，返回 (退出码, 是否在超时内退出)。

    POSIX 发 SIGINT、Windows 发 CTRL_BREAK_EVENT（子进程需建在独立进程组里），
    Python 都会把它转成 KeyboardInterrupt，CLI 的取消路径随即调 core_lifecycle.stop()。
    超时被强杀时返回 False，调用点必须按失败处理。
    """
    if os.name == "nt":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)
    try:
        return proc.wait(timeout=90), True
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(timeout=15), False


def _http_opener() -> urllib.request.OpenerDirector:
    """带 cookie 的 HTTP 客户端：登录签发的 JWT 存在 cookie 里，后续请求自动带上。"""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _post_json(opener, path: str, payload: dict, timeout: float = 30) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{DASHBOARD_BASE}{path}", data=body,
        headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _wait_dashboard_ready(opener, deadline: float) -> bool:
    """横幅出现不等于 HTTP 就绪：轮询公开的 setup-status 直到连得上。"""
    while time.monotonic() < deadline:
        try:
            with opener.open(f"{DASHBOARD_BASE}/api/auth/setup-status", timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    return False


def _login(opener) -> str | None:
    """登录并返回 JWT。cookie 带 Secure 标记，HTTP 下不会回传，所以显式用 Bearer。"""
    try:
        status, text = _post_json(
            opener, "/api/auth/login",
            {"username": DASHBOARD_USER, "password": SMOKE_PASSWORD})
    except (urllib.error.URLError, OSError) as exc:
        print(f"登录请求失败：{exc}", file=sys.stderr)
        return None
    if status != 200:
        print(f"登录失败（HTTP {status}）：{text[:300]}", file=sys.stderr)
        return None
    try:
        token = (json.loads(text).get("data") or {}).get("token")
    except (ValueError, AttributeError):
        token = None
    if not isinstance(token, str) or not token:
        print(f"登录失败：响应里没有 token：{text[:300]}", file=sys.stderr)
        return None
    return token


def _run_command(opener, token: str) -> str:
    """经 WebChat 通道发一条 /占卜，读完整条 SSE 返回。"""
    payload = {"message": "/占卜 测试", "session_id": "smoke-session"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{DASHBOARD_BASE}/api/chat/send", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    with opener.open(req, timeout=120) as resp:
        return resp.read().decode("utf-8", "replace")


def _has_plugin_result(sse: str) -> bool:
    """SSE 里是否出现了插件发出的牌面图。

    没有 AI Provider 时插件走图片模式：只发「洗牌提示 + 牌面图」，不带文字解读，
    所以不能用免责声明当标记——牌面图本身就是「命令被执行且结果已发送」的证据。
    """
    for line in sse.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[len("data: "):])
        except ValueError:
            continue
        if event.get("type") == "image" and str(event.get("data", "")).startswith("[IMAGE]"):
            return True
    return False


def main() -> int:
    version = _read_declared_version()
    expected = f"{BANNER_MARK}{version}"
    print(f"冒烟对象：{PLUGIN_DIR_NAME}，metadata 声明的版本 {version}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    # init 认这个环境变量生成 dashboard 初始密码；后面登录与命令往返都靠它
    env["ASTRBOT_DASHBOARD_INITIAL_PASSWORD"] = SMOKE_PASSWORD
    # Windows 上 httpx 只认 NO_PROXY 环境变量：不设的话连 127.0.0.1 也走系统代理
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)

    smoke_root = Path(tempfile.mkdtemp(prefix="astrbot-smoke-root-"))
    log_path = smoke_root / "smoke.log"
    plugin_dest = smoke_root / "data" / "plugins" / PLUGIN_DIR_NAME

    try:
        init_proc = _run_cli(smoke_root, env, "init", "-y")
        if init_proc.returncode != 0:
            print("astrbot init 就没过，日志尾巴：", file=sys.stderr)
            print(init_proc.stdout.decode("utf-8", "replace"), file=sys.stderr)
            print(init_proc.stderr.decode("utf-8", "replace"), file=sys.stderr)
            return 1

        plugin_dest.parent.mkdir(parents=True, exist_ok=True)
        copied = _copy_plugin(plugin_dest)
        print(f"已装进临时根：{plugin_dest}（{copied} 个文件）")

        manifest_path = plugin_dest / "assets" / "manifest.json"
        if not manifest_path.is_file():
            print("冒烟失败：包内缺少 assets/manifest.json，牌面渲染会全部降级为文字",
                  file=sys.stderr)
            return 1
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"冒烟失败：assets/manifest.json 读不出来：{exc}", file=sys.stderr)
            return 1
        if not isinstance(manifest, dict) or len(manifest) != 78:
            print(f"冒烟失败：素材清单条目数不是 78（实际 {len(manifest) if isinstance(manifest, dict) else '非字典'}）",
                  file=sys.stderr)
            return 1
        missing = [rel for rel in manifest.values()
                   if not isinstance(rel, str) or not (plugin_dest / "assets" / rel).is_file()]
        if missing:
            print(f"冒烟失败：素材清单指向了不存在的文件：{missing[:5]}", file=sys.stderr)
            return 1

        with log_path.open("wb") as log_file:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "astrbot.cli",
                    "run",
                    "--port",
                    SMOKE_PORT,
                ],
                cwd=smoke_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                # Windows 上要独立进程组才能收 CTRL_BREAK_EVENT；POSIX 取不到该常量给 0
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )

        print(f"拉起真框架（端口 {SMOKE_PORT}），等插件横幅……")
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        try:
            while True:
                text = _read_log(log_path)
                if expected in text:
                    print(f"启动通过：真框架里加载出了插件横幅「{expected}」")
                    break

                return_code = proc.poll()
                if return_code is not None:
                    print(
                        f"框架在横幅出现前就退出了（exit code {return_code}），日志尾巴：",
                        file=sys.stderr,
                    )
                    print(_tail(text), file=sys.stderr)
                    return 1

                if time.monotonic() >= deadline:
                    print(
                        f"冒烟失败：{STARTUP_TIMEOUT_SECONDS} 秒内没等到「{expected}」",
                        file=sys.stderr,
                    )
                    print(_tail(_read_log(log_path)), file=sys.stderr)
                    return 1

                time.sleep(2)

            opener = _http_opener()
            if not _wait_dashboard_ready(opener, time.monotonic() + 60):
                print("冒烟失败：横幅出现后 60 秒内 dashboard 没就绪", file=sys.stderr)
                print(_tail(_read_log(log_path)), file=sys.stderr)
                return 1
            token = _login(opener)
            if not token:
                print(_tail(_read_log(log_path)), file=sys.stderr)
                return 1

            print("登录通过，经 WebChat 通道发一条 /占卜 测试……")
            sse = _run_command(opener, token)
            if not _has_plugin_result(sse):
                print(
                    "冒烟失败：命令往返没有收到插件产出的牌面图",
                    file=sys.stderr,
                )
                print(_tail(sse, 30), file=sys.stderr)
                print(_tail(_read_log(log_path)), file=sys.stderr)
                return 1
            print("命令往返通过：/占卜 的结果已由 WebChat 通道返回（牌面图）")

            exit_code, exited = _stop_gracefully(proc)
            if not exited:
                print("冒烟失败：停止信号发出后 90 秒内没有退出，被强制结束",
                      file=sys.stderr)
                print(_tail(_read_log(log_path)), file=sys.stderr)
                return 1
            if exit_code not in (0, 130):
                print(f"冒烟失败：优雅退出返回 {exit_code}", file=sys.stderr)
                print(_tail(_read_log(log_path)), file=sys.stderr)
                return 1
            if "AstrBot has been shut down" not in _read_log(log_path):
                print("冒烟失败：退出码正常，但没看到 CLI 的收尾日志", file=sys.stderr)
                print(_tail(_read_log(log_path)), file=sys.stderr)
                return 1
            print("优雅退出通过：核心收到停止信号并完成收尾")
            return 0
        finally:
            _stop_process(proc)
    finally:
        shutil.rmtree(smoke_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
