---
hide:
  - navigation
  - toc
---

<div class="rk-home">
  <section class="rk-home__hero">
    <div class="rk-home__eyebrow">GitHub-native maintainer agent</div>
    <h1 class="rk-home__title">
      Keep repositories moving.
      <span>Turn issues into verified PRs.</span>
    </h1>
    <p class="rk-home__lede">
      RepoKeeper runs maintenance where open source work already happens:
      GitHub issues, Actions, branches, checks, and pull requests. It watches
      the queue, reads the codebase, verifies changes, and leaves maintainers
      with reviewable work instead of another prompt.
    </p>
    <div class="rk-home__actions">
      <a class="md-button md-button--primary" href="quick-start/">Add to a repo</a>
      <a class="md-button" href="module-3-agent/">See the agent flow</a>
    </div>
  </section>

  <section class="rk-signal-grid">
    <div class="rk-signal-card">
      <div class="rk-signal-card__label">Trigger</div>
      <div class="rk-signal-card__value">Issue label or comment</div>
    </div>
    <div class="rk-signal-card">
      <div class="rk-signal-card__label">Context</div>
      <div class="rk-signal-card__value">Repo files + maintainer profile</div>
    </div>
    <div class="rk-signal-card">
      <div class="rk-signal-card__label">Verify</div>
      <div class="rk-signal-card__value">Project lint and tests</div>
    </div>
    <div class="rk-signal-card">
      <div class="rk-signal-card__label">Deliver</div>
      <div class="rk-signal-card__value">Branch, commit, pull request</div>
    </div>
  </section>

  <section class="rk-band">
    <p>
      GitHub Copilot helps you code in the editor and review PRs.
      CodeRabbit and PR-Agent automate PR workflows with line-level suggestions.
      RepoKeeper does what they don't — implements issues, fixes PRs
      conversationally, scans 8 ecosystems for outdated dependencies, diagnoses
      CI failures, and monitors your community.  Two backends: native (fast,
      cheap) and Pi (autonomous agent loop for complex tasks).
    </p>
  </section>

  <section class="rk-card-grid">
    <article class="rk-panel">
      <h2>What ships now</h2>
      <ul>
        <li>Issue-triggered implementation agent with native and Pi backends</li>
        <li>Conversational PR fix mode — comment on a PR, agent fixes it</li>
        <li>Inline code review with severity indicators on specific lines</li>
        <li>Auto-labeler for issues and PRs (15 categories, diff-aware)</li>
        <li>Draft release notes from merged PRs and direct commits</li>
        <li>Profile-driven code style, skip keywords, and PR guardrails</li>
        <li>Pre-PR verification through discovered or configured commands</li>
        <li>Community radar (issues + discussions) and daily patrol reports</li>
      </ul>
    </article>

    <article class="rk-panel">
      <h2>Fastest path</h2>
      <ol>
        <li>Copy one GitHub Actions workflow into the repository.</li>
        <li>Add a <code>DEEPSEEK_API_KEY</code> repository secret.</li>
        <li>Label an issue <code>agent-todo</code> or comment <code>/repokeeper go</code>.</li>
        <li>Review the generated pull request.</li>
        <li>Comment <code>/repokeeper go</code> on the PR with feedback to get fixes.</li>
      </ol>
    </article>

    <article class="rk-terminal">
      <div class="rk-terminal__bar">
        <div class="rk-terminal__dot"></div>
        <div class="rk-terminal__dot"></div>
        <div class="rk-terminal__dot"></div>
        <span class="rk-terminal__title">github-actions / repokeeper</span>
      </div>
      <div class="rk-terminal__body">
        <div><span class="t-comment"># label: agent-todo</span></div>
        <div><span class="t-label">[repokeeper]</span> <span class="t-white">Issue #42: Add dark mode toggle</span></div>
        <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Running Pi coding agent (deepseek-chat)...</span></div>
        <div><span class="t-label">[repokeeper]</span> <span class="t-white">Plan: Add theme state and styles</span></div>
        <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Verifying: ruff check .</span></div>
        <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Verifying: pytest tests</span></div>
        <div><span class="t-green">PR opened:</span> <span class="t-highlight">github.com/owner/repo/pull/67</span></div>
      </div>
    </article>

    <article class="rk-panel">
      <h2>Current boundary</h2>
      <ul>
        <li>Human review remains required before merging generated PRs</li>
        <li>Workflow file edits are blocked for GitHub token safety</li>
        <li>Dependency upgrades and CI repair PRs are reported as candidates first</li>
      </ul>
    </article>
  </section>

  <section class="rk-panel">
    <h2>RepoKeeper vs. AI coding tools</h2>
    <table class="rk-compare">
      <thead>
        <tr><th></th><th>Copilot Code Review</th><th>CodeRabbit</th><th>PR-Agent</th><th>RepoKeeper</th></tr>
      </thead>
      <tbody>
        <tr><td>Issue → PR</td><td>—</td><td>—</td><td>—</td><td class="rk-yes">Native + Pi</td></tr>
        <tr><td>PR fix (conversational)</td><td>—</td><td>—</td><td>—</td><td class="rk-yes">Push to same branch</td></tr>
        <tr><td>Code review</td><td class="rk-yes">PR only</td><td class="rk-yes">Line-level</td><td class="rk-yes">/review</td><td class="rk-yes">Inline + severity</td></tr>
        <tr><td>PR description</td><td>—</td><td class="rk-yes">Auto</td><td class="rk-yes">/describe</td><td class="rk-yes">/describe</td></tr>
        <tr><td>Auto-labeling</td><td>—</td><td class="rk-yes">Yes</td><td class="rk-yes">Yes</td><td class="rk-yes">15 categories, diff-aware</td></tr>
        <tr><td>Dependency scanning</td><td>—</td><td>—</td><td>—</td><td class="rk-yes">8 ecosystems</td></tr>
        <tr><td>CI auto-fix</td><td>—</td><td>—</td><td>—</td><td class="rk-yes">Repair PRs</td></tr>
        <tr><td>Community radar</td><td>—</td><td>—</td><td>—</td><td class="rk-yes">Issues + discussions</td></tr>
        <tr><td>Scheduled / cron</td><td>—</td><td>—</td><td>—</td><td class="rk-yes">Daily patrol</td></tr>
        <tr class="rk-highlight"><td>OSS cost</td><td>Subscription</td><td>Free</td><td>Free</td><td>Free (your own LLM key)</td></tr>
      </tbody>
    </table>
  </section>
</div>
