# Module 3: Implementation Agent 🤖

The Implementation Agent reads GitHub issues, understands your codebase,
generates an implementation plan using AI, and opens a pull request for your
review.

## How to Trigger

There are two ways to invoke the agent:

### Method 1: Label

Add the `agent-todo` label to any issue:

```
Issue → Labels → agent-todo
```

### Method 2: Comment

Comment `/repokeeper go` on an issue (must be a repo collaborator):

```
/repokeeper go
```

The agent responds immediately with an acknowledgment comment and begins
working.

RepoKeeper discovery modules may add `repokeeper-candidate`,
`repokeeper-radar`, or `repokeeper-patrol` labels to issues. Those labels are
handoff context only; they do not trigger implementation. A maintainer must
still add `agent-todo` or comment `/repokeeper go`.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│              Implementation Agent                     │
├──────────┬──────────┬──────────┬─────────────────────┤
│  Trigger │  Context │  LLM     │  Git + PR           │
│          │          │          │                     │
│  label:  │  Read    │  Read    │  Create branch      │
│  agent-  │  repo    │  issue   │  Apply changes      │
│  todo    │  files   │  + code  │  Push to remote     │
│          │  (60 max)│  + style │  Open PR            │
│  comment:│          │  → JSON  │  Post comment       │
│  @repo-  │          │  plan    │  with PR link       │
│  keeper  │          │          │                     │
│  go      │          │          │                     │
└──────────┴──────────┴──────────┴─────────────────────┘
```

## What Happens Step by Step

### Step 1: Trigger Detection

The GitHub Action listens for:
- Issue labeled `agent-todo`
- Comment `/repokeeper go` from a collaborator

The workflow also checks access: only `OWNER`, `MEMBER`, or `COLLABORATOR` can
trigger via comment.

For PR creation, the default `GITHUB_TOKEN` needs repository Actions permission
to create pull requests. If your repository or organization disables that
permission, set `REPOKEEPER_GITHUB_TOKEN` to a token with contents and pull
request write access.

### Step 2: Skip Checks

Before doing anything expensive, the agent checks:

1. **Profile `agent.implement`**: If `false`, skips immediately.
2. **Skip keywords**: If the issue contains any phrase from `agent.skip_keywords`,
   the agent skips with an explanation.
3. **Similar issue detection**: If `agent.similar_issue_check` is `true`
   (default), the agent scans open issues for keyword overlap. When similar
   issues are found, it posts a comment with links and skips implementation.

Example skip keyword match:

```yaml
agent:
  skip_keywords:
    - "needs design"
    - "breaking change"
    - "RFC required"
