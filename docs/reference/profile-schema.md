# Profile Schema Reference

Complete JSON Schema for `repokeeper.yml`.

## Top Level

```json
{
  "maintainer": "string (required)",
  "tone": { "...": "..." },
  "style": { "...": "..." },
  "pr": { "...": "..." },
  "tech": { "...": "..." },
  "notifications": { "...": "..." },
  "agent": { "...": "..." },
  "radar": { "...": "..." },
  "patrol": { "...": "..." }
}
```

## `tone`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `language` | string | No | `"en"` | `"en"`, `"zh"`, `"auto"` |
| `style` | string | No | `"friendly"` | `"friendly"`, `"formal"`, `"minimal"` |
| `emoji` | boolean | No | `true` | `true`, `false` |
| `closing` | string | No | `"happy to help"` | Any string |

## `style`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `code_style` | string | No | — | Free-form text |
| `testing` | string | No | `"pytest"` | `"pytest"`, `"unittest"`, `"jest"`, `"go test"`, `"none"` |
| `linting` | boolean | No | `true` | `true`, `false` |
| `formatting` | string | No | `"ruff"` | `"ruff"`, `"black"`, `"prettier"`, `"gofmt"`, `"none"` |

## `pr`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `min_tests` | boolean | No | `true` | `true`, `false` |
| `max_files_per_pr` | integer | No | `15` | 1–100 |
| `require_changelog` | boolean | No | `false` | `true`, `false` |
| `auto_merge` | boolean | No | `false` | `true`, `false` |
| `review_required` | boolean | No | `true` | `true`, `false` |

## `tech`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `preferred` | list[string] | No | `[]` | Any tech names |
| `avoid` | list[string] | No | `[]` | Any tech names |
| `target_python` | string | No | `"3.10"` | Python version string |
| `target_node` | string | No | `"20"` | Node.js version string |

## `notifications`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `email` | string | No | `""` | Email address |
| `telegram` | string | No | `""` | `chat_id:bot_token` |
| `wechat` | string | No | `""` | Webhook URL |
| `daily_summary` | boolean | No | `true` | `true`, `false` |
| `urgent_only` | boolean | No | `false` | `true`, `false` |

## `agent`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `model` | string | No | `"deepseek-chat"` | `"deepseek-chat"`, `"deepseek-reasoner"`, `"gpt-4o"`, `"gpt-4-turbo"` |
| `implement` | boolean | No | `true` | `true`, `false` |
| `max_context_files` | integer | No | `40` | 1–100 |
| `temperature` | float | No | `0.1` | 0.0–2.0 |
| `skip_keywords` | list[string] | No | `[]` | Any strings |

## `radar`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `enabled` | boolean | No | `true` | `true`, `false` |
| `keywords` | list[string] | No | `[]` | Any strings |
| `confidence_threshold` | float | No | `0.7` | 0.0–1.0 |
| `auto_create_issue` | boolean | No | `false` | `true`, `false` |

## `patrol`

| Key | Type | Required | Default | Valid Values |
|-----|------|----------|---------|--------------|
| `enabled` | boolean | No | `true` | `true`, `false` |
| `schedule` | string | No | `"0 8 * * 1-5"` | 5-field cron expression |
| `auto_upgrade_deps` | boolean | No | `true` | `true`, `false` |
| `stale_days` | integer | No | `90` | 1–365 |
| `ci_auto_fix` | boolean | No | `true` | `true`, `false` |
