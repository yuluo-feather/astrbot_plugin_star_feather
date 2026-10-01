"""AstrBot 契约守卫：我们依赖的框架面，在真实 AstrBot 里还活着吗。

**为什么开子进程**：pytest 收集前会先导入 conftest，而 conftest 为了让插件在没有
框架的机器上也能跑测试，会把假的 astrbot 模块塞进 sys.modules——本进程里
`import astrbot` 拿到的是桩，在这儿查真框架等于查桩（假绿的经典形态）。所以本文件
自己 fork 一个干净子进程当探针：`python tests/test_astrbot_contract.py`——子进程不加载
conftest，import 到的才是真框架。

**口径来源（不许手抄名单，两处都是现成事实）**：
- 运行时依赖：插件根 `*.py` 里 `from astrbot... import ...` 的每一条（我们真正要的面）；
- 打桩面：conftest 里 `_mk_module("astrbot...", 名字=...)` 的名单——main.py 有一句
  `from astrbot.api.all import *`，星导入静态列不出名字，桩名单正是它的枚举替身。

**管什么**：模块路径导得进来、点名属性存在（**子模块也算存在**：`astrbot.api.event.filter`
是子模块而不是模块属性，只有真的 import 一次才会挂到父模块上，`hasattr` 单独看会误判
成缺失）、框架版本落在 metadata.yaml 声明的 `astrbot_version` 范围里。
**不管什么**：签名宽度（归 test_stub_signatures）与语义行为（归各域用例）；也不导入插件
本体——「真框架下能不能 import 起插件」是兼容性的下一格，等这一格稳了再谈。
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


def _imports_from_framework(path: str) -> dict:
    """插件根模块里 `from astrbot... import X` 的点名要求：{模块: {名字}}。

    星导入（`import *`）没有名字可列，跳过——那份面由 conftest 桩名单兜（见文件头）。
    """
    want: dict[str, set[str]] = {}
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module \
                and node.module.startswith("astrbot"):
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
    """守全面：插件根所有 .py 的框架导入 ∪ conftest 的桩名单。"""
    want: dict[str, set[str]] = {}
    for name in sorted(os.listdir(_PLUGIN_ROOT)):
        if name.endswith(".py"):
            for mod, names in _imports_from_framework(
                    os.path.join(_PLUGIN_ROOT, name)).items():
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


def _check_surface(want: dict) -> tuple:
    """逐项查真实框架：返回（检查到的名字数，缺失清单）。"""
    import importlib
    checked, missing = 0, []
    for mod_name in sorted(want):
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:                      # 模块路径都没了：直接报
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
    return checked, missing


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
    report["checked"], report["missing"] = _check_surface(want)
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
    want = required_surface()
    assert "astrbot.api.all" in want, "conftest 的桩名单没解析出来（星导入那份面靠它）"
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
