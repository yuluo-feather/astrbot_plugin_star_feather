"""解读结构校验（ai.structure_check）：严格口径判据 + 三档行为 + 影子计数。

为什么单开一个域：这个配置值直接映射到用户体验——严格口径拒掉一个候选＝那次占卜
整段 AI 解读降级成本地牌义。宽严之差就是体验之差，所以要被锁住的是三样东西：
1) 判据本身（hardening.validate_interpret_structure_strict 的三条，位置名不锁死）；
2) 三档在候选链上的实际行为（loose 放行 / shadow 放行但计量 / strict 拒了往下试）；
3) 影子计数给得出「候选拒绝率」与「收紧后降级率」的两个分母——只有分子算不出结论。

边界：不重复 test_hardening 的宽松口径用例（那边管输入清洗与宽松判据），
也不测预算（test_ai_budget 的域）。
"""
import asyncio
import logging

import pytest
from data.plugins.astrbot_plugin_star_feather.hardening import (
    validate_interpret_structure,
    validate_interpret_structure_strict,
)
from data.plugins.astrbot_plugin_star_feather.interpret import AiInterpreter
from data.plugins.astrbot_plugin_star_feather.settings import (
    DEFAULT_STRUCTURE_CHECK,
    STRUCTURE_CHECK_LOOSE,
    STRUCTURE_CHECK_SHADOW,
    STRUCTURE_CHECK_STRICT,
)
from stubs import EVENT, OK_INTERP, PICK, FakeContext, FakeProvider

OK3 = ("【第1张·过去】一段过去。【第2张·现在】一段现在。"
       "【第3张·未来】一段未来。【总结】结论。")
# 历史替身样本：声明三张牌、只写了两张（test_core 的集成替身曾是这个形状）。
# 它宽松通过（标记段数 3 ≥ 牌数 3，【总结】也计入段数）、严格必拒——留着当反面锚点。
LEGACY_TWO_OF_THREE = "【第1张·过去】一段过去。【第2张·现在】一段现在。【总结】结论。"


def _interp(providers, structure_check=STRUCTURE_CHECK_SHADOW):
    return AiInterpreter(FakeContext(others=providers), structure_check=structure_check)


def _ask(it, positions):
    return asyncio.run(it.interpret(EVENT, "羽签", positions,
                                    [PICK] * len(positions), "问感情", "详情"))


# ---------- 判据层：严格口径到底严在哪 ----------
class TestStrictJudgement:
    def test_standard_three_card_passes(self):
        assert validate_interpret_structure_strict(OK3, 3) == (True, ())

    def test_summary_is_optional(self):
        """总结段缺失不判失格（与宽松口径同一条）：它只影响完整性，不影响安全。"""
        no_summary = "【第1张·过去】一段过去。【第2张·现在】一段现在。"
        assert validate_interpret_structure_strict(no_summary, 2)[0] is True
        assert validate_interpret_structure_strict(OK3, 3)[0] is True

    def test_position_name_is_not_locked_to_a_fixed_set(self):
        """位置名换一组（换牌阵）照旧合法——锁死文本＝换个阵就把合法解读全判死。"""
        other = "【第1张·核心课题】A。【第2张·障碍】B。【第3张·转机】C。【总结】D。"
        assert validate_interpret_structure_strict(other, 3)[0] is True

    def test_position_name_may_be_empty_in_loose_but_not_in_strict(self):
        blank = "【第1张·】A。【第2张·现在】B。"
        assert validate_interpret_structure("【第1张·】A。【第2张·现在】B。", 2) is True
        ok, why = validate_interpret_structure_strict(blank, 2)
        assert ok is False and any("位置名为空" in r for r in why)

    def test_missing_number_rejected(self):
        ok, why = validate_interpret_structure_strict(LEGACY_TWO_OF_THREE, 3)
        assert ok is False and "缺号:3" in why

    def test_duplicate_number_rejected(self):
        text = "【第1张·过去】A。【第1张·过去】B。"
        ok, why = validate_interpret_structure_strict(text, 2)
        assert ok is False and "缺号:2" in why and any("重复编号:1" in r for r in why)

    def test_out_of_range_number_rejected(self):
        text = "【第1张·过去】A。【第2张·现在】B。【第4张·未来】D。"
        ok, why = validate_interpret_structure_strict(text, 3)
        assert ok is False and any("越界编号:4" in r for r in why)

    def test_duplicate_summary_rejected(self):
        text = "【第1张·过去】A。【总结】B。【总结】C。"
        ok, why = validate_interpret_structure_strict(text, 1)
        assert ok is False and "总结标记重复" in why

    def test_structureless_text_rejected(self):
        ok, why = validate_interpret_structure_strict("牌灵今天心情不错，随便说两句。", 2)
        assert ok is False and why == ("无【第N张·位置】标记",)

    def test_empty_rejected(self):
        assert validate_interpret_structure_strict("   ", 2) == (False, ("输出为空",))
        assert validate_interpret_structure_strict(None, 2) == (False, ("输出为空",))

    @pytest.mark.parametrize("text,n", [
        (OK3, 3), (OK_INTERP, 1), (LEGACY_TWO_OF_THREE, 3),
        ("【第1张·】A。【第2张·现在】B。", 2),
        ("【第1张·过去】A。【总结】B。【总结】C。", 1),
        ("随便一段没有标记的话", 2),
    ])
    def test_strict_pass_implies_loose_pass(self, text, n):
        """关系红线：严格通过 ⇒ 宽松通过（严格只多三条判据，不是另一套分段规则）。

        反过来不成立（LEGACY 那条就是），所以收紧只可能减少放行、不会放进新东西。
        """
        if validate_interpret_structure_strict(text, n)[0]:
            assert validate_interpret_structure(text, n) is True


