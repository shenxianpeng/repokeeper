from __future__ import annotations

from pathlib import Path

import pytest

from repokeeper import cli


def test_cli_help_exits_success(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "AI-powered open source maintainer agent" in capsys.readouterr().out


def test_init_writes_profile_and_workflows(tmp_path):
    exit_code = cli.main(["init", str(tmp_path), "--workflows"])

    assert exit_code == 0
    assert (tmp_path / "repokeeper.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "repokeeper.yml").exists()


def test_init_refuses_to_overwrite_existing_profile(tmp_path, capsys):
    Path(tmp_path / "repokeeper.yml").write_text("maintainer: alice\n")

    exit_code = cli.main(["init", str(tmp_path)])

    assert exit_code == 1
    assert "already exists" in capsys.readouterr().err


def test_profile_validate_command(tmp_path, capsys):
    (tmp_path / "repokeeper.yml").write_text("maintainer: alice\n")

    exit_code = cli.main(["profile", "validate", "--profile", str(tmp_path / "repokeeper.yml")])

    assert exit_code == 0
    assert "Profile is valid" in capsys.readouterr().out


def test_health_command_prints_score(monkeypatch, capsys):
    report = type("Report", (), {"health_score": 91})()
    monkeypatch.setattr(cli, "_run_patrol_report", lambda args: report)

    exit_code = cli.main(["health", "--repo", "owner/repo"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "91"
