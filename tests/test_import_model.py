"""导入模型红线：插件模块只有一个身份（包名），插件自己不再备导入机关。

2026-10-12 把插件从「顶层平铺名 + 手动清 sys.modules」换成包内相对导入
（from .daily import ...）。收益靠这四条守住——别处的用例全绿替代不了它们：

1. 一份文件一个身份：插件目录下的模块只能注册成 data.plugins.<目录名>.*。
   老形态下同一个 daily.py 会是「顶层 daily」＋「data.plugins....daily」两份，
   monkeypatch 打在哪个上全看运气，打空了照样绿。
2. 插件根没有 sys 机关：插件目录里的模块不访问 sys.path / sys.modules。
   那是框架的活；插件自己动手就会歪掉上一条，还连累同一解释器里同名的别人的模块。
3. 框架清得干净：按 data.plugins.<目录名>.* 前缀删，插件模块一个不剩、再导入拿到
   全新对象。顶层名永远匹配不到这个前缀——这正是当初 main.py 非写一段 pop 循环
   不可的原因。
4. 应用层不碰宿主：core / services / ai / render / app 不 import astrbot，宿主差异
   一律经 adapters/ 收窄（host-decoupling.md §4 准入条件 1）。函数体内的懒加载同样
   算数——它一样让应用层知道了宿主是谁。
"""
import ast
import os
import subprocess
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_TESTS_DIR)
_PLUGIN_PKG = "data.plugins.astrbot_plugin_star_feather"
_SKIP_DIRS = {"tests", "scripts", "tools", "__pycache__", ".git", ".ruff_cache",
              ".pytest_cache", ".mypy_cache"}


def _plugin_sources():
    for cur, dirs, files in os.walk(_PLUGIN_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(cur, name)
                yield os.path.relpath(path, _PLUGIN_ROOT).replace(os.sep, "/"), path


class TestOneIdentityPerFile:
    def test_plugin_modules_only_registered_under_package_name(self):
        """插件目录下的模块只许有包名这一个身份（冒出顶层名 = 老形态回来了）。"""
        from data.plugins.astrbot_plugin_star_feather import main as main_mod

        assert main_mod is sys.modules[_PLUGIN_PKG + ".main"], "入口没按包名注册？"
        offenders = []
        for name, mod in list(sys.modules.items()):
            path = getattr(mod, "__file__", None)
            if not path or not os.path.abspath(path).startswith(_PLUGIN_ROOT + os.sep):
                continue
            if os.path.relpath(path, _PLUGIN_ROOT).split(os.sep)[0] in _SKIP_DIRS:
                continue
            if name == _PLUGIN_PKG or name.startswith(_PLUGIN_PKG + "."):
                continue
            offenders.append(name)
        assert not offenders, (
            "插件模块出现了第二个身份（顶层名 + 包名）：" + repr(sorted(offenders))
            + "——补丁会打在没包名的那份上，静默打空"
        )


class TestNoImportMachineryInPlugin:
    def test_plugin_root_touches_no_sys_path_or_sys_modules(self):
        """扫描 AST（注释与字符串里提到不算），逐处报文件与行号。"""
        hits = []
        for fname, fpath in _plugin_sources():
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fname)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "sys" and node.attr in ("path", "modules")):
                    hits.append(f"{fname}:{node.lineno} sys.{node.attr}")
        assert not hits, "插件根不该自备导入机关：" + "；".join(hits)


class TestHostImportBoundary:
    """宿主解耦准入条件 1：应用层不直接 import astrbot（adapters/ 与 main.py 除外）。"""

    _LAYERS = {"core", "services", "ai", "render", "app"}

    def test_app_layers_do_not_import_astrbot(self):
        """扫描 AST，函数体内的懒加载同样算——它一样让应用层知道了宿主是谁。"""
        hits = []
        for fname, fpath in _plugin_sources():
            if fname.split("/")[0] not in self._LAYERS:
                continue
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fname)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    if root == "astrbot":
                        hits.append(f"{fname}:{node.lineno} from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] == "astrbot":
                            hits.append(f"{fname}:{node.lineno} import {alias.name}")
        assert not hits, (
            "应用层直接依赖了宿主，应改为经 adapters/ 收窄："
            + "；".join(hits)
        )


_CHILD = r'''
import importlib
import sys

sys.path.insert(0, r"{tests_dir}")
import conftest  # noqa: F401  装上 astrbot 桩与插件包链

pkg = "{pkg}"
main1 = importlib.import_module(pkg + ".main")
core1 = importlib.import_module(pkg + ".services.render")
tracked = sorted(n for n in sys.modules if n.startswith(pkg + "."))
assert len(tracked) >= 10, "插件模块没挂在包名下：" + repr(tracked)
assert pkg + ".services.daily" in tracked and pkg + ".ai.prompts" in tracked, str(tracked)

# 模拟框架的重载清理：按 data.plugins.<目录名>.* 前缀删（AstrBot 的清理就是这个口径）
for name in tracked:
    del sys.modules[name]
left = sorted(n for n in sys.modules if n.startswith(pkg + "."))
assert left == [], "前缀清理漏了：" + repr(left)

main2 = importlib.import_module(pkg + ".main")
core2 = importlib.import_module(pkg + ".services.render")
card2 = importlib.import_module(pkg + ".render.store")
assert main2 is not main1 and core2 is not core1, "重新导入拿到的是旧模块（僵尸没清掉）"
# 同源自检：services.render 的模块级名字引用的必须是新 render.store 里的那个函数
assert core2._schedule_image_cleanup.__globals__ is card2.__dict__, "两模块不是同一代"
print("OK:" + str(len(tracked)))
'''


class TestFrameworkPurgeReachesEverything:
    def test_prefix_purge_clears_all_and_reimport_is_fresh(self):
        """在子进程里真删真重导：本进程还握着别的地方的模块对象，不能就地糟蹋。"""
        code = _CHILD.format(tests_dir=_TESTS_DIR, pkg=_PLUGIN_PKG)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=_TESTS_DIR)
        assert proc.returncode == 0, (
            "子进程导入模型自证失败：\n" + proc.stdout + "\n" + proc.stderr
        )
        assert proc.stdout.startswith("OK:"), proc.stdout


