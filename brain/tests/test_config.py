import logging

from core.config import RepoConfig, load_config


def test_defaults_are_read_only():
    c = RepoConfig()
    assert c.mode == "suggest-only"
    assert c.threshold == 0.85
    assert c.is_check_enabled("cosmetic_only") is True


def test_load_from_yaml_overrides():
    c = load_config("mode: auto-gate\nthreshold: 0.9\nchecks:\n  cosmetic_only: false\n")
    assert c.mode == "auto-gate"
    assert c.threshold == 0.9
    assert c.is_check_enabled("cosmetic_only") is False


def test_missing_yaml_returns_defaults():
    c = load_config(None)
    assert c.mode == "suggest-only"


def test_unknown_check_key_warns_but_still_runs(caplog):
    # `ci_status_check` names no registered check (the real one is `ci_status`).
    with caplog.at_level(logging.WARNING, logger="gatekeep"):
        c = load_config("checks:\n  ci_status_check: false\n  cosmetic_only: false\n")
    # The warning is visible and names the offending key.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a warning for the unknown check key"
    assert any("ci_status_check" in r.getMessage() for r in warnings)
    # It warned but did not raise: the valid checks still take effect.
    assert c.is_check_enabled("cosmetic_only") is False
    assert c.is_check_enabled("ci_status") is True


def test_all_valid_check_keys_emit_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="gatekeep"):
        c = load_config("checks:\n  cosmetic_only: false\n  ci_status: true\n")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert c.is_check_enabled("cosmetic_only") is False
