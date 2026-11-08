import numpy as np

from zj_datagen import noise

TEXT = "Überhitzung am Getriebe, Maßnahme: Lager ersetzt. Kosten 1.200 €"


def test_mojibake_matches_plan_table_and_repairs_exactly() -> None:
    assert noise.MOJIBAKE == {
        "ä": "Ã¤", "ö": "Ã¶", "ü": "Ã¼", "ß": "ÃŸ", "Ä": "Ã„", "Ö": "Ã–", "Ü": "Ãœ", "€": "â‚¬",
    }  # fmt: skip
    broken = noise.mojibake(TEXT)
    assert "Ãœberhitzung" in broken
    assert "â‚¬" in broken
    assert noise.repair_mojibake(broken) == TEXT
    broken.encode("cp1252")  # mojibake text is cp1252-encodable


def test_transliterate() -> None:
    assert noise.transliterate("Größe Übertemperatur") == "Groesse Uebertemperatur"


def test_typo_changes_one_word_deterministically() -> None:
    a = noise.typo(TEXT, np.random.default_rng(1))
    b = noise.typo(TEXT, np.random.default_rng(1))
    assert a == b
    assert a != TEXT
    diff = [x for x, y in zip(a.split(" "), TEXT.split(" "), strict=True) if x != y]
    assert len(diff) == 1


def test_typo_without_candidates_is_identity() -> None:
    assert noise.typo("a b c", np.random.default_rng(0)) == "a b c"


def test_neighbours_symmetricish() -> None:
    assert set(noise.NEIGHBOURS["q"]) >= {"w", "a"}
    assert all(len(v) >= 2 for v in noise.NEIGHBOURS.values())


def test_whitespace_and_punctuation() -> None:
    assert (
        noise.drop_punctuation("Pumpe undicht, tropft. Lager ersetzt.")
        == "Pumpe undicht tropft Lager ersetzt"
    )
    out = noise.extra_whitespace("a b c", np.random.default_rng(3))
    assert out.split() == ["a", "b", "c"]
    assert out != "a b c"


def test_upper_keeps_micro_sign_cp1252_safe() -> None:
    out = noise.upper("Rauheit 0,8 µm, Maß ± 0,2")
    assert out == "RAUHEIT 0,8 µM, MASS ± 0,2"
    out.encode("cp1252")
    assert "µ".upper() == "\u039c"  # the Python behaviour this guards against
