# Module 1: Community Radar 🔭

The Community Radar monitors GitHub issues and Discussions for keywords you
specify. When it finds a match, it uses AI to classify the post (bug, feature
request, question, or noise). With `auto_create_issue` enabled, it automatically
creates well-structured GitHub issues — complete with deduplication checks and
professional RepoKeeper branding — so you can focus on reviewing and deciding,
not on manual triage.

!!! note "Current scope"
    The Community Radar scans both GitHub Issues and Discussions.
    Discussion scanning requires a token with ``discussions:read`` scope
    and PyGithub >=2.1.  Auto-creating issues requires ``issues: write``
    permission in the workflow.

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                       Community Radar                             │
├───────────┬───────────┬───────────┬────────────┬─────────────────┤
│  Scanner  │  AI       │  Filter   │  Create /  │  Notifier       │
│           │  Classify │           │  Update    │                 │
│  GitHub   │  LLM      │  Remove   │  Auto      │  Email          │
│  Issues   │  sorts    │  noise &  │  create    │  Telegram       │
│  +        │  into:    │  low      │  issues    │  WeChat         │
│  Discus-  │  • bug    │  confi-   │  with      │  Work           │
│  sions    │  • feat   │  dence    │  dedup +   │                 │
│           │  • q      │           │  branding  │                 │
│           │  • noise  │           │            │                 │
└───────────┴───────────┴───────────┴────────────┴─────────────────┘
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
| `keywords` | list | `[]` | Keywords to watch for (case-insensitive). In cross-repo mode, keywords are optional — all matching results are included. |
| `confidence_threshold` | float | `0.7` | Minimum AI confidence to act (0.0–1.0) |
| `auto_create_issue` | bool | `false` | Auto-create issues (`true`) or draft for approval (`false`). When enabled, issues are created with the `repokeeper-radar` label, a header linking to the original discussion, and a branded footer. Requires `issues: write` workflow permission. |
| `cross_repo_search` | bool | `false` | Search **all of GitHub** for mentions of your project, not just issues in your own repo. See `[Cross-Repo Search](#cross-repo-search)`. |
| `cross_repo_query` | string | `""` | Custom GitHub search query for cross-repo mode. Defaults to `"<repo-name>"` (quoted). Supports full GitHub search syntax. |

## Cross-Repo Search

