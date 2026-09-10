"""数据完整性域：牌库 / 签文池 / 字形覆盖 / 配置枚举——枚举性校验都归这里。

说人话：牌灵的家底要定期清点——一张牌、一句话、一个字、一个配置项，都不能少。"""
import importlib.util
import os

from PIL import ImageFont

import fonts
from dailylines import SIGIL_LINES
from settings import PERSONA_VALUES, TarotSettings
from tarot_data import TAROT_CARDS


class TestTarotDataIntegrity:
    """牌库完整性：78 张牌，键（花色, 序号）唯一，正逆位是一张牌的两面。"""

    def test_every_card_unique_key(self):
        keys = [(c["suit"], c["num"]) for c in TAROT_CARDS]
        assert len(keys) == 78
        assert len(set(keys)) == 78

    def test_keywords_exactly_three(self):
        # 卡面信息区两行硬截断（card_render: lines[:2]），关键词必须统一 3 个，
        # 否则第 4 个会被吞——这条假设以前只靠人工控制，现在锁进测试
        for c in TAROT_CARDS:
            for side in ("up", "down"):
                assert len(c[side]["keywords"]) == 3, (c["id"], side, c[side]["keywords"])

    def test_unique_id_and_order(self):
        # 唯一 ID（外部引用锚点）与全局排序号 0–77：连续、与数组顺序一致
        ids = [c["id"] for c in TAROT_CARDS]
        assert len(set(ids)) == 78
        assert all(c["id"].startswith(c["suit"] + "_") for c in TAROT_CARDS)
        assert [c["order"] for c in TAROT_CARDS] == list(range(78))


class TestDailylinesIntegrity:
    """签文池完整性：78 张全覆盖 / 正逆位各≥2 条 / 无孤儿键 / 正逆不重复 / 长度可控。"""

    def test_every_card_covered(self):
        # 78 张牌每张都必须有签文，正逆各至少 2 条
        keys = {(c["suit"], c["num"]) for c in TAROT_CARDS}
        assert len(TAROT_CARDS) == 78
        assert keys == set(SIGIL_LINES.keys())
        for entry in SIGIL_LINES.values():
            assert len(entry["up"]) >= 2
            assert len(entry["down"]) >= 2

    def test_no_orphan_entries(self):
        # 池里不能有牌库之外的键（写错牌名=白写还查不出）
        keys = {(c["suit"], c["num"]) for c in TAROT_CARDS}
        assert all(k in keys for k in SIGIL_LINES)

    def test_up_down_disjoint(self):
        # 正逆位签文内容不能重复（同一天正逆位撞成同一句就尴尬了）
        for entry in SIGIL_LINES.values():
            assert set(entry["up"]).isdisjoint(entry["down"])

    def test_lines_not_too_long(self):
        # 卡片签文区一行放得下：30 字以内（超出会截断，观感差）
        for entry in SIGIL_LINES.values():
            for line in entry["up"] + entry["down"]:
                assert len(line) <= 32, line


def _subset_cmap(name: str) -> set:
    """读内置子集的码点集合。fontTools 优先，缺失时退到打包的 charsets.json。

    这里直接查 cmap，不走 fonts._font_covers——免得校验函数里的豁免逻辑
    再次把缺字（尤其空格）藏起来，测试就白写了。"""
    path = os.path.join(fonts._FONT_DIR, name)
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(path, lazy=True)
        try:
            return set(tt.getBestCmap() or {})
        finally:
            tt.close()
    except ImportError:
        import json
        with open(os.path.join(fonts._FONT_DIR, "charsets.json"),
                  encoding="utf-8") as f:
            return set(json.load(f)["fonts"][name])


