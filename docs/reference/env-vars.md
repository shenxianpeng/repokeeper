# Environment Variables

RepoKeeper can be configured through environment variables, which override
profile settings at runtime.

## Required Secrets (GitHub Actions)

| Variable | Purpose | Required |
|----------|---------|----------|
| `DEEPSEEK_API_KEY` | DeepSeek API key for AI features | Yes |
| `GITHUB_TOKEN` | GitHub API access (auto-provided by Actions) | Yes |
| `REPOKEEPER_GITHUB_TOKEN` | Optional PAT used before `GITHUB_TOKEN` for PR creation | No |
| `GITHUB_REPOSITORY` | Repository slug `owner/repo` (auto-provided) | Yes |
| `ISSUE_NUMBER` | Issue number for Implementation Agent | For Agent only |

## Profile Overrides

Any profile key can be overridden using `RKP_` prefix with dot-separated paths
converted to uppercase underscore format.

### Syntax

```
Profile: agent.model = "deepseek-chat"
Env var: RKP_AGENT_MODEL=deepseek-reasoner
```

### All Supported Overrides

| Profile Path | Environment Variable | Type |
|-------------|---------------------|------|
| `maintainer` | `RKP_MAINTAINER` | string |
| `tone.language` | `RKP_TONE_LANGUAGE` | string |
| `tone.style` | `RKP_TONE_STYLE` | string |
| `tone.emoji` | `RKP_TONE_EMOJI` | bool |
| `tone.closing` | `RKP_TONE_CLOSING` | string |
| `style.code_style` | `RKP_STYLE_CODE_STYLE` | string |
| `style.testing` | `RKP_STYLE_TESTING` | string |
| `style.linting` | `RKP_STYLE_LINTING` | bool |
| `style.formatting` | `RKP_STYLE_FORMATTING` | string |
| `pr.min_tests` | `RKP_PR_MIN_TESTS` | bool |
| `pr.max_files_per_pr` | `RKP_PR_MAX_FILES_PER_PR` | int |
| `pr.require_changelog` | `RKP_PR_REQUIRE_CHANGELOG` | bool |
| `pr.auto_merge` | `RKP_PR_AUTO_MERGE` | bool |
| `pr.review_required` | `RKP_PR_REVIEW_REQUIRED` | bool |
| `tech.target_python` | `RKP_TECH_TARGET_PYTHON` | string |
| `tech.target_node` | `RKP_TECH_TARGET_NODE` | string |
| `notifications.email` | `RKP_NOTIFICATIONS_EMAIL` | string |
| `notifications.telegram` | `RKP_NOTIFICATIONS_TELEGRAM` | string |
| `notifications.wechat` | `RKP_NOTIFICATIONS_WECHAT` | string |
| `notifications.daily_summary` | `RKP_NOTIFICATIONS_DAILY_SUMMARY` | bool |
| `notifications.urgent_only` | `RKP_NOTIFICATIONS_URGENT_ONLY` | bool |
| `agent.model` | `RKP_AGENT_MODEL` | string |
| `agent.implement` | `RKP_AGENT_IMPLEMENT` | bool |
| `agent.max_context_files` | `RKP_AGENT_MAX_CONTEXT_FILES` | int |
| `agent.temperature` | `RKP_AGENT_TEMPERATURE` | float |
| `radar.enabled` | `RKP_RADAR_ENABLED` | bool |
| `radar.confidence_threshold` | `RKP_RADAR_CONFIDENCE_THRESHOLD` | float |
| `radar.auto_create_issue` | `RKP_RADAR_AUTO_CREATE_ISSUE` | bool |
| `patrol.enabled` | `RKP_PATROL_ENABLED` | bool |
| `patrol.schedule` | `RKP_PATROL_SCHEDULE` | string |
| `patrol.auto_upgrade_deps` | `RKP_PATROL_AUTO_UPGRADE_DEPS` | bool |
| `patrol.stale_days` | `RKP_PATROL_STALE_DAYS` | int |
| `patrol.ci_auto_fix` | `RKP_PATROL_CI_AUTO_FIX` | bool |

## Notification Infrastructure

### Email (SMTP)

| Variable | Default | Description |
|----------|---------|-------------|
| `RKP_SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `RKP_SMTP_PORT` | `587` | SMTP port |
| `RKP_SMTP_USER` | — | SMTP username |
| `RKP_SMTP_PASS` | — | SMTP password |

### Telegram

| Variable | Default | Description |
|----------|---------|-------------|
| `RKP_TELEGRAM_CHAT_ID` | — | Telegram chat ID |

The `notifications.telegram` profile value should be in `chat_id:bot_token` format.
Alternatively, use `RKP_TELEGRAM_CHAT_ID` and put the bot token in the profile.

## LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | — | DeepSeek API key |
| `OPENAI_API_KEY` | — | Alternative: OpenAI API key |
| `LLM_BASE_URL` | `https://api.deepseek.com` | LLM API base URL |

## Example: GitHub Actions Secrets

```yaml
# .github/workflows/repokeeper.yml
env:
  DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
  REPOKEEPER_GITHUB_TOKEN: ${{ secrets.REPOKEEPER_GITHUB_TOKEN || secrets.GITHUB_TOKEN }}
  # Override profile settings
  RKP_AGENT_MODEL: ${{ secrets.RKP_AGENT_MODEL || 'deepseek-chat' }}
  RKP_RADAR_CONFIDENCE_THRESHOLD: '0.8'
  RKP_NOTIFICATIONS_EMAIL: ${{ secrets.MY_EMAIL }}
```