```

If the issue title is *"RFC: New event system"*, the agent skips because
`"RFC"` implies design work.

### Step 3: Codebase Context Collection

The agent uses a two-step context engine by default:

1. List repository files with metadata: file kind, size, local dependency hints,
   and likely related tests.
2. Ask the LLM to select the files most relevant to the issue.
3. Expand that selection with nearby tests and local dependencies, while
   respecting `agent.max_context_files` and `agent.max_context_tokens`.

When `agent.smart_file_selection: false`, RepoKeeper falls back to direct
collection and collects up to `agent.max_context_files` source files
(default: 60), prioritizing:

1. Config files (`.yml`, `.toml`, `.cfg`, `.ini`)
2. Documentation files (`README.*`, `*.md`)
3. Source files

Files are skipped if:
- In excluded directories (`.git`, `node_modules`, `venv`, `dist`, etc.)
- Larger than 40KB (likely minified / generated)
- Not a recognized source extension

### Step 4: LLM Plan Generation

The context (issue + comments + codebase + maintainer style preferences) is
sent to the LLM with a system prompt that instructs:

- Follow existing code style exactly
- Make minimal changes only
- No unrequested features, refactors, or comments
- Respect tech stack preferences (preferred/avoid lists)
- Skip if unclear or unsafe

The LLM responds with a structured JSON plan. RepoKeeper prefers exact edit
operations or unified diffs so large files are not rewritten wholesale. The
legacy full-file `changes` format remains supported as a fallback.

```json
{
  "skip": false,
  "reason": "",
  "summary": "Add file size validation to upload endpoint with 10MB default limit",
  "branch_name": "repokeeper/issue-42-upload-size-validation",
  "commit_message": "feat: add upload size validation (10MB limit)",
  "edits": [
    {
      "path": "src/api/upload.py",
      "find": "MAX_UPLOAD_MB = 100",
      "replace": "MAX_UPLOAD_MB = 10",
      "replace_all": false
    }
  ],
  "patch": "",
  "changes": {
    "src/config.py": "<complete new file content, fallback only>"
  },
  "new_files": {
    "tests/test_upload_validation.py": "<complete new file content>"
  }
}
```

### Step 5: Validation

The plan is validated against profile constraints:

- **`pr.max_files_per_pr`**: Reject if too many files changed
- **`pr.min_tests`**: Warn if no test files changed
- **Branch naming**: Must start with `repokeeper/`

### Step 6: Verification

Before committing, the agent runs verification commands. If any command fails,
RepoKeeper summarizes the failure output and can ask the LLM for a focused fix
up to `agent.max_fix_attempts` times. If verification still fails, RepoKeeper
stops, comments on the issue with the final failure summary, and does not open
a PR.

Verification command selection:

1. If `agent.verify_commands` is set, those commands are used exactly.
2. Otherwise RepoKeeper conservatively discovers available project checks, such
   as `ruff check .` for Python projects and `pytest tests` when pytest is
   installed and a `tests/` directory exists.
3. Set `agent.verify_commands: false` to disable pre-PR verification.

### Step 7: Git Operations

After validation and verification pass, the agent:

1. Creates a new branch (`repokeeper/issue-42-upload-size-validation`)
2. Stages the already-verified patch/edit/full-file changes
3. Commits with the planned commit message
4. Pushes to the remote

### Step 8: PR Creation

A pull request is opened with:

```markdown
## 🤖 RepoKeeper Implementation

Closes #42

### Issue
Add upload size validation

### Plan
Add file size validation to upload endpoint with 10MB default limit

### Changed files
- `src/api/upload.py`
- `src/config.py`
- `tests/test_upload_validation.py`

### Verification
| Command | Status | Exit |
|---|---:|---:|
| `ruff check .` | passed | 0 |
| `pytest tests` | passed | 0 |

### Risk
- Estimated risk: **low**
- Test coverage touched: `tests/test_upload_validation.py`
- Human review is still required before merging.

### Cost and context
- LLM usage: 12850 tokens, ~$0.000214, deepseek-chat
- Context: 8 files, ~22000 context tokens

---
*Generated by RepoKeeper · Please review carefully before merging.*
```

### Step 9: Issue Update

The agent comments on the issue with the PR link and summary:

```
🤖 **RepoKeeper** finished implementation.

**PR:** https://github.com/owner/repo/pull/143

**Summary:** Add file size validation to upload endpoint with 10MB default limit
**Changed files:** `src/api/upload.py`, `src/config.py`, `tests/test_upload_validation.py`
**Verification:** 2 command(s) passed

Please review the changes before merging.
```

## Configuration

### Profile Settings

```yaml
agent:
  model: deepseek-chat
  implement: true
  max_context_files: 60
  max_context_tokens: 25000
  temperature: 0.1
  smart_file_selection: true
  context_expansion: true
  change_mode: edits
  max_fix_attempts: 2
  skip_keywords:
    - "needs design"
    - "breaking change"
    - "RFC required"
  verify_commands:
    - ruff check .
    - pytest tests

style:
  code_style: |
    Follow existing code style exactly.
    Use type hints in Python.
    Keep functions small and focused.

tech:
  preferred:
    - python
    - fastapi
  avoid:
    - jquery

pr:
  max_files_per_pr: 15
  min_tests: true
  review_required: true
