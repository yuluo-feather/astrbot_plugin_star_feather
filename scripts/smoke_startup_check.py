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

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 目标插件目录名：市场装出来的名字（框架用它推导模块名），不跟着本机目录名走
PLUGIN_DIR_NAME = "astrbot_plugin_star_feather"
# 端口故意错开 6185：本机跑冒烟时，真人在用的那个 AstrBot 正占着默认端口
SMOKE_PORT = "6199"
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


def main() -> int:
    version = _read_declared_version()
    expected = f"{BANNER_MARK}{version}"
    print(f"冒烟对象：{PLUGIN_DIR_NAME}，metadata 声明的版本 {version}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
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
            )

        print(f"拉起真框架（端口 {SMOKE_PORT}），等插件横幅……")
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        try:
            while time.monotonic() < deadline:
                text = _read_log(log_path)
                if expected in text:
                    print(f"冒烟通过：真框架里加载出了插件横幅「{expected}」")
                    return 0

                return_code = proc.poll()
                if return_code is not None:
                    print(
                        f"框架在横幅出现前就退出了（exit code {return_code}），日志尾巴：",
                        file=sys.stderr,
                    )
                    print(_tail(text), file=sys.stderr)
                    return 1

                time.sleep(2)

            print(
                f"冒烟失败：{STARTUP_TIMEOUT_SECONDS} 秒内没等到「{expected}」",
                file=sys.stderr,
            )
            print(_tail(_read_log(log_path)), file=sys.stderr)
            return 1
        finally:
            _stop_process(proc)
    finally:
        shutil.rmtree(smoke_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
