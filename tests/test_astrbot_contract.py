"""AstrBot 契约守卫：我们依赖的框架面，在真实 AstrBot 里还活着吗。

**为什么开子进程**：pytest 收集前会先导入 conftest，而 conftest 为了让插件在没有
框架的机器上也能跑测试，会把假的 astrbot 模块塞进 sys.modules——本进程里
`import astrbot` 拿到的是桩，在这儿查真框架等于查桩（假绿的经典形态）。所以本文件
自己 fork 一个干净子进程当探针：`python tests/test_astrbot_contract.py`——子进程不加载
conftest，import 到的才是真框架。

**口径来源（不许手抄名单，两处都是现成事实）**：
- 运行时依赖：插件目录下所有运行时 `.py`（排除 `tests` / `tools` / `scripts`）里
  `from astrbot... import ...` 的每一条（我们真正要的面）；
- 打桩面：conftest 里 `_mk_module("astrbot...", 名字=...)` 的名单。2026-11-01 起插件
  不再用 `from astrbot.api.all import *`，运行时依赖已能逐条解析，桩名单只作补充。

**管什么**：模块路径导得进来、点名属性存在（**子模块也算存在**：`astrbot.api.event.filter`
是子模块而不是模块属性，只有真的 import 一次才会挂到父模块上，`hasattr` 单独看会误判
成缺失）、框架版本落在 metadata.yaml 声明的 `astrbot_version` 范围里。
**不管什么**：签名宽度（归 test_stub_signatures）与语义行为（归各域用例）；也不导入插件
本体——「真框架下能不能 import 起插件」归 scripts/plugin_import_check.py（CI 契约格里
只观察、不设卡）。
**红不拦路**：CI 那一格 continue-on-error（报而不拦）；本机没有框架时跳过，不误红。

本机手跑（要真框架就把 PYTHONPATH 指到 AstrBot 根）：
    python tests/test_astrbot_contract.py
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_TESTS_DIR)
_SKIP_DIRS = {"tests", "scripts", "tools", "__pycache__", ".git", ".ruff_cache",
              ".pytest_cache", ".mypy_cache"}

# 只在更高版本才有的框架面：低于门槛时缺失不算违约。
# astrbot.api.web 是插件视图的后端接口，README 已注明视图需要 4.24.2+；
# 更低版本的插件加载与聊天不受影响（app/webui.py 在 handler 内惰性导入）。
VERSION_GATED_SURFACES = {"astrbot.api.web": "4.24.2"}


def _runtime_sources():
    """插件目录下所有运行时 .py（测试/工具/缓存目录不算我们依赖框架的面）。"""
    for cur, dirs, files in os.walk(_PLUGIN_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(cur, name)


def _imports_from_framework(path: str) -> dict:
    """插件运行时模块里 `from astrbot... import X` 的点名要求：{模块: {名字}}。

    星导入（`import *`）没有名字可列，跳过——那份面由 conftest 桩名单兜（见文件头）。
    """
    want: dict[str, set[str]] = {}
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and node.level == 0
                and node.module
                and (node.module == "astrbot" or node.module.startswith("astrbot."))):
            names = {a.name for a in node.names if a.name != "*"}
            if names:
                want.setdefault(node.module, set()).update(names)
    return want


def _stubbed_surface(path: str) -> dict:
    """conftest 的桩名单：`_mk_module("astrbot...", 名字=...)` → {模块: {名字}}。

    建了模块却一个名字没挂（`_mk_module("astrbot.api")`）的也收进来：模块路径本身
    就是我们要求框架提供的面（桩能 import，真框架也得能）。
    """
    want: dict[str, set[str]] = {}
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_mk_module"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        mod = node.args[0].value
        if not isinstance(mod, str) or not mod.startswith("astrbot"):
            continue
        want.setdefault(mod, set()).update(kw.arg for kw in node.keywords if kw.arg)
    return want


def required_surface() -> dict:
    """守全面：插件目录下所有运行时 .py 的框架导入 ∪ conftest 的桩名单。"""
    want: dict[str, set[str]] = {}
    for path in _runtime_sources():
        for mod, names in _imports_from_framework(path).items():
            want.setdefault(mod, set()).update(names)
    for mod, names in _stubbed_surface(os.path.join(_TESTS_DIR, "conftest.py")).items():
        want.setdefault(mod, set()).update(names)
    return want


def declared_range() -> str:
    """metadata.yaml 里声明的 astrbot_version（唯一真源，别在测试里另写一份）。

    取不到就返回空串，由测试判红——解析失效必须响，不能把断言空转成假绿。
    """
    with open(os.path.join(_PLUGIN_ROOT, "metadata.yaml"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^astrbot_version:\s*[\"']?([^\"'\n]+)", text, re.M)
    return m.group(1).strip() if m else ""


def _below_gate(mod_name: str, framework: str) -> bool:
    """当前框架低于该面要求的版本时返回 True；版本读不出来时返回 False（宁可报缺）。"""
    gate = VERSION_GATED_SURFACES.get(mod_name)
    if not gate:
        return False
    try:
        from packaging.version import Version
        return Version(framework) < Version(gate)
    except Exception:
        return False


def _check_surface(want: dict, framework: str = "unknown") -> tuple:
    """逐项查真实框架：返回（检查到的名字数，缺失清单，按版本门槛跳过的面）。"""
    import importlib
    checked, missing, gated = 0, [], []
    for mod_name in sorted(want):
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:                      # 模块路径都没了：直接报
            if _below_gate(mod_name, framework):
                gated.append(mod_name)
                continue
            missing.append(mod_name + "（模块导入失败：" + type(exc).__name__ + "）")
            continue
        for name in sorted(want[mod_name]):
            checked += 1
            if hasattr(mod, name):
                continue
            try:                                      # 子模块也算存在
                importlib.import_module(mod_name + "." + name)
            except Exception:
                missing.append(mod_name + "." + name)
    return checked, missing, gated


def _framework_version() -> str:
    """框架版本：源码运行时（launcher 直接指 PYTHONPATH）没有 dist 元数据，故先读
    `astrbot.__version__`，取不到再退回发行包元数据。"""
    try:
        import astrbot
        v = getattr(astrbot, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    try:
        import importlib.metadata as md
        return md.version("astrbot")
    except Exception:
        return "unknown"


def _in_declared_range(version: str, declared: str):
    """版本是否落在声明范围内；packaging 不可用或版本未知时返回 None（不判）。"""
    if not declared or version == "unknown":
        return None
    try:
        from packaging.specifiers import SpecifierSet
    except Exception:
        return None
    try:
        return SpecifierSet(declared).contains(version, prereleases=True)
    except Exception:
        return None


def probe() -> dict:
    """探针：只在子进程（或手跑）里执行——本进程的 astrbot 是桩，别信。"""
    want = required_surface()
    report: dict[str, Any] = {"surface_modules": len(want),
              "surface_names": sum(len(v) for v in want.values()),
              "declared": declared_range(),
              "checked": 0, "missing": []}
    try:
        import astrbot  # noqa: F401
    except Exception as exc:
        report["skipped"] = type(exc).__name__
        return report
    report["framework"] = _framework_version()
    report["checked"], report["missing"], report["gated"] = _check_surface(
        want, report["framework"])
    report["in_range"] = _in_declared_range(report["framework"], report["declared"])
    return report


_REPORT = None


def _probe_report() -> dict:
    """跑一次子进程探针，拿回它的 JSON 报告。

    一次会话只跑一次（子进程要现 import 真框架，几秒级），两个用例共用同一份结论。
    """
    global _REPORT
    if _REPORT is not None:
        return _REPORT
    proc = subprocess.run([sys.executable, os.path.abspath(__file__)],
                          capture_output=True, text=True, encoding="utf-8", timeout=300,
                          cwd=tempfile.mkdtemp(prefix="sf-contract-probe-"))
    assert proc.returncode == 0, "探针进程自己崩了：" + (proc.stderr or "")[-2000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert line, "探针没吐 JSON（stdout：" + (proc.stdout or "")[-500:] + "）"
    _REPORT = json.loads(line[-1])
    return _REPORT


def test_surface_sources_are_parsable():
    """口径来源解析得出东西（解析失效=守卫空转，直接红）。本机无需框架即可跑。"""
    paths = list(_runtime_sources())
    assert any(p.endswith(os.path.join("adapters", "deliver.py")) for p in paths), (
        "运行时扫描没走到适配器子包，契约守卫会漏掉子包导入：" + str(paths))
    want = required_surface()
    bad = [m for m in want if m != "astrbot" and not m.startswith("astrbot.")]
    assert not bad, "非框架模块混进契约清单（相对导入误判？）：" + str(bad)
    assert "astrbot.api.star" in want, "插件根没解析出框架导入？口径来源失效：" + str(want)
    assert "astrbot.api.message_components" in want, (
        "子包里的 message_components 导入没进清单：" + str(want))
    assert "astrbot.api.web" in want, (
        "views 后端依赖的 astrbot.api.web 没进清单（惰性导入也要扫到）：" + str(want))
    assert sum(len(v) for v in want.values()) >= 5, "框架面太少，解析多半失效了：" + str(want)
    assert declared_range(), "metadata.yaml 里没取到 astrbot_version"


def test_real_framework_surface_alive():
    """真实 AstrBot 里我们依赖的面逐项存活（没有框架的机器跳过）。"""
    report = _probe_report()
    if report.get("skipped"):
        pytest.skip("本机 import 不到 AstrBot（" + report["skipped"]
                    + "）——契约格在 CI 的真框架环境里跑")
    assert not report["missing"], ("AstrBot 侧我们依赖的面没了（声明范围 "
                                   + report["declared"] + "，实测 "
                                   + report["framework"] + "）："
                                   + "；".join(report["missing"]))
    print("[契约] astrbot " + report["framework"] + "；查 " + str(report["checked"])
          + " 个名字 / " + str(report["surface_modules"]) + " 个模块；声明 "
          + report["declared"] + "；缺失 0")


def test_framework_version_in_declared_range():
    """实测框架版本落在 metadata.yaml 声明的范围内（拿不到版本/范围就跳过）。"""
    report = _probe_report()
    if report.get("skipped"):
        pytest.skip("本机 import 不到 AstrBot")
    if report.get("in_range") is None:
        pytest.skip("版本或声明范围取不到（framework=" + str(report.get("framework"))
                    + "，declared=" + str(report.get("declared")) + "）")
    assert report["in_range"], ("实测框架 " + report["framework"] + " 不在声明的 "
                                + report["declared"] + " 里——要么升声明范围，要么改依赖")


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False))


def test_version_gates_match_declared_reality():
    """4.16 上缺 astrbot.api.web 是预期；4.24.2 起才算违约；版本读不出来按违约报。"""
    assert _below_gate("astrbot.api.web", "4.16.0") is True
    assert _below_gate("astrbot.api.web", "4.24.2") is False
    assert _below_gate("astrbot.api.web", "unknown") is False
    assert _below_gate("astrbot.api.unknown", "4.16.0") is False
