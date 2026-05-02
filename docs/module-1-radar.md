# Module 1: Community Radar 🔭

The Community Radar monitors GitHub issues for keywords you
specify. When it finds a match, it uses AI to classify the post (bug, feature
request, question, or noise), generates a structured issue draft, and notifies
you for approval.

!!! note "Current scope"
    The current implementation scans GitHub issues. GitHub Discussions support
    is planned, but not active in this release.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Community Radar                       │
├───────────┬───────────┬───────────┬─────────────────────┤
│  Scanner  │  AI       │  Filter   │  Notifier           │
│           │  Classify │           │                     │
│  GitHub   │  LLM      │  Remove   │  Email              │
│  Issues   │  sorts    │  noise &  │  Telegram           │
│  +        │  into:    │  low      │  WeChat             │
│  Discus-  │  • bug    │  confi-   │  Work              │
│  sions    │  • feat   │  dence    │                     │
│           │  • q      │           │                     │
│           │  • noise  │           │                     │
└───────────┴───────────┴───────────┴─────────────────────┘
```

## Configuration

Add a `radar` section to your `repokeeper.yml`:

```yaml
radar:
  enabled: true
  keywords:
    - bug
    - crash
    - security
    - vulnerability
    - memory leak
    - feature request
    - performance
  confidence_threshold: 0.7
  auto_create_issue: false
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable the radar |
| `keywords` | list | `[]` | Keywords to watch for (case-insensitive) |
| `confidence_threshold` | float | `0.7` | Minimum AI confidence to act (0.0–1.0) |
| `auto_create_issue` | bool | `false` | Auto-create issues (`true`) or draft for approval (`false`) |

## How It Works

### 1. Scanning

The radar scans your repository's open issues for keyword matches. It checks:

- **Issue titles and bodies** — any keyword match triggers a hit
- **Discussions** — planned for a future release

Each hit records:
- Which keyword matched
- The post author, title, body, and URL
- When the post was created

### 2. AI Classification

Each hit is sent to the LLM with this prompt:

> *"Analyze this post and classify it as bug, feature_request, question, or noise.
> Assign a confidence score 0-1. Identify if action is needed."*

The AI returns:

| Field | Example |
|-------|---------|
| `category` | `bug` |
| `confidence` | `0.92` |
| `summary` | "User reports crash when uploading files > 10MB" |
| `suggested_title` | "Crash on large file upload (>10MB)" |
| `suggested_labels` | `["bug", "needs-repro"]` |
| `action_needed` | `true` |

### 3. Filtering

Hits below the `confidence_threshold` are discarded. "Noise" is always discarded.
Only clearly actionable bugs and feature requests pass through.

### 4. Issue Draft Generation

For each filtered hit, the AI generates a well-structured GitHub issue draft:

```markdown
## Description
[Clear description of the problem or request]

## Steps to Reproduce (for bugs)
1. ...
2. ...

## Expected Behavior
[What should happen]

## Additional Context
[Relevant logs, screenshots, etc.]
```

### 5. Notifications

Drafts are pushed to you via your configured notification channels:

| Channel | Configuration |
|---------|--------------|
| **Email** | Set `RKP_SMTP_HOST`, `RKP_SMTP_PORT`, `RKP_SMTP_USER`, `RKP_SMTP_PASS` |
| **Telegram** | Set `RKP_TELEGRAM_CHAT_ID` and `notifications.telegram` as `chat_id:bot_token` |
| **WeChat Work** | Set `notifications.wechat` to your webhook URL |

Set notification preferences in your profile:

```yaml
notifications:
  email: you@example.com
  telegram: "123456789:AAH...xyz"
  wechat: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
  daily_summary: true
  urgent_only: false
```

## Workflow

The radar GitHub Action runs every 3 hours on weekdays:

```yaml
on:
  schedule:
    - cron: '0 */3 * * 1-5'
  workflow_dispatch:  # manual trigger
```

You can also run it manually from the Actions tab.

## Advanced: Custom Classification Rules

The classification prompt can be customized by overriding the `CLASSIFIER_SYSTEM_PROMPT`
in `src/repokeeper/radar.py`. For example, to add a "documentation" category:

```python
CLASSIFIER_SYSTEM_PROMPT = """\
...
Classification rules:
- "bug": User reports broken behavior...
- "feature_request": User asks for new functionality...
- "documentation": User asks about docs, unclear usage...
- "question": User asks how to use something...
- "noise": Spam, off-topic...
"""
```

## API Reference

### `run_radar(gh_client, llm_client, repo, profile=None, since=None) → RadarReport`

Run a complete radar scan.

**Parameters:**
- `gh_client`: PyGithub `Github` instance
- `llm_client`: OpenAI-compatible client
- `repo`: Repository slug (`"owner/repo"`)
- `profile`: Maintainer profile dict (loaded if None)
- `since`: Only scan items updated after this datetime

**Returns:** `RadarReport` with `hits`, `bugs`, `feature_requests`, `noise` lists.

### `scan_issues(gh_client, repo, keywords, since=None, max_results=50) → list[RadarHit]`

Scan GitHub issues for keyword matches.

### `classify_hit(hit, llm_client, model="deepseek-chat") → RadarHit`

Classify a single RadarHit using AI. Returns the hit with `category`, `confidence`,
`summary` populated.

### `filter_hits(hits, confidence_threshold=0.7) → list[RadarHit]`

Filter out noise and low-confidence hits.

### `generate_issue_draft(hit, llm_client, profile) → dict`

Generate a structured issue draft from a classified hit.

### `notify_maintainer(profile, report) → dict[str, bool]`

Send notifications via configured channels.

## Data Models

### `RadarHit`

```python
@dataclass
class RadarHit:
    source: str               # "issue" | "discussion"
    repo: str                 # "owner/repo"
    number: int
    title: str
    body: str
    url: str
    author: str
    created_at: datetime
    matched_keyword: str

    # Populated by classify_hit()
    category: str             # "bug" | "feature_request" | "question" | "noise"
    confidence: float
    summary: str
    suggested_title: str
    suggested_labels: list[str]
    action_needed: bool
```

### `RadarReport`

```python
@dataclass
class RadarReport:
    repo: str
    scanned_at: datetime
    total_scanned: int
    hits: list[RadarHit]          # actionable (filtered)
    bugs: list[RadarHit]
    feature_requests: list[RadarHit]
    noise: list[RadarHit]         # discarded
```

## Example Output

```
🔭 Radar scanning shenxianpeng/mylib for keywords: ['bug', 'crash', 'security']
  Found 12 raw hits
  Classifying 12 hits with deepseek-chat...
  8 actionable after filtering (threshold=0.7)
    [bug]     0.92 | "Crash on large file upload (>10MB)"
    [bug]     0.88 | "Memory leak after 1000 iterations"
    [feature] 0.85 | "Add WebSocket support for real-time updates"
    [bug]     0.78 | "Security: SQL injection in search endpoint"
    [feature] 0.75 | "Dark mode support"
    [bug]     0.74 | "Race condition in concurrent requests"
    [feature] 0.72 | "Export to CSV format"
    [bug]     0.71 | "Token expiry not handled gracefully"
  📧 Sent email to you@example.com
  📱 Sent Telegram alert
```
