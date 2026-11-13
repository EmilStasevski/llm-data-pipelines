"""WINNER.md logic: Pareto front and the production rule (PLAN §11.1 rule 5)."""

from __future__ import annotations

from zj_analysis import report, winner


def _e(c: str, wall: float, e: float, services: list[str]) -> winner.Entry:
    s = report.Summary(c, "L", 1000, False, 3, wall, wall, wall, 0.0, e, e, 0.0, False)
    return winner.Entry(s, True, 0.8, services)


def test_pareto_front() -> None:
    es = [_e("a", 10, 100, []), _e("b", 20, 50, []), _e("c", 30, 200, []), _e("d", 10, 120, [])]
    assert [x.s.contestant for x in winner.pareto(es)] == ["a", "b"]


def test_production_rule_flags_energy_and_services() -> None:
    ok = winner.production_md([_e("fast", 10, 140, ["LLM"]), _e("slow", 20, 100, ["LLM"])], "en")
    assert "**`fast`**" in "\n".join(ok)
    assert "no trade-off needed" in "\n".join(ok)
    hungry = winner.production_md([_e("fast", 10, 160, ["LLM"]), _e("slow", 20, 100, ["LLM"])], "en")
    assert "decision needed" in "\n".join(hungry)  # 1.6 × the most efficient
    heavy = winner.production_md(
        [_e("fast", 10, 100, ["DB", "Embed", "LLM", "Queue"]), _e("slow", 20, 100, ["LLM"])], "en"
    )
    assert "decision needed" in "\n".join(heavy)  # 3 more services


def test_stack_compared_with_candidate_not_ranked() -> None:
    s = report.Summary("e06_stack_go", "L", 1000, False, 3, 99, 99, 99, 0.0, 110, 110, 0.0, False)
    stack = winner.Entry(s, True, 0.75, [])
    md = "\n".join(winner.stack_md([stack], _e("e03", 100, 100, []), "en"))
    assert "`e06_stack_go` | `e03`" in md
    assert "-1.0 %" in md
    assert "+10.0 %" in md
    assert "e06_stack_go" in report.PRODUCTION_STACKS
    assert "No counted run" in "\n".join(winner.stack_md([], None, "en"))