```

| Setting | Default | Description |
|---------|---------|-------------|
| `agent.model` | `deepseek-chat` | LLM model. Options: `deepseek-chat`, `deepseek-reasoner`, `gpt-4o`, `gpt-4-turbo`, `claude-sonnet-4-20250514`, `claude-3-5-haiku-20241022` |
| `agent.implement` | `true` | Enable automatic implementation |
| `agent.max_context_files` | `60` | Max files to include in LLM context |
| `agent.max_context_tokens` | `null` | Optional token budget for source context |
| `agent.temperature` | `0.1` | LLM temperature (lower = more deterministic) |
| `agent.smart_file_selection` | `true` | Use two-step LLM file selection before implementation |
| `agent.context_expansion` | `true` | Add likely tests and local dependencies to selected context |
| `agent.change_mode` | `edits` | Preferred change style: `edits`, `patch`, or `full_file` |
| `agent.max_fix_attempts` | `2` | Verification fix retries before giving up |
| `agent.skip_keywords` | `[]` | Phrases that trigger auto-skip |
| `agent.similar_issue_check` | `true` | Scan for duplicate issues before implementing |
| `agent.verify_commands` | auto-detect | Commands that must pass before PR creation; set `false` to disable |
| `agent.similar_issue_check` | `true` | Scan for duplicate issues before implementing |
| `style.code_style` | — | Code style instructions for the LLM |
| `tech.preferred` | `[]` | Preferred tech stack (LLM prioritizes) |
| `tech.avoid` | `[]` | Tech to avoid (LLM will not use) |
| `pr.max_files_per_pr` | `15` | Reject PRs exceeding this many files |
| `pr.min_tests` | `true` | Warn when no test files are changed |

## When the Agent Skips

The agent will skip and explain why in these cases:

| Reason | Example |
|--------|---------|
| Issue is unclear | "Cannot determine what needs to be changed from the description." |
| Too many files | "Implementation would touch 22 files (max: 15). Reduce scope." |
| Skip keyword matched | "Issue contains 'breaking change' — requires design discussion." |
| Unsafe change | "Modifying authentication logic requires manual review." |
| Requires external info | "Need API documentation for the third-party service." |

## Streaming & Cost

The agent streams LLM responses to the workflow log with progress dots every
20 tokens (disabled in CI environments and on retries). After the PR is
created, the issue comment includes token usage and estimated cost:

```
**Estimated cost:** ~$0.000214 (12850 tokens, deepseek-chat)
```

Built-in prices are estimates for common DeepSeek, GPT-4o, GPT-4-turbo, and
Claude models. Override them with `RKP_LLM_PRICE_<MODEL>_INPUT` and
`RKP_LLM_PRICE_<MODEL>_OUTPUT`, using USD per 1M tokens.

### Branch Name Collisions

If the LLM suggests a branch name that already exists on the remote, the
agent automatically appends a `-YYYYMMDDHHMMSS` timestamp suffix.

## Workflow Trigger Details

```yaml
# Triggered by comment "/repokeeper go"
if: |
  github.event_name == 'issue_comment' &&
  !github.event.issue.pull_request &&
  contains(github.event.comment.body, '/repokeeper go') &&
  github.event.comment.author_association in ('OWNER', 'MEMBER', 'COLLABORATOR')

# Triggered by label "agent-todo"
if: |
  github.event_name == 'issues' &&
  github.event.label.name == 'agent-todo'
```

## API Reference

### `run_agent(gh_token, repository, issue_number, llm_api_key, llm_base_url) → dict`

Run the implementation agent end-to-end.

**Returns:** `{"skip": bool, "reason": str, "pr_url": str | None}`

### `collect_repo_files(max_files=60) → dict[str, str]`

Collect source files from the repository.

### `get_issue_data(repo, number) → dict`

Extract structured data from a GitHub issue.

### `call_llm(issue_data, context_str, profile, llm_client) → dict`

Call the LLM to generate an implementation plan.

### `validate_implementation(implementation, profile) → list[str]`

Validate an implementation against profile constraints.

### `check_skip_keywords(issue_data, profile) → str | None`

Check if the issue matches any skip keywords.

## Best Practices

1. **Start small.** First issues should be well-scoped, single-file changes.
2. **Review thoroughly.** The agent is fast but not infallible. Always review.
3. **Use skip keywords.** Add phrases like `security`, `auth`, `breaking` to
   prevent the agent from touching sensitive areas.
4. **Provide clear issues.** The more specific the issue description, the
   better the implementation.
5. **Iterate on style.** Update `style.code_style` in your profile as you
   discover what the agent gets wrong.
