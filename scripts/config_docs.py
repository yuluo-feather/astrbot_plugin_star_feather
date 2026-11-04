"""配置默认值的单一来源校验/回写：Settings → schema + 两份 README。

默认值只在 core/settings.py 的 dataclass 字段上写一次；这里负责把 `_conf_schema.json`
的 default 与 README 配置表的默认值列对齐。CI 走 `check()`（tests/test_config.py），
本地回写走 `python -m scripts.gen_config --write`。
"""
import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
README_PATHS = ("README.md", "README.en.md")
SECTIONS = {
    "README.md": ("## 🎛️ 配置项", "## 📦 安装方法"),
    "README.en.md": ("## 🎛️ Configuration", "## 📦 Installation"),
}
EMPTY_DISPLAY = {"README.md": "（空）", "README.en.md": "(empty)"}
ROW_RE = re.compile(
    r"^\|\s*`([a-z_]+)\.([a-z_]+)`\s*\|\s*([A-Za-z_]+)\s*\|\s*`?([^|`]*)`?\s*\|")


def flatten(settings) -> dict[str, object]:
    """Settings → {"group.key": 值}（键名与 schema / README 对齐）。"""
    return {
        "ai.enable_ai": settings.ai.enable_ai,
        "ai.ai_provider": settings.ai.provider_id,
        "ai.persona": settings.ai.persona,
        "ai.spirit_line": settings.ai.spirit_line,
        "ai.ai_timeout": settings.ai.timeout,
        "ai.ai_fail_cooldown": settings.ai.fail_cooldown,
        "ai.ai_budget_seconds": settings.ai.budget_seconds,
        "ai.parallel_stages": settings.ai.parallel_stages,
        "ai.max_concurrency": settings.ai.max_concurrency,
        "ai.all_providers_fallback": settings.ai.all_providers_fallback,
        "ai.question_max_len": settings.ai.question_max_len,
        "ai.structure_check": settings.ai.structure_check,
        "tool.llm_tool_enabled": settings.tool.llm_enabled,
        "tool.llm_tool_cooldown": settings.tool.llm_cooldown,
        "tool.cmd_rate_limit": settings.tool.cmd_rate_limit,
        "limit.daily_count": settings.limit.daily_count,
        "output.send_mode": settings.output.send_mode,
        "output.segment_size": settings.output.segment_size,
        "output.daily_fixed": settings.output.daily_fixed,
        "output.daily_card": settings.output.daily_card,
        "output.shuffle_lines": settings.output.shuffle_lines,
        "output.disclaimer": settings.output.disclaimer,
    }


def display(value, rel: str) -> str:
    """把默认值渲染成 README 默认值列的文本（bool/int/字符串；空串按语言显示）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return EMPTY_DISPLAY[rel] if text == "" else text


def _section(text: str, rel: str) -> str:
    begin, end = SECTIONS[rel]
    i = text.find(begin)
    j = text.find(end, i + len(begin)) if i >= 0 else -1
    if i < 0 or j < 0:
        raise ValueError(f"{rel} 找不到配置章节锚点")
    return text[i:j]


def _readme_rows(text: str, rel: str) -> dict[str, tuple[str, str]]:
    rows = {}
    for line in _section(text, rel).splitlines():
        m = ROW_RE.match(line)
        if m:
            rows[f"{m.group(1)}.{m.group(2)}"] = (m.group(3), m.group(4).strip())
    return rows


def check(parse, root: Path = PLUGIN_ROOT) -> list[str]:
    """校验 schema 与 README 的默认值/字段集合和 Settings 一致，返回问题列表。"""
    defaults = flatten(parse(None))
    problems: list[str] = []
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    schema_keys = set()
    for group, spec in schema.items():
        for key, item in spec.get("items", {}).items():
            full = f"{group}.{key}"
            schema_keys.add(full)
            if full not in defaults:
                problems.append(f"_conf_schema.json 多出字段 {full}")
                continue
            if item.get("default") != defaults[full]:
                problems.append(
                    f"_conf_schema.json {full} 默认值 {item.get('default')!r} "
                    f"!= Settings {defaults[full]!r}")
    missing = sorted(set(defaults) - schema_keys)
    if missing:
        problems.append(f"_conf_schema.json 缺少字段 {missing}")
    for rel in README_PATHS:
        rows = _readme_rows((root / rel).read_text(encoding="utf-8"), rel)
        for key, value in defaults.items():
            if key not in rows:
                problems.append(f"{rel} 配置表缺 {key}")
                continue
            _typ, cell = rows[key]
            expected = display(value, rel)
            if cell != expected:
                problems.append(f"{rel} {key} 默认值 {cell!r} != {expected!r}")
        extra = sorted(set(rows) - set(defaults))
        if extra:
            problems.append(f"{rel} 配置表多出字段 {extra}")
    return problems


def write(parse, root: Path = PLUGIN_ROOT) -> list[str]:
    """按 Settings 回写 schema default 与 README 默认值列，返回变更项列表。"""
    defaults = flatten(parse(None))
    changed: list[str] = []
    schema_path = root / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for group, spec in schema.items():
        for key, item in spec.get("items", {}).items():
            full = f"{group}.{key}"
            if full in defaults and item.get("default") != defaults[full]:
                item["default"] = defaults[full]
                changed.append(f"_conf_schema.json {full}")
    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        newline="\n")
    for rel in README_PATHS:
        path = root / rel
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            m = ROW_RE.match(line)
            if not m:
                continue
            key = f"{m.group(1)}.{m.group(2)}"
            if key not in defaults:
                continue
            expected = display(defaults[key], rel)
            if m.group(4).strip() != expected:
                def _replace_row(match, _expected=expected):
                    return (f"| `{match.group(1)}.{match.group(2)}` | {match.group(3)} "
                            f"| `{_expected}` |")

                lines[i] = ROW_RE.sub(_replace_row, line, count=1)
                changed.append(f"{rel} {key}")
        path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return changed
