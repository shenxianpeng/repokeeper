# Quick Start

Get RepoKeeper running in 5 minutes.

## 1. Copy the workflows

Create `.github/workflows/` and copy the bundled workflows:

```bash
mkdir -p .github/workflows
curl -fsSLo .github/workflows/repokeeper.yml \
  https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/src/repokeeper/templates/workflows/repokeeper.yml
curl -fsSLo .github/workflows/radar.yml \
  https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/src/repokeeper/templates/workflows/radar.yml
curl -fsSLo .github/workflows/patrol.yml \
  https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/src/repokeeper/templates/workflows/patrol.yml
```

You can also run `pip install repokeeper && repokeeper init . --workflows` if
you prefer the CLI to write these files.

## 2. Create your profile

Create `repokeeper.yml` and adjust anything repo-specific:

```bash
cat > repokeeper.yml <<'EOF'
maintainer: your-github-username
radar:
  keywords:
    - bug
    - crash
    - security
    - feature request

patrol:
  schedule: "0 8 * * 1-5"

agent:
  model: deepseek-chat
EOF
```

## 3. Add your API key

Go to **Settings → Secrets and variables → Actions** → **New repository secret**:

- **Name:** `DEEPSEEK_API_KEY`
- **Value:** `sk-...`

## 4. Push and test

```bash
git add .github/workflows repokeeper.yml
git commit -m "Add RepoKeeper"
git push
```

Go to the **Actions** tab, select **RepoKeeper Daily Patrol**, click **Run workflow**.

## 5. Try the Implementation Agent

Create a new issue, label it `agent-todo`, and RepoKeeper will analyze your codebase
and open a PR with the implementation.

Or comment `@repokeeper go` on an existing issue (must be a repo collaborator).

---

That's it! RepoKeeper is now monitoring your repository.