class TestLayerDependencyDirection:
    """依赖方向：下层不许认识上层。

    `services -> adapters` 是 2026-11-10 已登记的例外：clock / kv_utils 是宿主
    存储与时区的窄封装；移除它们需要先抽 KV / Clock 端口，另行评估。
    """

    _FORBIDDEN = {
        "core": {"services", "ai", "render", "adapters", "app"},
        "ai": {"services", "render", "adapters", "app"},
        "render": {"services", "ai", "adapters", "app"},
        "services": {"app"},
    }

    @staticmethod
    def _target_paths(fname: str, node: ast.ImportFrom) -> list[list[str]]:
        """把 ImportFrom 解析成目标模块的层级路径（相对导入按文件所在包上溯）。"""
        if node.level == 0:
            return [node.module.split(".")] if node.module else []
        pkg = os.path.dirname(fname).split("/")
        base = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
        if node.module:
            return [base + node.module.split(".")]
        return [base + [alias.name] for alias in node.names]

    def test_layers_only_depend_downwards(self):
        hits = []
        for fname, fpath in _plugin_sources():
            forbidden = self._FORBIDDEN.get(fname.split("/")[0])
            if not forbidden:
                continue
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fname)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                for parts in self._target_paths(fname, node):
                    if parts and parts[0] in forbidden:
                        hits.append(f"{fname}:{node.lineno} -> {'.'.join(parts)}")
        assert not hits, "依赖方向反了（下层认识了上层）：" + "；".join(hits)


_NO_HOST_CHILD = r'''
import importlib
import importlib.machinery
import sys
import types


class _BlockHost:
    """任何 astrbot 导入都判失败：应用层不该顺着依赖链认识宿主。"""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "astrbot" or fullname.startswith("astrbot."):
            raise ImportError("blocked host import: " + fullname)
        return None


sys.meta_path.insert(0, _BlockHost())

# 与 conftest._install_plugin_package 同一套包链，但不装 astrbot 桩——
# 这里要验证的正是「没有宿主也能导入 services」。
pkg = "{pkg}"
path = r"{plugin_root}"
parts = pkg.split(".")
parent = None
for i, part in enumerate(parts):
    name = ".".join(parts[:i + 1])
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__path__ = [path] if i == len(parts) - 1 else []
        mod.__package__ = name
        mod.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=True)
        sys.modules[name] = mod
    if parent is not None:
        setattr(parent, part, mod)
    parent = mod

importlib.import_module(pkg + ".services.daily")
importlib.import_module(pkg + ".services.gating")
left = sorted(n for n in sys.modules if n == "astrbot" or n.startswith("astrbot."))
assert left == [], "host modules loaded while importing services: " + repr(left)
print("OK")
'''


class TestApplicationLayersDoNotPullHost:
    def test_services_import_does_not_load_astrbot(self):
        """导入 services.daily / gating 不得触达 astrbot（间接链也要断）。

        2026-11-10：`adapters/__init__.py` 曾急切导入 `astrbot_io`，只改两处
        logger 不够；这条测试钉住的是整条导入链，不是单个 import 语句。
        """
        code = _NO_HOST_CHILD.format(plugin_root=_PLUGIN_ROOT, pkg=_PLUGIN_PKG)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=_TESTS_DIR)
        assert proc.returncode == 0, (
            "子进程验证失败（services 仍会拉起宿主）：\n" + proc.stdout + "\n" + proc.stderr
        )
        assert proc.stdout.startswith("OK"), proc.stdout
