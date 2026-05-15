# Module 4: Maintainer Profile 👤

The Maintainer Profile is the **core configuration system** that drives all
RepoKeeper modules. It's a YAML file that describes your preferences as a
maintainer: code style, reply tone, PR acceptance standards, tech stack
preferences, notification channels, and module settings.

## Why a Profile?

Without a profile, an AI agent doesn't know:

- Your preferred coding style (tabs vs spaces, type hints, naming conventions)
- Your communication tone (formal vs casual, emoji usage, language)
- What kinds of PRs you accept (test requirements, max file changes)
- What technologies you prefer (or want to avoid)
- Where to send notifications

The profile gives RepoKeeper a personality — **your** personality.

## Profile Layering

RepoKeeper merges settings from four layers. Later layers override earlier ones:

```
┌──────────────────────────────────┐
│ 4. Environment variables (RKP_*) │  ← Highest priority (CI/secrets)
├──────────────────────────────────┤
│ 3. repokeeper.yml (per-repo)     │  ← Per-project overrides
├──────────────────────────────────┤
│ 2. ~/.repokeeper/global.yml      │  ← Your global preferences
├──────────────────────────────────┤
│ 1. Built-in defaults             │  ← Always present
└──────────────────────────────────┘
```

### Example Layering

**Global** (`~/.repokeeper/global.yml`):
```yaml
agent:
  model: deepseek-chat
style:
  code_style: "Use type hints, keep functions small."
```

**Per-repo** (`myproject/repokeeper.yml`):
```yaml
agent:
  model: deepseek-reasoner  # Override: this project needs more reasoning
tech:
  preferred: [python, fastapi]
```

**Environment** (`RKP_TONE_LANGUAGE=zh`):
```bash
# Override language for a specific CI run
```

**Result** (merged):
```yaml
agent:
  model: deepseek-reasoner       # ← from per-repo
style:
  code_style: "Use type hints..." # ← from global
tech:
  preferred: [python, fastapi]   # ← from per-repo
tone:
  language: zh                   # ← from env var
```

## Full Schema

### Top-Level Fields

```yaml
maintainer: string     # Required. Your GitHub username.
```

### `tone` — Communication Preferences

Controls how RepoKeeper communicates in comments, PRs, and notifications.

```yaml
tone:
  language: en          # en | zh | auto
  style: friendly       # friendly | formal | minimal
  emoji: true           # Allow emoji in replies
  closing: "happy to help"  # Custom closing phrase
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `language` | string | `en` | Language for generated text (`en`, `zh`, `auto`) |
| `style` | string | `friendly` | Tone of voice |
| `emoji` | bool | `true` | Whether to use emoji in messages |
| `closing` | string | `"happy to help"` | Custom sign-off phrase |

### `style` — Code Style

Instructions passed to the LLM when generating code.

```yaml
style:
  code_style: |
    Follow existing code style exactly.
    Use type hints in Python.
    Keep functions small and focused.
    No unnecessary blank lines or extra comments.
  testing: pytest        # pytest | unittest | jest | go test | none
  linting: true          # Run linter after changes
  formatting: ruff       # ruff | black | prettier | gofmt | none
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `code_style` | string | — | Free-form instructions for the LLM |
| `testing` | string | `pytest` | Testing framework to use |
| `linting` | bool | `true` | Run linter after generating code |
| `formatting` | string | `ruff` | Code formatter to use |

!!! tip "Writing effective code style"
    Be specific. Instead of "write good code", say:
    ```
    Use type hints on all function signatures.
    Functions should be < 50 lines.
    Prefer dataclasses over plain classes.
    Import order: stdlib → third-party → local.
    ```

### `pr` — PR Acceptance Standards

Controls what PRs the agent is allowed to submit and validation rules.

```yaml
pr:
  min_tests: true          # Require tests for new code
  max_files_per_pr: 15     # Reject PRs touching too many files
  require_changelog: false # Enforce changelog entry
  auto_merge: false        # Auto-merge after CI passes (dangerous!)
  review_required: true    # Always require human review
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_tests` | bool | `true` | Require test files in PRs |
| `max_files_per_pr` | int | `15` | Max files a PR can touch |
| `require_changelog` | bool | `false` | Enforce changelog updates |
| `auto_merge` | bool | `false` | ⚠️ Auto-merge after CI passes |
| `review_required` | bool | `true` | Require human review before merge |

