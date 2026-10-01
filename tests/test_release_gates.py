"""发布红线：仓库侧能机械判定的三条（本地 pytest 与 GitHub CI 共用一把尺子）。

为什么在这里而不写进 workflow YAML：CI 只跑 pytest；YAML 里另抄一套正则与阈值就是
第二处口径，早晚与发布体检（preflight）漂移。放进测试，本地收工顺带跑，问题在推送
之前现形，不必等 CI 变红再回头查。

三条红线各自的来历：

- **版本六项一致**：main.VERSION / metadata.version / README 中英各徽章的 src 与
  alt，正则与 preflight.check_versions 同源。GitHub main 是市场与安装器真正拉取的
  那一份，徽章与代码版本对不上就是对外撒谎。
- **包体 ≤ 16 MB**：AstrBot 插件市场硬限制。按 git 已跟踪文件现打 zip——GitHub 上被
  拉走的就是这些文件，未跟踪的 PNG 原图不进这条口径（素材双轨守的正是这条线）。
- **运行时代码全部入库**：本地新写了模块却忘了 `git add` ⇒ push 之后 GitHub 上没有
  这个文件，装插件的人直接 ImportError（羽画 v0.2.2 就这么坏过：scanline.py 从未入库）。

边界：发布物（市场 zip、安装器拉下来的插件目录）里没有 .git，依赖 git 的两条跳过——
同 test_integrity 对 tools/ 的处理，跳过不丢防线：这两条的主场本来就在开发侧。
"""
import io
import os
import re
import subprocess
import zipfile

import pytest

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_LIMIT_BYTES = 16 * 1024 * 1024     # 插件市场整包上限
# 只查运行时代码：测试文件漏提交不影响装机，不该在写测试的中途就把人绊住
_SKIP_TOP_DIRS = ("tests",)


def _read(name):
    with open(os.path.join(_PLUGIN_ROOT, name), encoding="utf-8", errors="replace") as f:
        return f.read()


def _git_paths(*args):
    """跑一条 git 命令并返回路径列表；没有 git / 不是 git 工作区时跳过本条。

    一律带 `-z`：git 默认会把非 ASCII 与带空格的路径转义成 `"\\346\\200..."` 这种带引号
    的八进制串，拿去 os.path.join 必然 FileNotFoundError（这个坑栽过两次）。
    """
    try:
        done = subprocess.run(("git",) + args + ("-z",), cwd=_PLUGIN_ROOT,
                              capture_output=True)
    except OSError:
        pytest.skip("本机没有 git：依赖 git 的发布红线跳过")
    if done.returncode != 0:
        pytest.skip("不是 git 工作区（发布物里没有 .git）：依赖 git 的发布红线跳过")
    return [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]


def _version_carriers():
    """六个版本值：解析不到的位置留 None（与 preflight 同一套正则，改口径要两处一起改）。"""
    def first(name, pattern):
        hit = re.search(pattern, _read(name))
        return hit.group(1) if hit else None

    return {
        "main.VERSION": first("main.py", r'VERSION\s*=\s*"([^"]+)"'),
        "metadata.version": first("metadata.yaml", r"(?m)^version:\s*([\d.]+)"),
        "README.md 徽章": first("README.md", r"version-v([\d.]+)-"),
        "README.en.md 徽章": first("README.en.md", r"version-v([\d.]+)-"),
        "README.md alt": first("README.md", r'alt="v([\d.]+)"'),
        "README.en.md alt": first("README.en.md", r'alt="v([\d.]+)"'),
    }


def test_version_carriers_agree():
    carriers = _version_carriers()
    assert None not in carriers.values(), f"有版本项没解析到（正则该跟着载体改）：{carriers}"
    assert len(set(carriers.values())) == 1, f"版本号不一致：{carriers}"


def test_market_package_within_limit():
    files = _git_paths("ls-files")
    assert files, "git 说仓库里没有已跟踪文件？路径不对"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(_PLUGIN_ROOT, rel), rel)
    size = len(buf.getvalue())
    assert size <= MARKET_LIMIT_BYTES, (
        f"市场包体 {size / 1024 / 1024:.2f} MB 超过 16 MB 上限"
        "（素材只入库 WebP，PNG 原图属本机资产）"
    )


def test_runtime_modules_are_tracked():
    untracked = [p for p in _git_paths("ls-files", "--others", "--exclude-standard")
                 if p.endswith(".py") and p.split("/")[0] not in _SKIP_TOP_DIRS]
    assert not untracked, f"这些模块在本地却没入库，push 后 GitHub 上不会有：{untracked}"
