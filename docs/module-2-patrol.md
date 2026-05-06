# Module 2: Daily Patrol 🔍

The Daily Patrol runs scheduled health checks on your repositories. It scans
for outdated dependencies, failed CI runs, stale issues, and produces a
health score with actionable recommendations.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Daily Patrol                          │
├────────────┬────────────┬────────────┬───────────────────┤
│  Dep       │  CI        │  Stale     │  Health           │
│  Scanner   │  Analyzer  │  Issues    │  Summary          │
│            │            │            │                   │
│  pip       │  GitHub    │  Find      │  Score 0-100      │
│  npm       │  Actions   │  issues    │  + warnings       │
│  go mod    │  API       │  >90 days  │  + suggestions    │
│  cargo     │  + AI diag │  + AI sum  │                   │
│  ...       │            │            │                   │
└────────────┴────────────┴────────────┴───────────────────┘
```

## Configuration

Add a `patrol` section to your `repokeeper.yml`:

```yaml
patrol:
  enabled: true
  schedule: "0 8 * * 1-5"
  auto_upgrade_deps: true
  stale_days: 90
  ci_auto_fix: true
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable the patrol |
| `schedule` | string | `"0 8 * * 1-5"` | Cron expression (UTC) |
| `auto_upgrade_deps` | bool | `true` | Auto-create PRs for outdated deps |
| `stale_days` | int | `90` | Days before issue is considered stale |
| `ci_auto_fix` | bool | `true` | Attempt automatic CI fixes |

## Features

### 1. Dependency Scanning

The patrol scans package manifests for outdated dependencies:

| Language | Manifest | Tool |
|----------|----------|------|
| Python | `requirements.txt`, `pyproject.toml`, `Pipfile` | `pip list --outdated` |
| Node.js | `package.json`, `yarn.lock` | `npm outdated` |
| Go | `go.mod` | `go list -u -m all` |
| Rust | `Cargo.toml` | `cargo outdated` |
| Ruby | `Gemfile` | `bundle outdated` |
| PHP | `composer.json` | `composer outdated` |
| Java (Maven) | `pom.xml` | `mvn versions:display-dependency-updates` |
| Java (Gradle) | `build.gradle`, `build.gradle.kts` | `gradle dependencyUpdates` |

Each outdated dependency is assigned a severity:

| Severity | Criteria | Health Deduction |
|----------|----------|-----------------|
| 🔴 `critical` | Security vulnerability, major version gap >2 | -10 |
| 🟠 `high` | Major version behind, breaking changes likely | -5 |
| 🟡 `medium` | Minor version behind | -2 |
| 🔵 `low` | Patch version behind | -1 |
| ⚪ `info` | Dependency deprecated or unmaintained | 0 |

#### Auto-Upgrade PR

When `auto_upgrade_deps` is `true`, RepoKeeper prepares dependency upgrade
report data. Automatic dependency file editing is still in progress, so the
current workflow reports outdated packages rather than safely rewriting lock
files.

```markdown
## 📦 Dependency Upgrades

RepoKeeper Patrol detected the following outdated dependencies:

| Package | Current | Latest | Severity |
|---------|---------|--------|----------|
| requests | 2.28.0 | 2.31.0 | medium |
| numpy | 1.24.0 | 2.0.0 | high |
| cryptography | 39.0.0 | 42.0.0 | critical |
```

### 2. CI Failure Analysis

The patrol checks recent GitHub Actions runs for failures. For each failure,
it uses AI to:

1. **Diagnose** the root cause (2-3 sentences)
2. **Suggest a fix** (concrete steps)
3. **Assess** whether the fix can be applied automatically

Example diagnosis:

```
Workflow: pytest
Failed at: 2024-04-30 08:15 UTC
Diagnosis: Test failure in test_upload.py:42 — the test expects a 201
           status code but gets 413. The mock server returns 200 instead of
           the expected 413 for payloads >10MB. The mock configuration in
           conftest.py line 89 needs updating.
Suggested Fix: Change the mock response in conftest.py:89 from
                status_code=200 to status_code=413 for upload_mock fixture.
Auto-fixable: ✅ Yes
```

When `ci_auto_fix` is enabled and the diagnosis is `auto_fixable: true`,
RepoKeeper reads the failing workflow file, calls the LLM to generate a
correction, creates a branch with the fix, and opens a CI fix PR.

### 3. Stale Issue Detection

The patrol identifies open issues that haven't been updated in `stale_days` days
(default: 90). For each stale issue, AI generates a one-sentence summary and
a suggested action:

| Action | When |
|--------|------|
| `close` | Issue is resolved, obsolete, or unactionable |
| `ping` | Needs a response from the reporter |
| `implement` | Still valid, should be prioritized |
| `ignore` | Known limitation, accepted behavior |

Stale issues appear in the daily health summary with their summary and age:

```markdown
## ⏰ Stale Issues

- [#142 Memory leak in worker pool](https://github.com/...) — stale 120d —
  Reporter hasn't provided requested reproduction steps. Suggest closing.
- [#88 Add dark mode](https://github.com/...) — stale 95d —
  Feature request with community support. Consider implementing.
```

### 4. Health Score

Each repository gets a health score (0-100), calculated from:

```
100 (perfect)
-10 per critical outdated dep
-5  per high-severity outdated dep
-2  per medium-severity outdated dep
-5  per CI failure
-3  per stale issue (>90 days)
-1  per stale issue (>30 days)
-5  per open issue without response >7 days
```

| Score | Label |
|-------|-------|
| 90–100 | 🟢 Healthy |
| 70–89 | 🟡 Needs Attention |
| 50–69 | 🟠 At Risk |
| 0–49 | 🔴 Critical |

### 5. Daily Health Summary

The patrol generates a markdown summary suitable for a GitHub issue, email,
or dashboard:

```markdown
# 📋 Daily Patrol Report — owner/repo

**Scanned:** 2024-04-30 08:00 UTC
**Health Score:** 78/100 🟡 Needs Attention

## 📦 Outdated Dependencies
_12 checked, 3 outdated_

| Package | Current | Latest | Severity |
|---------|---------|--------|----------|
| requests | 2.28.0 | 2.31.0 | medium |
| cryptography | 39.0.0 | 42.0.0 | high |

## ❌ CI Failures
### [pytest](https://github.com/...)
- **Failed:** 2024-04-30 08:15
- **Diagnosis:** Mock server returns wrong status code...
- **Suggested Fix:** Update conftest.py:89
- **Auto-fixable:** ✅ Yes

## ⏰ Stale Issues
- [#142 Memory leak](...) — stale 120d — Reporter has not replied. (suggested: ping)

## ⏳ Waiting for Maintainer Approval

RepoKeeper will not implement these candidates until a maintainer adds
`agent-todo` or comments `@repokeeper go`.

- Approve implementation for CI failure `pytest`.
- Review dependency `cryptography` upgrade (high).

---
*Generated by RepoKeeper Patrol*
```

When Patrol decides a stale issue's suggested action is `implement`, it adds
`repokeeper-candidate` and `repokeeper-patrol` labels plus a structured comment
to that issue. It never adds `agent-todo`; implementation remains an explicit
maintainer approval step.

## Workflow

The patrol runs daily at 8am UTC (weekdays), or you can trigger it manually:

```yaml
on:
  schedule:
    - cron: '0 8 * * 1-5'
  workflow_dispatch:
```

## API Reference

### `run_patrol(gh_client, llm_client, repo, profile=None, repo_path=None) → PatrolReport`

Run a complete patrol scan.

**Parameters:**
- `gh_client`: PyGithub `Github` instance
- `llm_client`: OpenAI-compatible client
- `repo`: Repository slug
- `profile`: Maintainer profile dict
- `repo_path`: Local path for dependency scanning (default: `"."`)

### `scan_dependencies(repo_path=Path(".")) → list[DepCheck]`

Scan all package manifests for outdated dependencies.

### `scan_ci_failures(gh_client, repo, since=None) → list[CIFailure]`

Find recent CI workflow run failures.

### `scan_stale_issues(gh_client, repo, stale_days=90, max_issues=30) → list[StaleIssue]`

Find open issues that are stale.

### `calculate_health(report: PatrolReport) → int`

Calculate the health score for a patrol report.

### `generate_health_summary(report: PatrolReport, profile: dict) → str`

Generate a markdown health summary.

## Data Models

### `DepCheck`

```python
@dataclass
class DepCheck:
    name: str               # Package name
    current: str            # Current version
    latest: str             # Latest available version
    is_outdated: bool
    severity: str           # critical | high | medium | low | info
    changelog_url: str
    breaking: bool          # Contains breaking changes
```

### `CIFailure`

```python
@dataclass
class CIFailure:
    workflow_name: str
    run_id: int
    run_url: str
    failed_at: datetime
    conclusion: str         # failure | cancelled | timed_out
    log_snippet: str
    diagnosis: str          # Populated by AI
    suggested_fix: str       # Populated by AI
    auto_fixable: bool       # Populated by AI
```

### `StaleIssue`

```python
@dataclass
class StaleIssue:
    number: int
    title: str
    url: str
    author: str
    created_at: datetime
    last_updated: datetime
    days_stale: int
    labels: list[str]
    summary: str            # Populated by AI
```

### `PatrolReport`

```python
@dataclass
class PatrolReport:
    repo: str
    scanned_at: datetime
    dependencies_checked: int
    outdated_deps: list[DepCheck]
    ci_failures: list[CIFailure]
    ci_fixed: list[str]         # Auto-fixes applied
    stale_issues: list[StaleIssue]
    health_score: int
    warnings: list[str]
```