!!! warning "`auto_merge: true`"
    This is dangerous. Only enable on repos with very thorough CI and test
    coverage. The agent can make mistakes.

### `tech` — Tech Stack Preferences

Tells the LLM which technologies you prefer and which to avoid.

```yaml
tech:
  preferred:
    - python
    - fastapi
    - postgresql
    - redis
  avoid:
    - jquery
    - php
    - mongodb
  target_python: "3.10"
  target_node: "20"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `preferred` | list | `[]` | Preferred tech stack |
| `avoid` | list | `[]` | Tech to avoid |
| `target_python` | string | `"3.10"` | Minimum Python version |
| `target_node` | string | `"20"` | Minimum Node.js version |

### `notifications` — Alert Channels

Where RepoKeeper sends alerts.

```yaml
notifications:
  email: you@example.com
  telegram: "123456789:AAH...xyz"
  wechat: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
  daily_summary: true
  urgent_only: false
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `email` | string | `""` | Email for alerts |
| `telegram` | string | `""` | `chat_id:bot_token` format |
| `wechat` | string | `""` | WeChat Work webhook URL |
| `daily_summary` | bool | `true` | Send daily health summary |
| `urgent_only` | bool | `false` | Only notify for critical items |

### `agent` — Agent Behavior

Controls how the Implementation Agent operates.

```yaml
agent:
  model: deepseek-chat
  implement: true
  max_context_files: 40
  temperature: 0.1
  skip_keywords:
    - "needs design"
    - "breaking change"
    - "RFC required"
  verify_commands:
    - ruff check .
    - pytest tests
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | `deepseek-chat` | LLM model |
| `implement` | bool | `true` | Enable automatic implementation |
| `max_context_files` | int | `60` | Max files in LLM context |
| `max_context_tokens` | int/null | `null` | Token budget for context |
| `temperature` | float | `0.1` | LLM temperature |
| `skip_keywords` | list | `[]` | Phrases that trigger auto-skip |
| `similar_issue_check` | bool | `true` | Scan for duplicate issues before implementing |
| `verify_commands` | list/bool | auto-detect | Commands required before PR creation; set `false` to disable |

### `radar` — Community Radar Settings

```yaml
radar:
  enabled: true
  keywords:
    - bug
    - crash
    - security
  confidence_threshold: 0.7
  auto_create_issue: false
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable the radar |
| `keywords` | list | `[]` | Keywords to watch for. Optional in cross-repo mode. |
| `confidence_threshold` | float | `0.7` | Min AI confidence (0–1) |
| `auto_create_issue` | bool | `false` | Auto-create issues |
| `cross_repo_search` | bool | `false` | Search all of GitHub for mentions |
| `cross_repo_query` | string | `""` | Custom search query (default: repo name) |

### `patrol` — Daily Patrol Settings

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
| `enabled` | bool | `true` | Enable the patrol |
| `schedule` | string | `"0 8 * * 1-5"` | Cron schedule |
| `auto_upgrade_deps` | bool | `true` | Include dependency upgrade candidates |
| `stale_days` | int | `90` | Stale issue threshold |
| `ci_auto_fix` | bool | `true` | Record auto-fixable CI candidates |

### `labeler` — Auto-Labeler Settings

```yaml
labeler:
  enabled: true
  model: null
  mode: add
  confidence_threshold: 0.7
  max_labels: 3
  allow_create_labels: true
  exclude_labels:
    - repokeeper-labeler
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable the auto-labeler |
| `model` | string/null | `null` | Per-module model (null = inherit `agent.model`) |
| `mode` | string | `add` | `add` (apply directly) or `suggest` (post comment) |
| `confidence_threshold` | float | `0.7` | Minimum AI confidence to apply labels |
| `max_labels` | int | `3` | Max labels per issue/PR |
| `allow_create_labels` | bool | `true` | Allow creating new labels with descriptions |
| `exclude_labels` | list | `[""]` | Labels to ignore when finding unlabeled issues |

### `review` — Code Review Settings

```yaml
review:
  model: deepseek-reasoner
  describe_on_open: false
  incremental: true
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string/null | `null` | Per-module model (null = inherit `agent.model`) |
| `describe_on_open` | bool | `false` | Auto-generate PR description on `pull_request.opened` |
| `incremental` | bool | `true` | Re-review when new commits are pushed (`pull_request.synchronize`) |

