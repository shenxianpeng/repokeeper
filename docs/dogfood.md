# Dogfood Cases

RepoKeeper needs public proof, not synthetic examples. This page tracks real
runs against this repository. Merged proof cases are separated from learning
cases so the page does not overstate unmerged or failed runs.

## Merged Proof Cases

| Case | Trigger | Output | Result | Lesson |
|---|---|---|---|---|
| [Code quality repair](#code-quality-repair) | RepoKeeper branch `repokeeper/ci-fix-1` | PR #13 | Merged, CI passed | Larger autonomous repairs need strong verification evidence |

## Learning Log

| Case | Trigger | Output | Result | Lesson |
|---|---|---|---|---|
| [Architecture diagram](#architecture-diagram) | Issue #26 + `agent-todo` | PR #27 | Generated, closed unmerged | PR body proof format works; docs-only changes need clear merge workflow |
| [Labeler exact-edit miss](#labeler-exact-edit-miss) | Issue #28 + `agent-todo` | Error comment | Failed | Exact edits must fall back to patch or re-select files |
| [Workflow guardrail](#workflow-guardrail) | Issue #32 + `agent-todo` | Issue #33 | Correctly refused | Blocking workflow edits protects repository automation |
| [Coverage scope boundary](#coverage-scope-boundary) | Issue #35 + `agent-todo` | Issue #36 | Failed with no changes | Broad coverage goals need task decomposition before agent execution |
| [Draft release generator](#draft-release-generator) | Issue #22 + Pi backend | PR #31 | Open, under review | Medium-risk generated PRs need human review before proof claims |

## Architecture Diagram

| Field | Link |
|---|---|
| Issue | [#26 Add a digram for RepoKeeper using mermaid to README.md](https://github.com/shenxianpeng/repokeeper/issues/26) |
| RepoKeeper PR | [#27 docs: add mermaid diagram to README.md](https://github.com/shenxianpeng/repokeeper/pull/27) |
| Outcome | Closed without merge |

Scope: Add a README architecture diagram. This was a low-risk documentation
task and a good test of issue-to-PR generation.

RepoKeeper output:

- Changed files: `README.md`
- Verification: not run
- LLM usage: 5,380 tokens, approximately $0.000825, `deepseek-chat`
- Context: 1 file, approximately 2,166 context tokens

Result: RepoKeeper opened a structured PR with changed files, risk, context,
and cost. The PR was closed instead of merged, so this is proof of PR
generation, not proof of accepted output.

## Code Quality Repair

| Field | Link |
|---|---|
| Pull request | [#13 fix: 10 code quality and safety improvements](https://github.com/shenxianpeng/repokeeper/pull/13) |
| Branch | `repokeeper/ci-fix-1` |
| Outcome | Merged on 2026-05-06 |

Scope: Apply a batch of code quality and safety fixes, including exception
types, safer git helpers, logging, and tests.

RepoKeeper output:

- Changed files: multiple source and test files
- Verification: GitHub checks passed for Python 3.10, 3.11, 3.12, 3.13, 3.14
- Human review notes: Codecov reported patch coverage gaps, but the test matrix
  passed and the PR was merged.

Result: This is the strongest merged proof case currently available. It should
be replaced or complemented with smaller issue-linked merged cases as more
RepoKeeper-generated PRs are accepted.

## Labeler Exact-Edit Miss

| Field | Link |
|---|---|
| Issue | [#28 bug: remove `repokeeper-labeler` default label added by repokeeper labeler](https://github.com/shenxianpeng/repokeeper/issues/28) |
| Outcome | Failed before PR creation |

Scope: Remove an unwanted default label behavior from the auto-labeler.

RepoKeeper output:

- It acknowledged the issue and began implementation.
- It failed with `Edit target not found in src/repokeeper/labeler.py`.

Result: The failure was useful. Exact find/replace edits are safe, but brittle.
This case supports improving fallback behavior: retry with a patch, refresh file
context, or use the Pi backend for edits that drift from selected snippets.

## Workflow Guardrail

| Field | Link |
|---|---|
| Issue | [#32 Revert commit 8c7e0a720b06cb860115e069f1aa212aeb93ee23](https://github.com/shenxianpeng/repokeeper/issues/32) |
| Follow-up issue | [#33 Project rules prohibit modifying files under `.github/workflows/`](https://github.com/shenxianpeng/repokeeper/issues/33) |
| Outcome | Correctly refused |

Scope: Revert a commit that only affected workflow files.

RepoKeeper output:

- Pi analyzed the requested revert.
- It refused because the only viable change modified `.github/workflows/`, a
  blocked path.

Result: This is a positive safety case. RepoKeeper avoided changing workflow
automation with a write token, which matches the documented security boundary.

## Coverage Scope Boundary

| Field | Link |
|---|---|
| Issue | [#35 Increase code coverage to >= 80%](https://github.com/shenxianpeng/repokeeper/issues/35) |
| Follow-up issue | [#36 RepoKeeper Pi finished but produced no file changes](https://github.com/shenxianpeng/repokeeper/issues/36) |
| Outcome | Failed with no changes |

Scope: Raise overall project coverage after Codecov reported project coverage
below the configured threshold.

RepoKeeper output:

- Pi ran but produced no file changes.
- A follow-up issue captured the no-change failure.

Result: This is a task-shaping failure. Large coverage goals should be split
into concrete test targets, for example one module or one missing branch at a
time, before invoking the implementation agent.

## Draft Release Generator

| Field | Link |
|---|---|
| Issue | [#22 Support draft release](https://github.com/shenxianpeng/repokeeper/issues/22) |
| RepoKeeper PR | [#31 feat: add draft release generator with AI-powered release notes](https://github.com/shenxianpeng/repokeeper/pull/31) |
| Outcome | Open, under review |

Scope: Add an AI-assisted draft release generator.

RepoKeeper output:

- Changed files include CLI/profile integration, a new releaser module, tests,
  and unexpected files.
- Verification was not run.
- Risk was marked medium.

Result: This should not be marketed as a successful case yet. It is useful
evidence that medium-risk generated PRs need stricter file controls and
verification before they become public proof.

## Criteria for Future Proof Cases

A case is strong enough for the README only when:

- The original issue is public and clearly scoped.
- RepoKeeper generated the PR or fix commit.
- CI passed.
- A maintainer merged the result.
- The case records changed files, verification commands, model/cost, and human
  review notes.

Target next: five small merged cases covering docs, test additions, bug fixes,
review feedback fixes, and CI repair.
