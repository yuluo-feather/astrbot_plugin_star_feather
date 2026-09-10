"""配置读取工具单测：分组优先 / 扁平回退 / None 兜底 / int 下限。
——配置乱来？牌灵宁愿给默认值也不炸。"""
from config import _cfg_bool, _cfg_get, _cfg_int


class TestCfgGet:
    def test_group_wins_over_flat(self):
        assert _cfg_get({"ai": {"ai_timeout": 5}, "ai_timeout": 99}, "ai", "ai_timeout", 30) == 5

    def test_flat_fallback(self):
        assert _cfg_get({"ai_timeout": 7}, "ai", "ai_timeout", 30) == 7

    def test_none_config_returns_default(self):
        assert _cfg_get(None, "ai", "ai_timeout", 30) == 30

    def test_group_not_dict_falls_back(self):
        assert _cfg_get({"ai": "oops"}, "ai", "ai_timeout", 30) == 30

    def test_none_value_returns_default(self):
        assert _cfg_get({"ai": {"ai_timeout": None}}, "ai", "ai_timeout", 30) == 30
        assert _cfg_get({"ai_timeout": None}, "ai", "ai_timeout", 30) == 30


class TestCfgInt:
    def test_int_value(self):
        assert _cfg_int({"a": {"x": 5}}, "a", "x", 3) == 5

    def test_flat_fallback(self):
        assert _cfg_int({"x": "7"}, "a", "x", 3) == 7

    def test_invalid_returns_default(self):
        assert _cfg_int({"a": {"x": "bad"}}, "a", "x", 3) == 3
        assert _cfg_int({"a": {"x": None}}, "a", "x", 3) == 3

    def test_floor_applied(self):
        assert _cfg_int({"a": {"x": 1}}, "a", "x", 30, floor=5) == 5

    def test_no_floor_passes_through(self):
        assert _cfg_int({"a": {"x": 0}}, "a", "x", 30) == 0


