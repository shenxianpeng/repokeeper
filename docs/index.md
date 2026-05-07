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
      Copilot and Cursor are AI coding agents that work with you in the editor.
      PR Agent (Qodo) automates PR workflows.
      RepoKeeper handles the maintenance queue when you are away — triaging,
      scanning, labeling, and fixing around the clock.
    </p>
  </section>

  <section class="rk-card-grid">
    <article class="rk-panel">
      <h2>What ships now</h2>
      <ul>
        <li>Issue-triggered implementation agent for GitHub repositories</li>
        <li>Profile-driven code style, skip keywords, and PR guardrails</li>
        <li>Pre-PR verification through discovered or configured commands</li>
        <li>Community radar and daily patrol reports for maintainer visibility</li>
      </ul>
    </article>

    <article class="rk-panel">
      <h2>Fastest path</h2>
      <ol>
        <li>Copy one GitHub Actions workflow into the repository.</li>
        <li>Add a <code>DEEPSEEK_API_KEY</code> repository secret.</li>
        <li>Label an issue <code>agent-todo</code> or comment <code>@repokeeper go</code>.</li>
        <li>Review the generated branch and pull request.</li>
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
        <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Collecting repository context...</span></div>
        <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Calling LLM (deepseek-chat)...</span></div>
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
        <tr><th></th><th>Copilot / Cursor</th><th>PR Agent (Qodo)</th><th>RepoKeeper</th></tr>
      </thead>
      <tbody>
        <tr><td>Primary job</td><td>AI agent coding in your editor</td><td>Automate PR workflows</td><td>Maintain the repository queue</td></tr>
        <tr><td>Interface</td><td>Editor session</td><td>PR comments, CLI</td><td>Issues, Actions, branches, PRs</td></tr>
        <tr><td>Timing</td><td>When you are coding</td><td>On PR events</td><td>On labels, comments, and schedules</td></tr>
        <tr><td>Verification</td><td>Developer-run checks</td><td>AI review &amp; suggestions</td><td>Configured pre-PR commands</td></tr>
        <tr class="rk-highlight"><td>Output</td><td>Code changes in-editor</td><td>PR descriptions &amp; reviews</td><td>Reviewable pull requests</td></tr>
      </tbody>
    </table>
  </section>
</div>