When `cross_repo_search: true`, the radar does **not** limit itself to issues
filed in your own repository. Instead, it searches **all of GitHub** using the
[GitHub Search API](https://docs.github.com/en/rest/search) (for issues) and
GraphQL (for discussions).

This is useful for discovering community discussions about your project that
happen in *other* people's repositories — for example, a bug report about your
library filed in a downstream project, or a discussion comparing your tool to
alternatives.

### How It Works

1. **Build search query** — by default, the radar searches for
   `"<project-name>" is:issue is:open -repo:owner/name` (issues) and
   `"<project-name>" type:discussion -repo:owner/name` (discussions).
   Your own repository is excluded to avoid re-discovering existing issues.

2. **Customize with `cross_repo_query`** — override the default query with
   full GitHub search syntax:
   ```yaml
   radar:
     cross_repo_search: true
     cross_repo_query: '"my-lib" OR "mylib"'
   ```

3. **Keyword filtering** — results are further filtered against
   `radar.keywords`. If keywords are empty, **all** results are included.

4. **AI classification + issue creation** — the same pipeline applies:
   classify with AI → filter by confidence → create issues in **your** repo.

### Example

For a project named `gitstats`:

```yaml
radar:
  enabled: true
  cross_repo_search: true
  keywords:
    - bug
    - crash
    - security
  confidence_threshold: 0.7
  auto_create_issue: false
```

This triggers:

- Issues search: `"gitstats" is:issue is:open -repo:shenxianpeng/gitstats`
- Discussions search: `"gitstats" type:discussion -repo:shenxianpeng/gitstats`

Any issue or discussion mentioning "gitstats" that also contains
"bug", "crash", or "security" will be classified by AI and a draft issue
created in the `gitstats` repository.

!!! tip "GitHub Search Syntax"
    `cross_repo_query` supports the full
    [GitHub search syntax](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests).
    You can use `OR`, `NOT`, `in:title`, `language:`, `label:`, etc.

!!! note "Discussion search"
    Cross-repo discussion search uses the GitHub GraphQL API, which is rate-limited
    separately from the REST API. The default `max_results` is 30 discussions per scan.

## How It Works

### 1. Scanning

The radar scans your repository's open issues for keyword matches. It checks:

- **Issue titles and bodies** — any keyword match triggers a hit
- **Discussions** — scanned via GitHub GraphQL API (token needs `discussions:read`)

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

### 6. Auto-Creation & Deduplication

When `auto_create_issue: true`, the Radar automatically creates a GitHub issue
for each actionable hit.

**Issue body structure:**

```markdown
> **Reported by @community_user** in [original discussion](URL)

## RepoKeeper Candidate

- **Source module:** Radar
- **Recommended action:** implement
- **Confidence:** 0.92
- **Original source:** URL
- **Diagnosis summary:** User reports a reproducible crash.
- **Suggested acceptance criteria:** Maintainer confirms this is actionable.

Maintainer approval required: add `agent-todo` or comment `/repokeeper go`
to request implementation.

[AI-generated structured description with sections:
 Description, Steps to Reproduce, Expected Behavior, Additional Context]

<!-- repokeeper-radar:https://github.com/owner/repo/discussions/123 -->
---
<sub>🤖 Created by [RepoKeeper](https://.../repokeeper) — AI-powered open source maintenance. [Learn more](...)</sub>
```

**Deduplication** prevents duplicate issues. Before creating, the Radar checks
for an existing issue by:

1. **Hidden marker match** — each created issue contains `<!-- repokeeper-radar:SOURCE_URL -->`
   in its body. The Radar searches existing `repokeeper-radar`-labeled issues
   for this exact marker.

2. **Title fallback** — if no marker matches, the Radar compares titles
   (case-insensitive, trimmed).

When a duplicate is found, the Radar adds a **comment** to the existing issue
noting renewed activity, rather than creating a redundant issue.

### 7. Branding

Every auto-created issue carries professional RepoKeeper branding:

| Element | Purpose |
|---------|---------|
| `repokeeper-candidate` label | Marks the issue as waiting for maintainer approval |
| `repokeeper-radar` label | Identifies issues created by RepoKeeper; powers deduplication |
| No `agent-todo` label | Prevents Radar from triggering automatic implementation |
| Header blockquote | Cites the original author and links to the source discussion |
| Candidate block | Records source module, recommended action, confidence, source, and acceptance criteria |
| Hidden marker | Enables reliable deduplication across scans |
| Branded footer | Links to the RepoKeeper project — organic promotion with every issue |

## Workflow

The radar GitHub Action runs every 3 hours on weekdays.

When you enable `auto_create_issue: true`, make sure the workflow has
`issues: write` permission:

```yaml
permissions:
  issues: write        # required for auto-create
  discussions: read
  contents: read
```

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

### `_create_radar_issue(gh_repo, hit, draft_body, labels) → dict`

Create a GitHub issue from a Radar hit. Applies the `repokeeper-radar` label,
wraps the body with header, hidden marker, and footer branding.

### `_find_existing_radar_issue(gh_repo, source_url, title) → Issue | None`

Find an existing issue for the same source URL. Matches by hidden marker first,
then by title similarity. Returns the Issue object or `None`.

### `_update_existing_radar_issue(issue_obj, hit) → dict`

Add an activity comment to an existing issue instead of creating a duplicate.

### `_build_radar_issue_body(draft_body, hit) → str`

Wrap an AI-generated draft body with the standard RepoKeeper header,
deduplication marker, and branded footer.

### `_radar_marker(source_url) → str` / `_extract_source_url_from_marker(body) → str | None`

Build and extract the hidden deduplication marker (``<!-- repokeeper-radar:URL -->``).

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
    issues_created: list[dict]     # {issue_number, issue_url, source_url}
    issues_updated: list[dict]     # {issue_number, issue_url, source_url, action}
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
  Created 5 new issues, updated 3 existing (duplicates)
  📧 Sent email to you@example.com
  📱 Sent Telegram alert
```
