# GitHub Marketplace

RepoKeeper's Marketplace listing should be a unified GitHub maintainer
automation action. The default module is the Implementation Agent, but the same
root action can also run review, PR description, radar, patrol, labeler, and release
workflows.

## Listing Copy

**Name:** RepoKeeper Maintainer Automation

**Tagline:** Run AI maintainer workflows inside GitHub Actions.

**Description:**

RepoKeeper is a GitHub-native maintainer automation toolkit. Use one action with
`module: agent`, `review`, `describe`, `radar`, `monitor`, `patrol`, `labeler`, or `release` to turn
issues into pull requests, review PRs, generate PR descriptions, monitor
community signals, run daily maintenance patrols, classify issues or PRs, and
draft release notes.
The default agent module reads approved issues, collects code context, asks the
configured model for a minimal implementation, verifies the change, pushes a
branch, and opens a pull request for human review.

**Primary category:** Code quality

**Secondary category:** Utilities

## Demo Script

Use this flow for a two-minute screen recording:

1. Open a repository with RepoKeeper installed.
2. Show `.github/workflows/repokeeper.yml` using `shenxianpeng/repokeeper@v1`
   with `module: agent`.
3. Open a small issue with clear acceptance criteria.
4. Add the `agent-todo` label or comment `/repokeeper go`.
5. Show the GitHub Actions run collecting context and running verification.
6. Open the generated PR.
7. Point out changed files, verification evidence, cost/context, and risk.
8. Add a PR comment with a small requested change.
9. Comment `/repokeeper go` on the PR.
10. Show RepoKeeper pushing the fix to the same branch.

Keep the video focused on GitHub screens only. The message is that maintainers
do not leave the issue and PR workflow.

## Publishing Notes

GitHub Marketplace uses the action metadata in the root `action.yml`. The
repository now includes that root action so the Marketplace banner can appear
when drafting a release.

Publishing still requires a manual GitHub step:

1. Open the root `action.yml` on GitHub.
2. Click the Marketplace publishing banner.
3. Draft a release.
4. Select `Publish this Action to the GitHub Marketplace`.
5. Choose the categories above.
6. Publish the release with 2FA.

GitHub requires a public repository, a unique action `name`, and valid action
metadata. RepoKeeper publishes one Marketplace action and uses `module` to route
to each workflow.
