from zjmark import envinfo


def test_versions_reports_missing_as_none() -> None:
    v = envinfo.versions(["pytest", "definitely-not-installed-zj"])
    assert v["pytest"] is not None
    assert v["definitely-not-installed-zj"] is None


def test_fingerprint_is_stable_and_order_independent() -> None:
    a = envinfo.fingerprint(["pytest", "zjmark"])
    b = envinfo.fingerprint(["zjmark", "pytest", "pytest"])
    assert a["sha256"] == b["sha256"]
    assert a["packages"] == {"pytest": b["packages"]["pytest"], "zjmark": "0.1.0"}


def test_extra_changes_fingerprint() -> None:
    a = envinfo.fingerprint(["pytest"], {"env": "a"})
    b = envinfo.fingerprint(["pytest"], {"env": "b"})
    assert a["extra"] == {"env": "a"}
    assert a["sha256"] != b["sha256"]
