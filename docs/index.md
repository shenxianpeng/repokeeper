---
hide:
  - navigation
  - toc
---

<div class="rk-hero" markdown>
  <div class="rk-hero__eyebrow">ai maintainer · autonomous · zero config</div>
  <div class="rk-hero__headline">
    Your repo,<br>
    <span class="rk-accent">maintained by AI.</span>
  </div>
  <p class="rk-hero__sub">
    <strong>RepoKeeper</strong> is an AI agent that runs your open source maintenance —
    monitoring issues, diagnosing CI, updating dependencies, and <em>implementing code from issues</em>.
    No code-completion. No prompting. Just an agent doing the work.
  </p>
  <div class="rk-hero__facts" markdown>
    <span class="rk-hero__fact">Zero config</span>
    <span class="rk-hero__fact">GitHub-native</span>
    <span class="rk-hero__fact">$0.01 per PR</span>
    <span class="rk-hero__fact">CI-ready</span>
  </div>
  <div class="rk-cta-group" markdown>
    <a class="rk-btn rk-btn-primary" href="quick-start/">Add to my repo</a>
    <a class="rk-btn rk-btn-secondary" href="https://github.com/shenxianpeng/repokeeper">GitHub →</a>
  </div>
</div>

<div class="rk-terminal" markdown>
  <div class="rk-terminal__bar">
    <div class="rk-terminal__dot"></div>
    <div class="rk-terminal__dot"></div>
    <div class="rk-terminal__dot"></div>
    <span class="rk-terminal__title">github-actions — repokeeper</span>
  </div>
  <div class="rk-terminal__body" markdown>
    <div><span class="t-comment"># Label any issue agent-todo — RepoKeeper handles the rest</span></div>
    <div>&nbsp;</div>
    <div><span class="t-label">[repokeeper]</span> <span class="t-white">Issue #42: Add dark mode toggle</span></div>
    <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Collecting repository context...</span></div>
    <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Loaded 35 files</span></div>
    <div><span class="t-label">[repokeeper]</span> <span class="t-dim">Calling LLM (deepseek-chat)...</span></div>
    <div><span class="t-label">[repokeeper]</span> <span class="t-white">Plan: Add dark mode CSS toggle and theme context</span></div>
    <div>&nbsp;</div>
    <div><span class="t-dim">→ git checkout -b repokeeper/issue-42-dark-mode</span></div>
    <div><span class="t-dim">→ Created src/theme.py, src/styles/dark.css</span></div>
    <div><span class="t-dim">→ Modified src/App.tsx, src/index.html</span></div>
    <div><span class="t-dim">→ git push origin repokeeper/issue-42-dark-mode</span></div>
    <div>&nbsp;</div>
    <div><span class="t-green">✓ PR opened:</span> <span class="t-highlight">https://github.com/owner/repo/pull/67</span></div>
    <div>&nbsp;</div>
    <div><span class="t-comment"># Review, approve, merge. You never wrote a line.</span></div>
  </div>
</div>

## The Problem

Open source maintenance is a second job you didn't sign up for. Triaging issues,
bumping dependencies, diagnosing flaky CI, responding to community questions —
all *before* you write a single line of code.

Existing tools help **you** write code faster. They don't run your repo while you sleep.

## Why RepoKeeper vs. Copilot

<table class="rk-compare">
  <thead>
    <tr><th></th><th>Copilot / Cursor</th><th>RepoKeeper</th></tr>
  </thead>
  <tbody>
    <tr><td>What it does</td><td>Suggests code as you type</td><td>Maintains your repo autonomously</td></tr>
    <tr><td>How it works</td><td>Inline completion in editor</td><td>Reads issues + codebase → opens PRs</td></tr>
    <tr><td>When it runs</td><td>While you code</td><td>24/7 on schedule</td></tr>
    <tr><td>Community</td><td>No</td><td>Monitors, classifies, responds</td></tr>
    <tr><td>Dependencies</td><td>No</td><td>Scans, upgrades, PRs</td></tr>
    <tr><td>CI</td><td>No</td><td>Diagnoses failures, suggests fixes</td></tr>
    <tr class="rk-highlight"><td>Cost</td><td>$10–39/month sub</td><td>~$0.01/PR with DeepSeek</td></tr>
    <tr><td>Config</td><td>IDE settings</td><td>One YAML (or zero)</td></tr>
  </tbody>
</table>

**They're complementary.** Copilot helps you write code. RepoKeeper runs your repo.

## How to Adopt

<div class="rk-steps" markdown>
  <div class="rk-step">
    <div class="rk-step__num">01</div>
    <div class="rk-step__title">Copy one file</div>
    <div class="rk-step__desc">
      One workflow into <code>.github/workflows/</code>.
      No Python. No install.
    </div>
  </div>
  <div class="rk-step">
    <div class="rk-step__num">02</div>
    <div class="rk-step__title">Add API key</div>
    <div class="rk-step__desc">
      <code>DEEPSEEK_API_KEY</code> secret.
      Free tier available.
    </div>
  </div>
  <div class="rk-step">
    <div class="rk-step__num">03</div>
    <div class="rk-step__title">Trigger the agent</div>
    <div class="rk-step__desc">
      Label <code>agent-todo</code> or comment
      <code>@repokeeper go</code>. That's it.
    </div>
  </div>
</div>

**No `repokeeper.yml` needed.** Sensible defaults handle everything. Add a profile
later when you want custom keywords, code style, or notifications.

[:octicons-arrow-right-24: Full setup guide](setup.md)

## Four Modules

<div class="rk-steps" markdown>
  <div class="rk-step">
    <div class="rk-step__num">🔭</div>
    <div class="rk-step__title">Community Radar</div>
    <div class="rk-step__desc">
      Monitors GitHub for keywords. AI classifies hits. Drafts issues. Sends notifications.
    </div>
  </div>
  <div class="rk-step">
    <div class="rk-step__num">🔍</div>
    <div class="rk-step__title">Daily Patrol</div>
    <div class="rk-step__desc">
      Scans deps, diagnoses CI, finds stale issues. Produces a health score every weekday.
    </div>
  </div>
  <div class="rk-step">
    <div class="rk-step__num">🤖</div>
    <div class="rk-step__title">Implementation Agent</div>
    <div class="rk-step__desc">
      Reads codebase + issue → implements → pushes branch → opens PR. Zero human code.
    </div>
  </div>
  <div class="rk-step">
    <div class="rk-step__num">👤</div>
    <div class="rk-step__title">Maintainer Profile</div>
    <div class="rk-step__desc">
      One YAML for your code style, tone, PR standards. Or skip it — defaults work.
    </div>
  </div>
</div>

[:octicons-arrow-right-24: Explore all modules](module-1-radar.md)

---

*RepoKeeper is MIT licensed. Built by [@shenxianpeng](https://github.com/shenxianpeng).*
