# Quick Start

Get RepoKeeper running in 5 minutes.

## 1. Copy the agent workflow

Create `.github/workflows/` and copy the Implementation Agent workflow:

```bash
mkdir -p .github/workflows
curl -fsSLo .github/workflows/repokeeper.yml \
  https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/src/repokeeper/templates/workflows/repokeeper.yml
```

You can also run `pip install repokeeper && repokeeper init . --minimal` if
you prefer the CLI to write the profile and workflow.

## 2. Add your API key

Go to **Settings → Secrets and variables → Actions** → **New repository secret**:

- **Name:** `DEEPSEEK_API_KEY`
- **Value:** `sk-...`

## 3. Push and test

```bash
git add .github/workflows/repokeeper.yml
git commit -m "Add RepoKeeper agent"
git push
```

Create a new issue, label it `agent-todo`, and RepoKeeper will analyze your
codebase and open a PR with the implementation.

Or comment `@repokeeper go` on an existing issue (must be a repo collaborator).

## 4. Optional: create a profile

RepoKeeper runs with defaults, but `repokeeper.yml` lets you customize style,
verification commands, and skip rules:

```bash
cat > repokeeper.yml <<'EOF'
maintainer: your-github-username

style:
  testing: pytest
  linting: true

agent:
  model: deepseek-chat
  verify_commands:
    - ruff check .
    - pytest tests
EOF
```

Run a local setup check any time:

```bash
repokeeper doctor --repo owner/repo
```

Want community monitoring and daily health reports too? Run
`repokeeper init . --all-workflows --force` or copy `radar.yml` and `patrol.yml`
from the template directory.

---

That's it. RepoKeeper can now handle issue-triggered implementation PRs.