class TestCfgBool:
    def test_bool_value(self):
        assert _cfg_bool({"a": {"x": False}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": True}}, "a", "x", False) is True

    def test_string_false_not_true(self):
        # 回归：bool("false") 曾错判为 True
        assert _cfg_bool({"a": {"x": "false"}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": "0"}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": "off"}}, "a", "x", True) is False

    def test_string_true_forms(self):
        assert _cfg_bool({"a": {"x": "true"}}, "a", "x", False) is True
        assert _cfg_bool({"a": {"x": "1"}}, "a", "x", False) is True

    def test_numeric_zero_false(self):
        assert _cfg_bool({"a": {"x": 0}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": 1}}, "a", "x", False) is True

    def test_default_when_missing(self):
        assert _cfg_bool({}, "a", "x", True) is True




# ---------- _conf_schema.json 机械校验 ----------
# 背景：AstrBot 只在加载插件时校验 _conf_schema.json，pytest 覆盖不到——
# type 写成 JSON Schema 风格的 "integer" 会让插件直接加载失败，而单测全绿。
# 这里把框架的校验口径前置到单测：type 白名单 / default 类型相容 / select 选项 /
# 描述齐全 / 无重复键 / schema 字段与代码取键双向对账。
import ast
import json
import os

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(_PLUGIN_ROOT, "_conf_schema.json")

# 框架认得的控件类型白名单（AstrBot 配置组件）：integer / str / boolean 这类
# JSON Schema 风格写法不在其中，出现即插件加载失败
TYPE_WHITELIST = ("int", "float", "bool", "string", "text", "list", "file",
                  "object", "template_list", "dict")
GROUP_TYPE_OK = ("object", "dict")
REQUIRED_GROUP_KEYS = ("type", "description", "hint", "items")

# 取键原语：settings.py 等按 (group, key) 字面量读配置
_CFG_READERS = ("_cfg_get", "_cfg_int", "_cfg_bool")
# 唯一允许动态取键的文件：原语层本身（group / key 是它的入参，不是具体配置项）
_PRIMITIVE_LAYER = "config.py"
# 旧扁平配置键：schema 已迁到分组（output.send_mode），读取侧保留扁平回退兼容，
# 不属于 schema 字段，对账时豁免（见 settings.resolve_send_mode）
LEGACY_FLAT_KEYS = {("output", "forward_result"), ("output", "show_image")}


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _iter_fields(node, path):
    """递归遍历 schema 字段定义，产出 (字段路径, 字段 dict)。

    分组本身与 items 下的每个子项都算字段；items 为 dict（object 型分组）
    或 list（template_list 型）都继续往下走。
    """
    out = []
    if not isinstance(node, dict):
        return out
    for key, sub in node.items():
        if not isinstance(sub, dict):
            continue
        here = path + "." + key if path else key
        out.append((here, sub))
        items = sub.get("items")
        if isinstance(items, dict):
            out.extend(_iter_fields(items, here + ".items"))
        elif isinstance(items, list):
            for i, el in enumerate(items):
                if isinstance(el, dict):
                    out.extend(_iter_fields({str(i): el}, here + ".items"))
    return out


def _default_ok(declared, value) -> bool:
    """default 的 JSON 类型是否与声明 type 相容。

    bool 必须先判：True/False 是 int 的子类，先走数值分支的话
    「bool 键写了 1」会被当成合法 int 放过。
    """
    if declared == "bool":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if declared == "int":
        return isinstance(value, int)
    if declared == "float":
        return isinstance(value, (int, float))
    if declared in ("string", "text", "file"):
        return isinstance(value, str)
    if declared in ("list", "template_list"):
        return isinstance(value, list)
    if declared in ("object", "dict"):
        return isinstance(value, dict)
    return False


class TestConfSchemaMechanical:
    """_conf_schema.json 机械校验（框架只在加载时校验，靠单测兜住）。"""

    @staticmethod
    def _schema() -> dict:
        return _load_schema()

    def test_top_level_groups_shape(self):
        schema = self._schema()
        assert isinstance(schema, dict) and schema, "schema 顶层必须是非空对象"
        bad = []
        for group, spec in schema.items():
            if not isinstance(spec, dict):
                bad.append(group + ": 分组不是对象（" + type(spec).__name__ + "）")
                continue
            missing = [k for k in REQUIRED_GROUP_KEYS if k not in spec]
            if missing:
                bad.append(group + ": 缺 " + str(missing))
            if spec.get("type") not in GROUP_TYPE_OK:
                bad.append(group + ": type=" + repr(spec.get("type"))
                           + " 不是 object/dict")
            if not isinstance(spec.get("items"), dict):
                bad.append(group + ": items 必须是子项对象")
        assert not bad, "分组结构违规：" + "；".join(bad)

    def test_field_types_in_framework_whitelist(self):
        bad = []
        for path, field in _iter_fields(self._schema(), ""):
            if field.get("type") not in TYPE_WHITELIST:
                bad.append(path + ": type=" + repr(field.get("type")))
        assert not bad, ("字段 type 不在框架白名单（JSON Schema 风格写法会让插件加载失败）："
                         + "；".join(bad))

    def test_default_types_compatible(self):
        bad = []
        for path, field in _iter_fields(self._schema(), ""):
            if "default" not in field:
                continue
            if not _default_ok(field.get("type"), field["default"]):
                bad.append(path + ": type=" + repr(field.get("type"))
                           + " 但 default=" + repr(field["default"])
                           + "（" + type(field["default"]).__name__ + "）")
        assert not bad, "default 与 type 不相容：" + "；".join(bad)

    def test_select_special_default_in_options(self):
        bad = []
        seen = 0
        for path, field in _iter_fields(self._schema(), ""):
            if field.get("_special") != "select" or "options" not in field:
                continue
            seen += 1
            options = field["options"]
            if not isinstance(options, list) or not options:
                bad.append(path + ": options 必须是非空列表，实为 " + repr(options))
            elif field.get("default") not in options:
                bad.append(path + ": default=" + repr(field.get("default"))
                           + " 不在 options 里")
        assert not bad, "select 选项违规：" + "；".join(bad)
        assert seen, "没扫到任何 _special=select 字段——路径可能已失效，断言会空转"

    def test_every_field_has_description(self):
        bad = []
        for path, field in _iter_fields(self._schema(), ""):
            desc = field.get("description")
            if not isinstance(desc, str) or not desc.strip():
                bad.append(path + ": " + repr(desc))
        assert not bad, "字段缺 description（WebUI 上不可读）：" + "；".join(bad)

    def test_no_duplicate_json_keys(self):
        dups = []

        def hook(pairs):
            seen = set()
            for k, _v in pairs:
                if k in seen:
                    dups.append(k)
                seen.add(k)
            return dict(pairs)

        with open(SCHEMA_PATH, encoding="utf-8") as f:
            json.load(f, object_pairs_hook=hook)
        assert not dups, ("schema 有重复键（json.loads 默认静默去重，框架只会读到残缺配置）："
                          + str(sorted(set(dups))))

    @staticmethod
    def _referenced_keys():
        """AST 扫源码里的 (group, key) 字面量取键。

        返回 (字面量键集合, 动态取键位置列表)：动态取键只允许出现在原语层
        config.py（那里 group/key 是入参而非具体配置项），其他文件出现即报——
        否则 A7 会静默漏掉它的取键，对账变成假绿。
        """
        refs, dynamic = set(), []
        for name in sorted(os.listdir(_PLUGIN_ROOT)):
            if not name.endswith(".py"):
                continue
            fpath = os.path.join(_PLUGIN_ROOT, name)
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in _CFG_READERS:
                    continue
                args = node.args
                literal = (len(args) >= 3
                           and all(isinstance(a, ast.Constant) and isinstance(a.value, str)
                                   for a in args[1:3]))
                if literal:
                    refs.add((args[1].value, args[2].value))
                elif name != _PRIMITIVE_LAYER:
                    dynamic.append(name + ":" + str(node.lineno))
        return refs, dynamic

    def test_schema_fields_match_code_references(self):
        """双向对账：schema 字段集合 == 代码取键集合（防加字段忘写 schema / 反之）。"""
        schema_fields = {(g, f) for g, spec in self._schema().items()
                         for f in (spec.get("items") or {})}
        refs, dynamic = self._referenced_keys()
        assert not dynamic, ("出现动态取键，无法对账（请写字面量，或确认后改本测试）："
                             + "；".join(dynamic))
        refs -= LEGACY_FLAT_KEYS
        only_schema = sorted(schema_fields - refs)
        only_code = sorted(refs - schema_fields)
        assert not only_schema and not only_code, (
            "schema 有而代码未读：" + str(only_schema)
            + "；代码读了但 schema 没有：" + str(only_code))