# ---------- 行为层：三档在候选链上各自怎么走 ----------
class TestStructureCheckModes:
    def test_default_is_shadow(self):
        """默认档必须是影子：先量再收紧，谁都不许把默认值偷偷改成 strict。"""
        assert DEFAULT_STRUCTURE_CHECK == STRUCTURE_CHECK_SHADOW

    def test_loose_lets_mismatched_candidate_through(self):
        prov = FakeProvider("p0", result=LEGACY_TWO_OF_THREE)
        it = _interp([prov], structure_check=STRUCTURE_CHECK_LOOSE)
        assert _ask(it, ["过去", "现在", "未来"]) == LEGACY_TWO_OF_THREE

    def test_shadow_keeps_loose_behaviour(self):
        """影子档行为等于 loose：放行与否一个字都不许变（判定只进日志）。"""
        prov = FakeProvider("p0", result=LEGACY_TWO_OF_THREE)
        it = _interp([prov], structure_check=STRUCTURE_CHECK_SHADOW)
        assert _ask(it, ["过去", "现在", "未来"]) == LEGACY_TWO_OF_THREE
        assert prov.calls == 1  # 没有因为结构判定多发一次请求

    def test_strict_rejects_then_uses_next_candidate(self):
        bad = FakeProvider("bad", result=LEGACY_TWO_OF_THREE)
        good = FakeProvider("good", result=OK3)
        it = _interp([bad, good], structure_check=STRUCTURE_CHECK_STRICT)
        assert _ask(it, ["过去", "现在", "未来"]) == OK3
        assert (bad.calls, good.calls) == (1, 1)

    def test_strict_all_rejected_returns_none(self):
        """全候选都不合规 → None（调用方回退本地牌义），且不静默。"""
        prov = FakeProvider("p0", result=LEGACY_TWO_OF_THREE)
        it = _interp([prov], structure_check=STRUCTURE_CHECK_STRICT)
        assert _ask(it, ["过去", "现在", "未来"]) is None
        assert prov.calls == 1