### `release` — Release Note Settings

```yaml
release:
  enabled: true
  model: null
  temperature: 0.1
  audience: users and maintainers
  categories:
    - Breaking Changes
    - Features
    - Fixes
    - Documentation
    - Maintenance
  include_prereleases: false
  prerelease: false
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable draft release note generation |
| `model` | string/null | `null` | Per-module model (null = inherit `agent.model`) |
| `temperature` | float | `0.1` | LLM temperature |
| `audience` | string | `users and maintainers` | Target audience for notes |
| `categories` | list | see example | Preferred release note section order |
| `include_prereleases` | bool | `false` | Consider prereleases when finding the previous release |
| `prerelease` | bool | `false` | Mark the GitHub draft release as a prerelease |

## Environment Variable Overrides

Any profile field can be overridden with an environment variable using the
`RKP_` prefix and dot-notation converted to underscores:

| Profile Path | Env Var |
|-------------|---------|
| `tone.language` | `RKP_TONE_LANGUAGE=zh` |
| `agent.model` | `RKP_AGENT_MODEL=deepseek-reasoner` |
| `notifications.email` | `RKP_NOTIFICATIONS_EMAIL=me@example.com` |
| `radar.confidence_threshold` | `RKP_RADAR_CONFIDENCE_THRESHOLD=0.8` |
| `patrol.stale_days` | `RKP_PATROL_STALE_DAYS=60` |
| `release.audience` | `RKP_RELEASE_AUDIENCE=maintainers` |

Values are automatically type-coerced:
- `"true"`, `"yes"`, `"1"` → `True`
- `"false"`, `"no"`, `"0"` → `False`
- `"42"` → `42`
- `"0.8"` → `0.8`
- Everything else → `str`

## API Reference

### `load_profile(profile_path=None) → dict`

Load the fully merged maintainer profile.

```python
from repokeeper.profile import load_profile

profile = load_profile()
print(profile["agent"]["model"])     # "deepseek-chat"
print(profile["tone"]["language"])   # "en"
```

### `validate_profile(profile) → list[str]`

Validate a profile and return issues.

```python
from repokeeper.profile import validate_profile

issues = validate_profile(profile)
if issues:
    for issue in issues:
        print(f"❌ {issue}")
```

### `generate_profile_template(path="repokeeper.yml")`

Generate a commented template file for a new repo.

```python
from repokeeper.profile import generate_profile_template

generate_profile_template("my-repo/repokeeper.yml")
```

## Complete Example

```yaml
# repokeeper.yml — Complete example

maintainer: shenxianpeng

tone:
  language: en
  style: friendly
  emoji: true
  closing: "happy to help"

style:
  code_style: |
    Follow existing code style exactly.
    Use type hints in Python.
    Keep functions small (< 50 lines).
    Prefer dataclasses.
    Import order: stdlib → third-party → local.
  testing: pytest
  linting: true
  formatting: ruff

pr:
  min_tests: true
  max_files_per_pr: 10
  require_changelog: false
  auto_merge: false
  review_required: true

tech:
  preferred:
    - python
    - fastapi
    - postgresql
  avoid:
    - jquery
    - mongodb
  target_python: "3.10"
  target_node: "20"

notifications:
  email: maintainer@example.com
  telegram: ""
  wechat: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
  daily_summary: true
  urgent_only: false

agent:
  model: deepseek-chat
  implement: true
  max_context_files: 40
  temperature: 0.1
  skip_keywords:
    - "needs design"
    - "breaking change"
    - "security audit"
  similar_issue_check: true

radar:
  enabled: true
  keywords:
    - bug
    - crash
    - security
    - vulnerability
    - performance
  confidence_threshold: 0.7
  auto_create_issue: false
  cross_repo_search: false
  cross_repo_query: ""

patrol:
  enabled: true
  schedule: "0 8 * * 1-5"
  auto_upgrade_deps: true
  stale_days: 90
  ci_auto_fix: true

labeler:
  enabled: true
  mode: add
  confidence_threshold: 0.7
  max_labels: 3
  allow_create_labels: true

review:
  model: null
  describe_on_open: false
  incremental: true

release:
  enabled: true
  model: null
  temperature: 0.1
  audience: users and maintainers
  include_prereleases: false
  prerelease: false
```
