import zj_eval


def test_package_importable() -> None:
    assert zj_eval.__version__ == "0.1.0"
