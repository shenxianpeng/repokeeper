# Quick Start

Get RepoKeeper running in 5 minutes.

## 1. Fork or copy the workflows

Create these files in your repo:

```bash
mkdir -p .github/workflows
```

Copy [`repokeeper.yml`](https://github.com/shenxianpeng/repokeeper/blob/main/.github/workflows/repokeeper.yml),
[`radar.yml`](https://github.com/shenxianpeng/repokeeper/blob/main/.github/workflows/radar.yml),
and [`patrol.yml`](https://github.com/shenxianpeng/repokeeper/blob/main/.github/workflows/patrol.yml)
into `.github/workflows/`.

## 2. Create your profile

```bash
cat > repokeeper.yml << 'EOF'
maintainer: $(git config user.name)

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