def _load_subset_builder():
    """按文件路径加载子集生成脚本，复用它的字符集口径。

    字形的真相源只该有一处：测试不再自己手抄一份字符集（上一版就是这么把
    空格漏出子集的）。脚本在 tools/ 下，不在插件运行路径里，按路径加载即可。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "build_font_subset.py")
    spec = importlib.util.spec_from_file_location("build_font_subset", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFontIntegrity:
    """字形覆盖完整性：当前牌库文案（牌名+正逆位牌义全量）全部在内置子集内。"""

    SUBSETS = ("StarFeather-Regular.otf", "StarFeather-Bold.otf")

    # 渲染模板：标题 / 日期 / 牌名行 / 阵位 / 署名 / 水印，均取自 card_render
    # 与 daily 的真实绘制调用——逐字符必须落在子集里
    RENDER_TEMPLATES = (
        "星羽塔罗·今日牌运",
        "9月10日·周四",
        "正位 · 圣杯三",
        "逆位 · 权杖王后",
        "牌阵 · 羽时三刻",
        "—— 牌灵·星羽塔罗",
        "星羽塔罗 Star Feather",
        "【过去】",
    )

    def test_current_texts_covered(self):
        # 验证 README 声明：当前牌库文案（牌名+正逆位牌义，含关键词/能量状态/走向/心法/转身）
        # 全部在内置子集内——渲染卡面与兜底文字都不会出豆腐块。
        # 两个字体都验（与 SUBSETS 对齐）：本方法名说的是「当前文案覆盖」，只测
        # Regular 会名不副实——读测试的人会以为 Bold 也验过了。
        texts = []
        for c in TAROT_CARDS:
            texts.append(c["cn"])
            for side in ("up", "down"):
                kw = "、".join(c[side]["keywords"])
                texts.append(kw)
                texts.append(kw + ("。" + c[side].get("what", "") if c[side].get("what") else ""))
                energy = c[side].get("energy", "")
                if energy:
                    texts.append(energy)
                tail = c[side].get("how") or c[side].get("turn") or ""
                if tail:
                    texts.append(tail)
        texts += ["牌阵 · 羽时三刻", "正位", "逆位", "过去", "现在", "未来", "【过去】"]
        for name in self.SUBSETS:
            font = ImageFont.truetype(os.path.join(fonts._FONT_DIR, name), 20)
            for t in texts:
                assert fonts._font_covers(font, t), f"{name} 缺字: {t!r}"

    def test_space_glyph_present(self):
        """回归：子集漏掉 U+0020 时，牌面「正位 · 圣杯三」的两个空格会被画成
        豆腐块——而旧校验用 isspace 把空格豁免掉，连系统字体回退都触发不了。"""
        for name in self.SUBSETS:
            assert 0x20 in _subset_cmap(name), f"{name} 缺空格字形 U+0020"

    def test_render_templates_covered(self):
        """渲染模板逐字符覆盖：标题、日期、牌名行、阵位、署名、水印。"""
        for name in self.SUBSETS:
            cmap = _subset_cmap(name)
            for text in self.RENDER_TEMPLATES:
                missing = [c for c in text if ord(c) not in cmap]
                assert not missing, f"{name} 缺字 {missing}（模板 {text!r}）"

    def test_builder_charset_covered(self):
        """生成脚本算出的字符集必须全部落在子集里：口径与产物对账，
        漏字（新文案、模板标点、空格）当场报错，不用等人眼发现豆腐块。"""
        charset, _ = _load_subset_builder()._build_charset()
        for name in self.SUBSETS:
            cmap = _subset_cmap(name)
            missing = sorted(ord(c) for c in charset if ord(c) not in cmap)
            assert not missing, f"{name} 缺 {len(missing)} 个字形: {missing[:20]}"

    def test_font_covers_not_exempting_space(self):
        """_font_covers 不得豁免空格：造一个「缺空格的 cmap」，覆盖判定必须为假，
        这样才会回退系统字体，而不是带着缺字形直接画豆腐块。"""
        path = os.path.join(fonts._FONT_DIR, "StarFeather-Regular.otf")
        font = ImageFont.truetype(path, 20)
        saved = fonts._FONT_CMAP.get(path)
        fonts._FONT_CMAP[path] = {ord("正"), ord("位")}
        try:
            assert not fonts._font_covers(font, "正 位")
            assert fonts._font_covers(font, "正位")
        finally:
            if saved is None:
                fonts._FONT_CMAP.pop(path, None)
            else:
                fonts._FONT_CMAP[path] = saved


class TestSchemaIntegrity:
    """配置完整性：枚举导出与默认值，与 settings 语义层逐项对齐。"""

    def test_enum_export(self):
        assert PERSONA_VALUES == ("off", "tsundere", "gentle", "mystic", "random")

    def test_defaults(self):
        s = TarotSettings(None)
        assert s.send_mode == "forward"
        assert s.shuffle_lines is True
        assert s.disclaimer == "✨ 占卜仅供娱乐参考，选择权永远在你手里。"
        assert s.daily_fixed is True
        assert s.enable_ai is True
        assert s.segment_size == 300
        assert s.ai_timeout == 30
        assert s.ai_cooldown == 60
        assert s.llm_tool_enabled is True
        assert s.llm_tool_cooldown == 60
        assert s.cmd_rate_limit == 10
        assert s.daily_count_limit == 0
        assert s.ai_provider_id == ""
        assert s.ai_max_len == 200
