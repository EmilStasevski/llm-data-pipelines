from zj_llm import envcheck


def test_all_pinned_packages_installed() -> None:
    info = envcheck.check(gpu=False)
    missing = [name for name, version in info["packages"].items() if version is None]
    assert not missing, f"not installed: {missing}"
