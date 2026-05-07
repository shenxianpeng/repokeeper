# Dogfood Case Studies

RepoKeeper should earn trust through public, reviewable work. Use this page to
publish real runs where an issue was handled by RepoKeeper, the generated PR
passed CI, and a maintainer merged it.

Do not add synthetic examples. Each case should link to the original issue, the
RepoKeeper pull request, the CI run, and the merge commit.

## Case Study Template

```markdown
## <Repository>: <Issue Title>

| Field | Link |
|---|---|
| Issue | https://github.com/owner/repo/issues/123 |
| RepoKeeper PR | https://github.com/owner/repo/pull/456 |
| CI run | https://github.com/owner/repo/actions/runs/789 |
| Merge commit | https://github.com/owner/repo/commit/abcdef |

### Scope
One or two sentences describing the issue and why it was a good autonomous
maintenance candidate.

### RepoKeeper Output
- Changed files:
- Verification commands:
- LLM usage and estimated cost:
- Human review notes:

### Result
State whether CI passed, what the maintainer changed before merging, and what
should be improved in RepoKeeper based on the run.
```

## Candidate Criteria

- The issue is small enough for one PR.
- The requested behavior is already clear from the issue or existing tests.
- The change can be verified by repository lint and tests.
- The PR body includes changed files, verification evidence, risk, and cost.
- A human maintainer reviewed and merged the result.

## Suggested First Runs

Start with low-risk issues in RepoKeeper itself:

- Documentation drift between README and docs pages.
- Small CLI diagnostics improvements covered by tests.
- Narrow bug fixes in parsing, context selection, or verification summaries.

After each successful merge, replace the template above with the real links and
notes. This keeps the public proof honest and useful for new adopters.
