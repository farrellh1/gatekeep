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
