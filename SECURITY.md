# Security Policy

RepoKeeper is designed to create reviewable pull requests, not to merge code
without a maintainer. This document explains the default safety boundaries and
how to report vulnerabilities.

## Supported Versions

Security fixes are handled on the `main` branch and released through the latest
published package version.

## Reporting a Vulnerability

Please report security issues privately by opening a GitHub security advisory
for this repository when available, or by contacting the maintainer listed in
the repository profile.

Do not open public issues for vulnerabilities that expose secrets, token
handling weaknesses, command injection, privilege escalation, or unsafe
automation behavior.

## Automation Boundaries

RepoKeeper runs inside GitHub Actions and uses the permissions granted by the
workflow. The default Implementation Agent workflow is intentionally limited to
creating branches, commenting on issues, and opening pull requests.

By default:

- RepoKeeper does not auto-merge generated pull requests.
- Human review remains required before generated changes are merged.
- Workflow file edits under `.github/workflows/` are blocked by the agent.
- Only repository collaborators can trigger the comment-based agent workflow.
- Label-based execution should be limited to trusted users who can apply labels.
- Verification commands run before a pull request is opened when configured or
  discovered.

## Token Guidance

Use the least privileged token that can complete the workflow:

- Prefer the default `GITHUB_TOKEN` when repository Actions settings allow it to
  create pull requests.
- Use `REPOKEEPER_GITHUB_TOKEN` only when a personal access token is required by
  your repository or organization policy.
- Do not grant administration permissions to the token.
- Store LLM and GitHub credentials only as GitHub Actions secrets.

## Recommended Settings

For sensitive repositories, start with conservative settings in
`repokeeper.yml`:

```yaml
agent:
  implement: true
  max_context_files: 20
  verify_commands:
    - ruff check .
    - pytest tests

pr:
  max_files_per_pr: 8
  review_required: true
  auto_merge: false
```

Disable autonomous implementation entirely with:

```yaml
agent:
  implement: false
```

