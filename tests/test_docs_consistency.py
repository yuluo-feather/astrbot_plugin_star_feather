"""测试清单对账：README.md / README.en.md / tests/conftest.py 三处手写清单
与 tests/ 目录的实际 test_*.py 文件集合必须**相等**（多一个少一个都要报）。

为什么：手写清单没有对账就是过期文档——README 漏了 test_kv_utils、
conftest 头注释漏了 test_integrity，两边各自腐化，读文档的人拿到的是假清单。
把 parse 出来的名字与实际文件集合直接比，腐化当场红。

第二块对账是配置表（2026-10-13 第 1 条补充）：`_conf_schema.json` 里的每个配置项
都要在中英文 README 的「配置项」章节里有一行，且两个重点项的默认值/取值与 schema、
settings 常量逐字一致。理由同上——面板上看得见、文档里查不到的配置项，用户只能靠猜；
新增配置项忘了写文档，这一块当场红。

边界：CHANGELOG 不参与对账（历史条目记录当时状态，是对的）；清单里的模块名
形态固定为 test_ 开头的蛇形名，注释性提及（如「踩过：test_dailylines 曾全路径
导入」）不在清单区段内，不会误收。解析容错：markdown 反引号、中英混排、跨行
断句都认；解析不到任何名字直接失败，防止解析失效把断言空转成假绿。
"""
import json
import os
import re

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_TESTS_DIR)

# 清单锚点：清单紧跟在锚点之后（改文案要同步改这里）
ANCHORS = {
    "README.md": "纯逻辑单测位于",
    "README.en.md": "Pure-logic unit tests in",
    os.path.join("tests", "conftest.py"): "测试按域分文件",
}
# 模块名形态：test_ 开头 + 小写蛇形；左右不能再接标识符字符（别从 pytest /
# xtest_foo 里刨出半截名字）
NAME_RE = re.compile(r"(?<![A-Za-z0-9_])test_[a-z][a-z_]*")

# 配置章节锚点：字段名必须出现在这个区间内（别处随口提一句不算数）
CONFIG_SECTIONS = {
    "README.md": ("## 🎛️ 配置项", "## 📦 安装方法"),
    "README.en.md": ("## 🎛️ Configuration", "## 📦 Installation"),
}
_SCHEMA_PATH = os.path.join(_PLUGIN_ROOT, "_conf_schema.json")
# 配置表的一行：| `group.key` | type | `default` | 说明 |
_ROW_RE = re.compile(
    r"^\|\s*`([a-z_]+)\.([a-z_]+)`\s*\|\s*([A-Za-z_]+)\s*\|\s*`?([^|`]*)`?\s*\|", re.M)


def _list_text(rel_path: str) -> str:
    """取清单所在文本片段：md 取锚点那一行；conftest 取锚点到第一个句号（跨行断句）。"""
    with open(os.path.join(_PLUGIN_ROOT, rel_path), encoding="utf-8") as f:
        text = f.read()
    anchor = ANCHORS[rel_path]
    i = text.find(anchor)
    assert i >= 0, rel_path + " 找不到清单锚点 " + repr(anchor) + "（锚点变了要同步改本测试）"
    seg = text[i:]
    if rel_path.endswith(".md"):
        return seg.split("\n", 1)[0]
    end = seg.find("。")
    return seg[:end] if end >= 0 else seg


def _listed_names(rel_path: str) -> set:
    return set(NAME_RE.findall(_list_text(rel_path)))


def _actual_names() -> set:
    return {n[:-3] for n in os.listdir(_TESTS_DIR)
            if n.startswith("test_") and n.endswith(".py")}


def test_list_anchor_parsable():
    """三处清单都要解析得到模块名（解析失效=对账空转，直接红）。"""
    actual = _actual_names()
    assert actual, "tests/ 下没有 test_*.py？路径不对"
    for rel in ANCHORS:
        names = _listed_names(rel)
        assert names, rel + " 的清单解析不到任何模块名（解析失效会让对账空转）"


