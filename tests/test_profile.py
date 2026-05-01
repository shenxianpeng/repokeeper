from __future__ import annotations

from repokeeper.profile import load_profile, validate_profile


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
            "agent": {"model": "unknown"},
            "tone": {"style": "loud"},
            "radar": {"confidence_threshold": 2},
            "patrol": {"schedule": "* * *", "stale_days": 0},
        }
    )

    assert "maintainer must be a non-empty string" in issues
    assert any("agent.model" in issue for issue in issues)
    assert any("tone.style" in issue for issue in issues)
    assert any("confidence_threshold" in issue for issue in issues)
    assert any("schedule" in issue for issue in issues)
    assert any("stale_days" in issue for issue in issues)
