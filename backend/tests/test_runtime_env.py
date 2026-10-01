from app.runtime_env import (
    describe_environment_risks,
    hostile_node_options_reason,
    sanitize_environment,
)


def test_plain_node_options_are_left_alone() -> None:
    """只有会强加载外部代码的开关才算有害；`--max-old-space-size` 必须保留。"""
    assert hostile_node_options_reason("--max-old-space-size=4096") is None
    assert hostile_node_options_reason(None) is None
    assert hostile_node_options_reason("") is None


def test_require_style_node_options_are_flagged() -> None:
    reason = hostile_node_options_reason('--require="C:/Program Files/x/shim.cjs"')

    assert reason is not None
    assert "NODE_OPTIONS" in reason
    assert "The page needs to be reloaded" in reason


def test_sanitize_environment_removes_only_the_hostile_variable() -> None:
    environ = {
        "NODE_OPTIONS": "--require=/tmp/shim.cjs",
        "HTTPS_PROXY": "http://127.0.0.1:7890",
        "PATH": "/usr/bin",
    }

    removed = sanitize_environment(environ)

    assert [item.name for item in removed] == ["NODE_OPTIONS"]
    assert "--require" in removed[0].value
    assert "NODE_OPTIONS" not in environ
    assert environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert environ["PATH"] == "/usr/bin"


def test_sanitize_environment_is_idempotent() -> None:
    environ = {"NODE_OPTIONS": "--import=file:///tmp/shim.mjs"}

    assert len(sanitize_environment(environ)) == 1
    assert sanitize_environment(environ) == []


def test_sanitize_environment_touches_the_real_environ_by_default(monkeypatch) -> None:
    monkeypatch.setenv("NODE_OPTIONS", "--require=/tmp/shim.cjs")

    removed = sanitize_environment()

    assert [item.name for item in removed] == ["NODE_OPTIONS"]
    assert describe_environment_risks() == []


def test_describe_environment_risks_reports_what_is_still_there(monkeypatch) -> None:
    monkeypatch.setenv("NODE_OPTIONS", "--loader=/tmp/shim.mjs")

    risks = describe_environment_risks()

    assert len(risks) == 1
    assert risks[0]["name"] == "NODE_OPTIONS"
    assert "--loader" in risks[0]["value"]


def test_no_risk_when_the_variable_is_absent(monkeypatch) -> None:
    monkeypatch.delenv("NODE_OPTIONS", raising=False)

    assert describe_environment_risks() == []