def test_lists_match_tests_dir():
    """三处清单与实际 tests/ 文件集合集合相等（漏项 / 多余项都要报出来）。"""
    actual = _actual_names()
    problems = []
    for rel in ANCHORS:
        listed = _listed_names(rel)
        missing = sorted(actual - listed)
        extra = sorted(listed - actual)
        if missing or extra:
            problems.append(rel + ": 漏掉 " + str(missing) + "；多余 " + str(extra))
    assert not problems, "测试清单与实际 tests/ 不一致：" + "；".join(problems)


# ---------- 配置表对账：README 的中英配置表 vs _conf_schema.json ----------
def _schema() -> dict:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _section(rel_path: str) -> str:
    """取配置章节正文（锚点之间），失败即红——锚点漂了不能让对账空转。"""
    with open(os.path.join(_PLUGIN_ROOT, rel_path), encoding="utf-8") as f:
        text = f.read()
    begin, end = CONFIG_SECTIONS[rel_path]
    i = text.find(begin)
    assert i >= 0, rel_path + " 找不到配置章节锚点 " + repr(begin)
    j = text.find(end, i)
    assert j > i, rel_path + " 找不到配置章节结束锚点 " + repr(end)
    return text[i:j]


def _rows(rel_path: str) -> dict:
    """配置表 → {group.key: (类型, 默认值)}；一行都解析不出一律失败。"""
    out = {g + "." + k: (typ, default.strip())
           for g, k, typ, default in _ROW_RE.findall(_section(rel_path))}
    assert out, rel_path + " 的配置表解析不到任何行（解析失效会让对账空转）"
    return out


def _row_line(rel_path: str, key: str) -> str:
    for line in _section(rel_path).splitlines():
        if line.startswith("| `" + key + "`"):
            return line
    raise AssertionError(rel_path + " 配置表里找不到 " + key)


def test_readme_config_tables_cover_every_schema_key():
    """schema 里的每个配置项都要在两份 README 的配置表里有一行。"""
    keys = {g + "." + k for g, spec in _schema().items() for k in spec.get("items", {})}
    assert keys, "_conf_schema.json 解析不到任何字段——对账失效了，别当绿灯"
    problems = []
    for rel in CONFIG_SECTIONS:
        missing = sorted(keys - set(_rows(rel)))
        if missing:
            problems.append(rel + " 配置表缺：" + str(missing))
    assert not problems, ("README 配置表与 _conf_schema.json 不一致（面板上看得见的"
                          "项，文档里必须查得到）：" + "；".join(problems))


def test_budget_and_structure_rows_match_schema_and_code():
    """两个重点项：默认值与取值必须与 schema、settings 常量逐字一致（防文档臆造）。"""
    from data.plugins.astrbot_plugin_star_feather.settings import (
        DEFAULT_AI_BUDGET,
        DEFAULT_STRUCTURE_CHECK,
        STRUCTURE_CHECK_VALUES,
        TarotSettings,
    )

    ai = _schema()["ai"]["items"]
    assert ai["ai_budget_seconds"]["default"] == DEFAULT_AI_BUDGET
    assert ai["structure_check"]["default"] == DEFAULT_STRUCTURE_CHECK
    assert TarotSettings(None).structure_check == DEFAULT_STRUCTURE_CHECK
    assert tuple(ai["structure_check"]["options"]) == STRUCTURE_CHECK_VALUES
    for rel in CONFIG_SECTIONS:
        rows = _rows(rel)
        assert rows["ai.ai_budget_seconds"] == ("int", str(DEFAULT_AI_BUDGET)), rel
        assert rows["ai.structure_check"] == ("select", DEFAULT_STRUCTURE_CHECK), rel
        # 支持的取值一个都不能少：说明里要逐个列出三档（文档缺一档＝用户看不见那个选项）
        desc = _row_line(rel, "ai.structure_check")
        for value in STRUCTURE_CHECK_VALUES:
            assert "`" + value + "`" in desc, rel + " 的 structure_check 说明缺取值 " + value
