import xml.etree.ElementTree as ET

from zj_analysis.figures import Pt, front, lin_ticks, svg_lines, svg_scatter, svg_stacked


def test_front_minimizes_both_axes() -> None:
    pts = [Pt("a", 1, 10, ""), Pt("b", 2, 5, ""), Pt("c", 3, 6, ""), Pt("d", 0.5, 20, "")]
    assert front(pts) == {"a", "b", "d"}  # c is beaten by b on both axes


def test_front_maximize_y() -> None:
    pts = [Pt("cheap", 1, 0.8, ""), Pt("good", 10, 0.97, ""), Pt("worse", 20, 0.9, "")]
    assert front(pts, maximize_y=True) == {"cheap", "good"}


def test_lin_ticks_are_round() -> None:
    assert lin_ticks(0.7654, 0.98) == [0.8, 0.85, 0.9, 0.95]


def test_svgs_are_wellformed() -> None:
    pts = [Pt("x<1>", 1, 2, "tip & more"), Pt("y", 3, 1, "t"), Pt("z", 5, 5, "t")]
    for svg in (
        svg_scatter("T", pts, "x", "y"),
        svg_stacked("S", ["a", "b"], ["s1", "s2"], [[1.0, 2.0], [3.0, 0.0]], "de"),
        svg_lines("L", [("s", [(1.0, 10.0), (2.0, 20.0)])], [(15.0, "SLO")], "x", "y"),
    ):
        ET.fromstring(svg)


def test_skills_classify() -> None:
    from zj_analysis.skills import GROUPS, classify  # noqa: PLC0415

    names = {
        GROUPS[i][0].split(" ")[0] for i in classify("Werkstudent Data Science mit Python | Data Scientist")
    }
    assert names == {"Daten"}
    assert classify("Servicekraft Systemgastronomie | Kellner/in") == [
        len(GROUPS) - 1
    ]  # no IT false positive
    assert classify("NTT DATA: Social Media Creator | Content Creator") == [7]  # company name ignored
