from __future__ import annotations

from repokeeper.profile import get_module_model, load_profile, validate_profile


def test_load_profile_merges_defaults_global_repo_and_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home_profile = home / ".repokeeper" / "global.yml"
    home_profile.parent.mkdir(parents=True)
    home_profile.write_text("tone:\n  style: formal\nagent:\n  model: deepseek-reasoner\n")
    repo_profile = tmp_path / "repo" / "repokeeper.yml"
    repo_profile.parent.mkdir()
    repo_profile.write_text("maintainer: alice\nradar:\n  confidence_threshold: 0.8\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("RKP_TONE_LANGUAGE", "zh")

    profile = load_profile(repo_profile)

    assert profile["maintainer"] == "alice"
    assert profile["tone"]["style"] == "formal"
    assert profile["tone"]["language"] == "zh"
    assert profile["agent"]["model"] == "deepseek-reasoner"
    assert profile["radar"]["confidence_threshold"] == 0.8


def test_validate_profile_reports_bad_values():
    issues = validate_profile(
        {
            "maintainer": "",
            "agent": {"model": 123},
            "tone": {"style": "loud"},
            "radar": {"confidence_threshold": 2},
            "patrol": {"schedule": "* * *", "stale_days": 0},
        }
    )

    assert "maintainer must be a non-empty string" in issues
    assert any("agent.model" in issue for issue in issues)  # 123 is not a string
    assert any("tone.style" in issue for issue in issues)
    assert any("confidence_threshold" in issue for issue in issues)
    assert any("schedule" in issue for issue in issues)
    assert any("stale_days" in issue for issue in issues)


def test_validate_profile_missing_maintainer():
    """Profile with missing maintainer key still reports issue."""
    issues = validate_profile({"agent": {"model": "deepseek-chat"}, "tone": {"style": "friendly"}})
    assert "maintainer must be a non-empty string" in issues


def test_validate_profile_maintainer_none():
    issues = validate_profile({"maintainer": None})
    assert "maintainer must be a non-empty string" in issues


def test_validate_profile_maintainer_non_string():
    issues = validate_profile({"maintainer": 123})
    assert "maintainer must be a non-empty string" in issues


def test_validate_profile_stale_days_not_int():
    issues = validate_profile({"patrol": {"stale_days": "ninety"}})
    assert any("stale_days" in issue for issue in issues)


def test_validate_profile_stale_days_zero():
    issues = validate_profile({"patrol": {"stale_days": 0}})
    assert any("stale_days" in issue for issue in issues)


def test_validate_profile_stale_days_negative():
    issues = validate_profile({"patrol": {"stale_days": -1}})
    assert any("stale_days" in issue for issue in issues)


def test_validate_profile_empty_schedule_is_ok():
    """Empty schedule should not produce validation error."""
    issues = validate_profile({
        "maintainer": "alice",
        "agent": {"model": "deepseek-chat"},
        "tone": {"style": "friendly"},
        "patrol": {"schedule": ""},
    })
    assert not any("schedule" in i for i in issues)


def test_load_yaml_corrupt_file(tmp_path):
    """_load_yaml returns empty dict for corrupt YAML."""
    from repokeeper.profile import _load_yaml
    bad_yaml = tmp_path / "bad.yml"
    bad_yaml.write_text(": :: : broken: [")
    result = _load_yaml(bad_yaml)
    assert result == {}


def test_load_yaml_scalar_not_dict(tmp_path):
    """_load_yaml returns empty dict when YAML parses to non-dict."""
    from repokeeper.profile import _load_yaml
    scalar_yaml = tmp_path / "scalar.yml"
    scalar_yaml.write_text("just a string")
    result = _load_yaml(scalar_yaml)
    assert result == {}


def test_load_yaml_nonexistent_file():
    """_load_yaml returns empty dict for nonexistent file."""
    from pathlib import Path

    from repokeeper.profile import _load_yaml
    result = _load_yaml(Path("/nonexistent/path/profile.yml"))
    assert result == {}


def test_apply_env_overrides_type_coercion(monkeypatch):
    """RKP_* env vars are coerced to proper types."""
    from repokeeper.profile import _apply_env_overrides
    monkeypatch.setenv("RKP_TONE_EMOJI", "false")
    monkeypatch.setenv("RKP_PATROL_DAYS", "365")
    monkeypatch.setenv("RKP_NUMERIC", "3.14")
    monkeypatch.setenv("RKP_BOOL_YES", "yes")
    monkeypatch.setenv("RKP_BOOL_1", "1")
    monkeypatch.setenv("RKP_BOOL_NO", "no")
    monkeypatch.setenv("RKP_BOOL_0", "0")

    profile = {"tone": {"emoji": True}, "patrol": {"days": 90}}
    profile = _apply_env_overrides(profile)

    assert profile["tone"]["emoji"] is False
    assert profile["patrol"]["days"] == 365
    assert profile["numeric"] == 3.14
    assert profile["bool"]["yes"] is True
    assert profile["bool"]["1"] is True
    assert profile["bool"]["no"] is False
    assert profile["bool"]["0"] is False


def test_apply_env_overrides_nested_new_path(monkeypatch):
    """RKP_ creates nested dicts for new keys."""
    from repokeeper.profile import _apply_env_overrides
    monkeypatch.setenv("RKP_NEW_SECTION_KEY", "hello")
    profile = {}
    profile = _apply_env_overrides(profile)
    assert profile["new"]["section"]["key"] == "hello"


def test_get_module_model_falls_back_to_agent():
    """When module.model is None, agent.model is used."""
    profile = {
        "agent": {"model": "deepseek-reasoner"},
        "labeler": {"model": None},
        "radar": {},
    }
    assert get_module_model(profile, "labeler") == "deepseek-reasoner"
    assert get_module_model(profile, "radar") == "deepseek-reasoner"
    assert get_module_model(profile, "patrol") == "deepseek-reasoner"


def test_get_module_model_per_module_override():
    """Per-module model takes precedence over agent.model."""
    profile = {
        "agent": {"model": "deepseek-chat"},
        "labeler": {"model": "qwen2.5-coder"},
        "review": {"model": "deepseek-reasoner"},
    }
    assert get_module_model(profile, "labeler") == "qwen2.5-coder"
    assert get_module_model(profile, "review") == "deepseek-reasoner"
    # radar has no override, falls back to agent.model
    assert get_module_model(profile, "radar") == "deepseek-chat"


def test_get_module_model_default_hardcoded():
    """When agent.model is missing, returns deepseek-chat."""
    profile: dict = {}
    assert get_module_model(profile, "labeler") == "deepseek-chat"


def test_validate_allows_unknown_models():
    """Unknown models like Ollama are allowed without validation error."""
    issues = validate_profile({
        "maintainer": "alice",
        "agent": {"model": "qwen2.5-coder"},
        "tone": {"style": "friendly"},
    })
    assert not any("agent.model" in i for i in issues)


def test_validate_rejects_non_string_model():
    """Non-string models are rejected."""
    issues = validate_profile({
        "maintainer": "alice",
        "agent": {"model": 42},
        "tone": {"style": "friendly"},
    })
    assert any("agent.model" in i for i in issues)
