# AGENTS.md

Instructions for AI coding agents working on this repository.

## Conversational Style

- Keep answers short and concise
- No emojis in commits, issues, PR comments, or code
- No fluff or cheerful filler text
- Technical prose only, be kind but direct

## Code Quality

- Read files in full before making wide-ranging changes, before editing files you have not already fully inspected, and when the user asks you to investigate or audit something. Do not rely only on search snippets for broad changes.
- Always ask before removing functionality or code that appears to be intentional
- Do not preserve backward compatibility unless the user explicitly asks for it

## Before Committing

Always run the linter and fix any issues before committing:

```bash
ruff check src/ tests/
```

If there are any lint errors or warnings, fix them, then run the linter again. Repeat until there are **zero** lint issues.

## Test Coverage

Run tests with coverage before committing:

```bash
pytest tests/ --cov=repokeeper --cov-report=term-missing
```

Ensure all tests pass.

## Type Checking

Run mypy before committing. NEVER push code with type errors:

```bash
mypy src/repokeeper
```

If there are any type errors, fix them, then run mypy again. Repeat until
there are **zero** type errors. Only then may you commit and push.

## Git Rules for Parallel Agents

Multiple agents may work on different files in the same worktree simultaneously. You MUST follow these rules:

### Committing

- **ONLY commit files YOU changed in THIS session**
- ALWAYS include `fixes #<number>` or `closes #<number>` in the commit message when there is a related issue or PR
- NEVER use `git add -A` or `git add .` - these sweep up changes from other agents
- ALWAYS use `git add <specific-file-paths>` listing only files you modified
- Before committing, run `git status` and verify you are only staging YOUR files
- Track which files you created/modified/deleted during the session

### Forbidden Git Operations

These commands can destroy other agents' work:

- `git reset --hard` - destroys uncommitted changes
- `git checkout .` - destroys uncommitted changes
- `git clean -fd` - deletes untracked files
- `git stash` - stashes ALL changes including other agents' work
- `git add -A` / `git add .` - stages other agents' uncommitted work
- `git commit --no-verify` - bypasses required checks and is never allowed

### Safe Workflow

```bash
# 1. Check status first
git status

# 2. Add ONLY your specific files
git add path/to/file1.py path/to/file2.py

# 3. Commit
git commit -m "description"

# 4. Push (pull --rebase if needed, but NEVER reset/checkout)
git pull --rebase && git push
```

### If Rebase Conflicts Occur

- Resolve conflicts in YOUR files only
- If conflict is in a file you didn't modify, abort and ask the user
- NEVER force push
