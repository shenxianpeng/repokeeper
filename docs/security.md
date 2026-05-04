# Security

RepoKeeper automates maintenance work by opening pull requests. It should be
treated like any other automation with write access to a repository: useful, but
bounded and reviewable.

## Default Safety Model

The default Implementation Agent workflow is built around review, not direct
merge:

- The agent creates a branch and pull request.
- Generated pull requests are not auto-merged.
- Maintainers review the diff before merging.
- Workflow files under `.github/workflows/` are blocked from agent edits.
- Comment-based triggering is restricted to collaborators.
- Label-based triggering depends on who can apply labels in your repository.

## Permissions

The starter workflow asks GitHub Actions for:

```yaml
permissions:
  contents: write
  issues: write
  pull-requests: write
```

These permissions allow RepoKeeper to push a branch, comment on the issue, and
open a pull request. Avoid using tokens with broader repository administration
permissions.

## Tokens and Secrets

Use repository or organization secrets for all credentials:

- `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`
- `REPOKEEPER_GITHUB_TOKEN` only when the default `GITHUB_TOKEN` cannot create
  pull requests in your repository
- Notification secrets such as SMTP or Telegram credentials

Do not commit secrets to `repokeeper.yml`, workflow files, or documentation.

## Trigger Control

RepoKeeper supports two agent triggers:

- `@repokeeper go` on an issue comment from an owner, member, or collaborator
- `agent-todo` label on an issue

If many contributors can apply labels in your repository, prefer the comment
trigger or adjust the workflow condition before enabling label-based execution.

## Repository Guardrails

Use `repokeeper.yml` to keep generated work small and reviewable:

```yaml
agent:
  max_context_files: 20
  verify_commands:
    - ruff check .
    - pytest tests
  skip_keywords:
    - "security-sensitive"
    - "needs design"

pr:
  max_files_per_pr: 8
  review_required: true
  auto_merge: false
```

Set `agent.implement: false` to keep Radar and Patrol enabled while disabling
automatic implementation PRs.

## What RepoKeeper Should Handle

Good candidates:

- Small bug fixes with clear reproduction details
- Documentation updates
- Test additions
- Dependency update candidates
- Simple CI configuration fixes

Avoid autonomous implementation for:

- Authentication, authorization, or cryptography changes
- Large architectural rewrites
- Vague product decisions
- Unreviewed workflow or deployment changes
- Issues that require access to private production data

## Reporting Security Issues

Report vulnerabilities privately through a GitHub security advisory when
available, or contact the repository maintainer directly. Avoid public issues
for vulnerabilities involving secrets, command execution, token handling, or
privilege escalation.

