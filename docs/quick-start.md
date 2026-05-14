# Quick Start

Get RepoKeeper running in 5 minutes.

## 1. Copy the agent workflow

Create `.github/workflows/repokeeper.yml`:

```yaml
name: RepoKeeper Implementation Agent

on:
  issue_comment:
    types: [created]
  issues:
    types: [labeled]
  pull_request_target:
    types: [labeled]

jobs:
  agent:
    runs-on: ubuntu-latest
    if: |
      (
        github.event_name == 'issue_comment' &&
        contains(github.event.comment.body, '/repokeeper go') &&
        (
          github.event.comment.author_association == 'OWNER' ||
          github.event.comment.author_association == 'MEMBER' ||
          github.event.comment.author_association == 'COLLABORATOR'
        )
      ) ||
      (
        github.event_name == 'issues' &&
        github.event.label.name == 'agent-todo'
      ) ||
      (
        github.event_name == 'pull_request_target' &&
        github.event.label.name == 'agent-fix'
      )
    permissions:
      contents: write
      issues: write
      pull-requests: write
    steps:
      - name: Determine context
        id: ctx
        shell: bash
        run: |
          if [ "${{ github.event_name }}" = "pull_request_target" ]; then
            echo "issue=${{ github.event.pull_request.number }}" >> "$GITHUB_OUTPUT"
            echo "pr=${{ github.event.pull_request.number }}" >> "$GITHUB_OUTPUT"
          elif [ "${{ github.event.issue.pull_request }}" != "" ]; then
            echo "issue=${{ github.event.issue.number }}" >> "$GITHUB_OUTPUT"
            echo "pr=${{ github.event.issue.number }}" >> "$GITHUB_OUTPUT"
          else
            echo "issue=${{ github.event.issue.number }}" >> "$GITHUB_OUTPUT"
            echo "pr=" >> "$GITHUB_OUTPUT"
          fi

      - uses: shenxianpeng/repokeeper@v1
        with:
          module: agent
          repo: ${{ github.repository }}
          issue: ${{ steps.ctx.outputs.issue }}
          pr: ${{ steps.ctx.outputs.pr }}
          llm_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
          llm_base_url: ${{ secrets.LLM_BASE_URL || 'https://api.deepseek.com' }}
          github_token: ${{ secrets.REPOKEEPER_GITHUB_TOKEN || secrets.GITHUB_TOKEN }}
```

The composite action bundles checkout, Python setup, Pi agent runtime
(Node.js + `@earendil-works/pi-coding-agent`), and the agent run into a single
step.

You can also run `pip install repokeeper && repokeeper init . --minimal` if
you prefer the CLI to write the profile and workflow.

## 2. Add your API key

Go to **Settings → Secrets and variables → Actions** → **New repository secret**:

- **Name:** `DEEPSEEK_API_KEY`
- **Value:** `sk-...`

## 3. Push and test

Run a setup check before pushing:

```bash
repokeeper doctor --repo owner/repo
```

Fix anything marked `missing`. The doctor checks the profile, workflow file,
required workflow triggers and permissions, token environment, LLM key, and
repository slug.

```bash
git add .github/workflows/repokeeper.yml
git commit -m "Add RepoKeeper agent"
git push
```

Create a new issue, label it `agent-todo`, and RepoKeeper will analyze your
codebase and open a PR with the implementation.

Or comment `/repokeeper go` on an existing issue or PR (must be a repo
collaborator).  On a PR, RepoKeeper enters **fix mode**: reads the feedback,
generates fixes, and pushes to the same branch.

## 4. Optional: create a profile

RepoKeeper runs with defaults, but `repokeeper.yml` lets you customize style,
verification commands, backend, and skip rules:

```bash
cat > repokeeper.yml <<'EOF'
maintainer: your-github-username

style:
  testing: pytest
  linting: true

agent:
  model: deepseek-chat
  backend: native            # native | pi (autonomous agent loop)
  verify_commands:
    - ruff check .
    - pytest tests
EOF
```

Set `backend: pi` for complex issues that need multi-file exploration and
self-verification.  Pi reads files, runs tests, and iterates autonomously.
The composite action includes Node.js and Pi automatically — just change
the config.

Run the local setup check any time:

```bash
repokeeper doctor --repo owner/repo
```

Want community monitoring and daily health reports too? Run
`repokeeper init . --all-workflows --force` to generate the full set of
workflows (Agent, Radar, Patrol, Labeler, Review).

Other useful commands:

```bash
repokeeper review --repo owner/repo --pr 42   # code review with inline comments
repokeeper describe --repo owner/repo --pr 42  # auto-generate PR description
```

---

## Trigger reference

| Trigger | What happens |
|---------|-------------|
| Label `agent-todo` on an issue | Agent implements the issue → opens a PR |
| Comment `/repokeeper go` on an issue | Same as `agent-todo` |
| Comment `/repokeeper go` on a PR | Fix mode: reads feedback → pushes fixes |
| Label `agent-fix` on a PR | Same as PR comment trigger |
| Comment `/repokeeper review` on a PR | Code review with inline comments |
| Label `agent-review` on a PR | Same as review comment trigger |

That's it. RepoKeeper can now handle issue-triggered implementation PRs,
conversational PR fixes, and inline code reviews.