# ---------- 计量层：影子汇总必须给出两个分母 ----------
class TestShadowTally:
    def test_summary_counts_candidate_and_rejection(self, caplog):
        """候选 1 个、严格会拒 → 汇总给「候选 1 / 拒 1」，判决是**未观测**而非降级。

        为什么不是「会降级」：这条候选宽松通过、链就此收尾，而严格口径拒了它之后会
        继续试后面的候选——那些候选本次没跑，降级与否根本没有观测量。把部分观测当
        结论用，恰恰是决定要不要收紧时最容易犯的错（主人 2026-10-12 专门点了这条）。
        """
        prov = FakeProvider("p0", result=LEGACY_TWO_OF_THREE)
        it = _interp([prov])
        with caplog.at_level(logging.INFO):
            assert _ask(it, ["过去", "现在", "未来"]) == LEGACY_TWO_OF_THREE
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()]
        assert len(line) == 1, f"影子汇总应恰好一行，实际 {line}"
        assert "本次候选拒绝率 1/1 (100.00%)" in line[0]
        assert "缺号" in line[0] and "未观测" in line[0]

    def test_summary_denominator_covers_every_checked_candidate(self, caplog):
        """分母是「参与校验的候选总数」：前两条连宽松都不过，也必须算进去。

        只数「严格拒掉的」会让拒绝率恒等于 100%——分母丢了，比例就没意义了。
        """
        p0 = FakeProvider("p0", result="牌灵今天心情不错，随便说两句。")
        p1 = FakeProvider("p1", result="【第1张·过去】只有一段，没有别的标记。")
        p2 = FakeProvider("p2", result=OK3)
        it = _interp([p0, p1, p2])
        with caplog.at_level(logging.INFO):
            assert _ask(it, ["过去", "现在", "未来"]) == OK3
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()]
        assert len(line) == 1
        assert "本次候选拒绝率 2/3 (66.67%)" in line[0]
        assert "不降级" in line[0] and "第 3 个候选" in line[0]

    def test_summary_reported_even_when_nothing_passes(self, caplog):
        """一条候选都不达标也要落汇总：这类占卜恰恰是收紧后最先降级的那批。"""
        prov = FakeProvider("p0", result="完全没有结构的一段话。")
        it = _interp([prov])
        with caplog.at_level(logging.INFO):
            assert _ask(it, ["过去", "现在"]) is None
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()]
        assert len(line) == 1 and "本次候选拒绝率 1/1 (100.00%)" in line[0] \
            and "降级为本地牌义" in line[0]

    def test_summary_is_not_written_when_nothing_was_checked(self, caplog):
        """没有候选经过校验（全部冷却/预算耗尽）时不落汇总：没有分母的空行只会刷日志。"""
        prov = FakeProvider("p0", result=OK3, exc=RuntimeError("boom"))
        it = _interp([prov])
        with caplog.at_level(logging.INFO):
            assert _ask(it, ["过去", "现在"]) is None
        assert not [r for r in caplog.records if "影子汇总" in r.getMessage()]

    def test_no_tally_in_loose_and_strict(self, caplog):
        """非影子档不计量：loose 没有「量」的任务，strict 的判定已经落实为行为了。"""
        single = "【第1张·过去】一段过去。【总结】结论。"
        for mode in (STRUCTURE_CHECK_LOOSE, STRUCTURE_CHECK_STRICT):
            prov = FakeProvider("p0", result=single)
            it = _interp([prov], structure_check=mode)
            with caplog.at_level(logging.INFO):
                assert _ask(it, ["过去"]) == single
            assert not [r for r in caplog.records if "影子汇总" in r.getMessage()], mode
            caplog.clear()

    def test_confirmed_degrade_when_chain_is_exhausted(self, caplog):
        """链已走完且无候选严格通过 → 判决是**确定降级**（这一支才敢给结论）。

        两个候选连宽松都不过（完全没有标记），严格口径面对的是同一批候选、同样失败，
        不存在「后面还有候选」这回事——观测即完整链，所以结论可以是确定的。
        """
        p0 = FakeProvider("p0", result="完全没有结构的一段话。")
        p1 = FakeProvider("p1", result="仍然没有结构。")
        it = _interp([p0, p1])
        with caplog.at_level(logging.INFO):
            assert _ask(it, ["过去", "现在"]) is None
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()]
        assert len(line) == 1
        assert "本次候选拒绝率 2/2 (100.00%)" in line[0]
        assert "降级为本地牌义" in line[0] and "整条可用候选链已观测完" in line[0]

    def test_unknown_branch_is_not_reported_as_degrade(self, caplog):
        """宽松放行后收尾 = 严格口径会走得更深：这一支必须报「未观测」，不许报降级。

        与上一条配对看：两条用例的候选都被严格拒，区别只在**链从哪个出口收尾**——
        出口不同，结论的确定性就不同。第二个指标要反映的是「完整候选链的最终降级
        结果」，所以这两种情形不能共用一句话。
        """
        prov = FakeProvider("p0", result=LEGACY_TWO_OF_THREE)
        it = _interp([prov])
        with caplog.at_level(logging.INFO):
            _ask(it, ["过去", "现在", "未来"])
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()]
        assert "未观测" in line[0] and "降级与否" in line[0]
        assert "降级为本地牌义" not in line[0]

    def test_cumulative_line_accumulates_across_readings(self, caplog):
        """累计读数要跨占卜累加：单签样本决定不了要不要收紧，看的是长期分布。"""
        it = _interp([FakeProvider("p0", result=OK3)])
        with caplog.at_level(logging.INFO):
            _ask(it, ["过去", "现在", "未来"])   # 判决：不降级
            _ask(it, ["过去", "现在", "未来"])
            caplog.clear()
            _ask(it, ["过去", "现在", "未来"])
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()]
        assert len(line) == 1
        assert "3 签——不降级 3" in line[0]
        assert "降级 0" in line[0] and "未观测 0" in line[0]
        assert "累计候选拒绝率 0/3 (0.00%)" in line[0]

    def test_summary_percentage_and_scope_are_stated(self, caplog):
        """单次与累计都要给出「分子/分母 (百分比)」，且累计要写明作用范围。"""
        # 第一个连宽松都不过（计数后继续），第二个严格通过：分母 2、分子 1 = 50%
        it = _interp([FakeProvider("p0", result="完全没有结构的一段话。"),
                      FakeProvider("p1", result=OK3)])
        with caplog.at_level(logging.INFO):
            _ask(it, ["过去", "现在", "未来"])
        line = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()][0]
        assert "本次候选拒绝率 1/2 (50.00%)" in line
        assert "累计候选拒绝率 1/2 (50.00%)" in line
        # 累计口径必须写在行里：实例级、热重载即清零、跨观察期要 Σ拒绝÷Σ候选
        assert "本解释器实例" in line and "热重载" in line
        assert "Σ拒绝÷Σ候选" in line and "勿平均各行百分比" in line

    def test_zero_denominator_reports_na_never_zero_percent(self):
        """分母为零写 N/A，不许写 0%：0/0 是「还没观测量」，不是「没被拒」。"""
        from interpret import _rate_text
        assert _rate_text(2, 50) == "2/50 (4.00%)"
        assert _rate_text(0, 3) == "0/3 (0.00%)"
        assert _rate_text(0, 0) == "0/0 (N/A)"
        assert "%" not in _rate_text(0, 0), "零分母不许给出百分比，哪怕是 0%"

    def test_cumulative_resets_with_the_interpreter_instance(self, caplog):
        """热重载＝新的解释器实例：累计从零开始，日志里的累计只覆盖自己这一代。

        这条正是「日志必须写明累计作用范围」的理由：观察期的总体拒绝率只能用
        各次观测的 Σ拒绝÷Σ候选 汇总，不能把两代实例的累计值拼起来当同一窗口。
        """
        with caplog.at_level(logging.INFO):
            _ask(_interp([FakeProvider("p0", result=LEGACY_TWO_OF_THREE)]),
                 ["过去", "现在", "未来"])
            first = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()][0]
            caplog.clear()
            _ask(_interp([FakeProvider("p0", result=OK3)]), ["过去", "现在", "未来"])
            second = [r.getMessage() for r in caplog.records if "影子汇总" in r.getMessage()][0]
        assert "1 签——" in first and "累计候选拒绝率 1/1 (100.00%)" in first
        assert "1 签——" in second, "新一代实例的累计必须从零起算，不继承上一代"
        assert "累计候选拒绝率 0/1 (0.00%)" in second


