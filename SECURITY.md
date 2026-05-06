# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in RepoKeeper, please report it
privately via a [GitHub Security Advisory](https://github.com/shenxianpeng/repokeeper/security/advisories/new)
or contact the maintainer directly.  **Do not open a public issue.**

Vulnerabilities of particular concern include:

- Accidental disclosure of API keys, tokens, or secrets in generated output
- Escape of repository path constraints (writing files outside the repo)
- Unsafe LLM output that could inject malicious code or commands
- Token leakage through PR comments or logs
- Trigger bypass (unauthorized users triggering the agent)

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.8.x   | :white_check_mark: |
| < 0.8   | :x:                |

Only the latest release receives security fixes.  Upgrade before reporting
issues against older versions.

## Security Model

RepoKeeper automates maintenance by opening **reviewable pull requests** —
it never auto-merges, never modifies `.github/workflows/`, and restricts
trigger access to repository collaborators.  See the [full security
documentation](https://shenxianpeng.github.io/repokeeper/security/) for
details on permissions, tokens, trigger control, and repository guardrails.