# ---------- 替身样本与真实口径对齐 ----------
class TestRepoFixturesMatchRealProtocol:
    """集成替身（test_core / stubs 里那些 fake_interp 返回值）也必须是合法解读。

    替身绕过真实校验，所以它写的形状不会被任何用例拦下——一旦它与 prompts 的格式协议
    漂移，「替身绿了、线上拒了」这类假绿就会长期潜伏。这里直接拿真实校验跑一遍替身样本。
    """

    FIXTURES = (
        ("stubs.OK_INTERP", OK_INTERP, 1),
        ("单张替身（命令入口/工具入口）", "【第1张·过去】一段过去。【总结】结论。", 1),
        ("三张替身（真实全链用例）", OK3, 3),
    )

    @pytest.mark.parametrize("label,text,n", FIXTURES)
    def test_fixture_passes_both_checks(self, label, text, n):
        assert validate_interpret_structure(text, n) is True, f"{label} 未过宽松口径"
        ok, why = validate_interpret_structure_strict(text, n)
        assert ok is True, f"{label} 未过严格口径：{why}"

    def test_legacy_two_of_three_fixture_is_the_known_bad_shape(self):
        """把历史那处错位钉成锚点：声明三张只写两张的替身，严格口径会拒。

        它宽松通过（【总结】计入段数），所以旧用例一直是绿的——这正是「先量再收紧」
        要防的那类盲区。
        """
        assert validate_interpret_structure(LEGACY_TWO_OF_THREE, 3) is True
        assert validate_interpret_structure_strict(LEGACY_TWO_OF_THREE, 3)[0] is False
