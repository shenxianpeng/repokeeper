
## Unreleased



### Added


- add Pi backend and multi-turn conversation context by @shenxianpeng (96d8627)

- enable Pi backend for PR fix mode and dogfooding by @shenxianpeng (8682f7b)



### Changed


- update deployed agent workflow to match template by @shenxianpeng (183083a)

- use uses: ./<module> to call local actions in all workflows by @shenxianpeng (b84b965)

- install Node.js + Pi in agent action for pi backend support by @shenxianpeng (6cb6a8f)

- simplify architecture diagram to 6-grouped-edge pipeline by @shenxianpeng (49476c5)

- auto-detect local checkout for dogfooding install by @shenxianpeng (8e08353)

- auto-sync CHANGELOG.md with git-cliff on every push by @shenxianpeng (ed2f5ae)

- update README and docs for v1.3 — Pi, PR fix, inline review by @shenxianpeng (6b01e6b)

- update CHANGELOG.md [skip ci] (b278b7a)

- accurate 4-way comparison table with Copilot, CodeRabbit, PR-Agent by @shenxianpeng (3927e5b)



### Fixed


- stop auto-adding repokeeper-labeler on every labeled issue by @shenxianpeng (a376d35)

- add actions/checkout before uses: ./<module> in all workflows by @shenxianpeng (1b391aa)

- handle empty PR_NUMBER env var gracefully by @shenxianpeng (4d13a4e)

- robust Pi output parsing, clearer JSON instructions by @shenxianpeng (bf405da)

- log Pi stderr, surface crash errors in issue comments by @shenxianpeng (52ffcd0)

- set DEEPSEEK_API_KEY env for Pi deepseek provider by @shenxianpeng (a4b97b5)

- pass --provider to Pi explicitly (defaults to google) by @shenxianpeng (8a3c90b)

- warn about untracked files before Pi runs by @shenxianpeng (4097ebf)

- replace git add -A with git add -u + explicit new file staging by @shenxianpeng (45c652c)


## v1.2.0 - 2026-05-10



### Added


- inline PR review comments, describe, similar-issue detection, incremental re-review by @shenxianpeng (be9ad30)

- Add codecov.yml with project/patch coverage thresholds and PR comments by @shenxianpeng (008ee90)

- Add GitHub Discussions community badge and section to README and docs nav by @shenxianpeng (8368a32)

- Add LLM API connectivity validation to repokeeper doctor by @shenxianpeng (d314b58)

- Add repokeeper pricing command with staleness check and env override hints by @shenxianpeng (75c69d9)

- Add JSON-structured logging via --log-format and RKP_LOG_FORMAT by @shenxianpeng (7dc4b93)

- Add benchmarks documentation with cost/performance estimates per scenario by @shenxianpeng (5a89c78)

- Add comprehensive tests for LLM client: Anthropic, streaming, JSON repair — coverage 56%→93% by @shenxianpeng (21bc107)

- Add integration-level tests: agent pipeline, verification fix loop, branch collision — coverage 82%→84% by @shenxianpeng (df516cb)

- add conversational PR fix mode by @shenxianpeng (e091ccc)



### Changed


- remove doctor action by @shenxianpeng (8ae43b6)

- update docs for inline review, describe, similar-issue detection, incremental re-review by @shenxianpeng (e2c1860)

- Revert "Add GitHub Discussions community badge and section to README and docs nav" by @shenxianpeng (1eaa64b)

- make overview concise, put all issue details in inline comments by @shenxianpeng (1d8f3eb)

- add Mermaid architecture diagram to README by @shenxianpeng (665785e)

- redesign architecture diagram for vertical flow by @shenxianpeng (56a9d16)

- change trigger from @repokeeper to /repokeeper by @shenxianpeng (f2e9174)


## v1.1.0 - 2026-05-07



### Added


- enhance implementation change handling and context expansion by @shenxianpeng (f86403f)



### Changed


- update AGENTS.md to refine conversational style and clarify git rules for parallel agents by @shenxianpeng (24eb74d)

- auto-move v1 tag on release by @shenxianpeng (7940811)



### Fixed


- update deploy condition to include workflow_dispatch event by @shenxianpeng (b7797bb)

- fix CI scan, dep count display, and add step logs to LLM diagnosis by @shenxianpeng (29e804b)

- resolve mypy errors in repo_context.py, add type-check requirement to AGENTS.md by @shenxianpeng (e5838a7)


## v1.0.0 - 2026-05-07



### Added


- add Auto-Labeler module (Module 5) with docs by @shenxianpeng (64bd0f0)

- per-module AI model selection (labeler/radar/patrol/review) by @shenxianpeng (d3eb23d)

- composite GitHub Actions — one action per module by @shenxianpeng (05a48a3)



### Changed


- chore(deps): bump taiki-e/install-action from 2.75.30 to 2.77.1 in the actions group by @dependabot[bot] in #20

- add dogfood labeler workflow for auto-labeling issues and PRs by @shenxianpeng (1418375)

- fetch repo labels once in batch mode instead of per-issue by @shenxianpeng (55b4fac)

- update tool comparison — modernize Copilot/Cursor, add PR Agent by @shenxianpeng (ba4f919)

- update CHANGELOG for v1.0.0 by @shenxianpeng (0a5b8ca)



### Fixed


- resolve mypy any-return error in get_module_model by @shenxianpeng (ad2ddb5)


## v0.10.0 - 2026-05-06



### Added


- two-step smart file selection and verification fix loop (ff602de)

- add Code Review Agent (Module 5) (86fbd3e)

- add review workflow for dogfooding (b837f44)



### Changed


- update changelog for version 0.10.0 by @shenxianpeng (01a2a5f)



### Fixed


- add mypy type ignore for check_review_skip_keywords (ea63fd4)


## v0.9.0 - 2026-05-06



### Added


- Add agent candidate handoff workflow by @shenxianpeng in #12



### Changed


- update changelog for version 0.9.0 (2d0ee8a)



### Fixed


- fix: 10 code quality and safety improvements by @shenxianpeng in #13


## v0.8.0 - 2026-05-05



### Added


- add cross-repo global search by @shenxianpeng (4e87f24)



### Changed


- add repokeeper badge and styling in README by @shenxianpeng (66a2eab)

- update example project name in Community Radar documentation by @shenxianpeng (22df5fb)

- add edge case tests for collect_repo_files and apply_and_push functions by @shenxianpeng (3788f58)

- update changelog for version 0.8.0 with new features and documentation updates by @shenxianpeng (eab8db6)


## v0.7.0 - 2026-05-05



### Added


- auto-create issues from community hits with deduplication and branding by @shenxianpeng (eacd1d0)



### Changed


- update changelog for version 0.6.0 with new features and fixes by @shenxianpeng (fc1762c)

- update changelog, readme, radar docs, and workflow for auto-create feature by @shenxianpeng (729678d)


## v0.6.0 - 2026-05-05



### Added


- make llm cost estimates configurable by @shenxianpeng (80cad48)



### Changed


- limit source distribution contents by @shenxianpeng (03457c0)

- raise coverage gate by @shenxianpeng (2246a9b)

- clarify workflow install paths by @shenxianpeng (cab1288)



### Fixed


- constrain llm file writes to repo by @shenxianpeng (873be6b)

- update commit preprocessors and parsers for better categorization by @shenxianpeng (d0d67c2)


## v0.5.0 - 2026-05-04



### Added


- improve setup diagnostics by @shenxianpeng (dcf804d)



### Changed


- sync README workflow setup with quick-start guide by @shenxianpeng (f42f356)

- document security model by @shenxianpeng (aaa7bf4)

- add contribution templates by @shenxianpeng (1db86a8)

- add doctor to quick start by @shenxianpeng (8e03e77)

- add uv.lock by @shenxianpeng (10c200e)



### Fixed


- include workflow templates in package by @shenxianpeng (00f5c88)


## v0.4.0 - 2026-05-03



### Added


- P4 LLM enhancements — Anthropic, streaming, token tracking, branch collision by @shenxianpeng (b6729a8)



### Changed


- rewrite adoption section with copy-workflow, CLI, and AI-prompt approaches by @shenxianpeng (dede402)

- Replace release-drafter with git-cliff for draft releases by @shenxianpeng (18d2d4b)

- Update cliff.toml section names to Keep a Changelog style by @shenxianpeng (1c95cf6)

- comprehensive P1 test coverage — patrol 25%→87%, radar 30%→89%, total 67%→89% by @shenxianpeng (bed609b)

- P2 architecture improvements — mypy, logging, split agent.py by @shenxianpeng (77f53d6)

- prepare 0.4.0 release — changelog, README, all module docs by @shenxianpeng (4bca2b0)



### Fixed


- patrol report footer links to repokeeper repo, not the target repo by @shenxianpeng (25e8e5a)

- implement all P0 features — discussion scanning, CI log fetching, CI auto-fix, multi-ecosystem dep checks by @shenxianpeng (f626a5b)

- CI mypy errors — missing type stubs and Any-return in radar.py by @shenxianpeng (a190154)


## v0.3.0 - 2026-05-03



### Added


- Add GitHub Actions workflow for RepoKeeper Agent by @shenxianpeng (7f0ad30)

- Add initial configuration for RepoKeeper by @shenxianpeng (03d8577)

- Add initial README with project title by @shenxianpeng (3dcb62e)

- implement all four RepoKeeper modules with full documentation by @shenxianpeng (616084d)

- prepare repokeeper for packaging and ci by @shenxianpeng (af2b14d)

- add --summary flag and upload radar-summary artifact by @shenxianpeng (09536e5)

- improve onboarding cli checks by @shenxianpeng (a75fdce)

- verify agent changes before pr by @shenxianpeng (39c07cf)

- inject CHANGELOG latest section into release draft body by @shenxianpeng (dd9f9e9)



### Changed


- Create agent.py by @shenxianpeng (38e0590)

- Correct copyright holder name by @shenxianpeng (ab59ce1)

- support python 3.10 through 3.14 by @shenxianpeng (c8e4443)

- remove dead .github/repokeeper/agent.py wrapper (CLI handles this) by @shenxianpeng (859537f)

- bump version to 0.2.0, add dependabot config by @shenxianpeng (5443524)

- group all deps into single PRs by @shenxianpeng (8a80ee8)

- chore(deps): bump the actions group with 5 updates by @dependabot[bot] in #7

- add comprehensive tests achieving 95+% agent, 100% profile, 90% cli coverage by @shenxianpeng (400c9e3)

- add AGENTS.md and fix all lint issues by @shenxianpeng (2d0d3c8)

- redesign README and docs with AI-project feel, Copilot comparison, zero-config adoption by @shenxianpeng (9b95be5)

- pipguard-style redesign with custom CSS, logo, hero section, terminal mockup by @shenxianpeng (121a8fd)

- update documentation and assets for improved clarity and design by @shenxianpeng (9aac522)

- align setup guide with current capabilities by @shenxianpeng (a04d941)

- refresh mkdocs visual design by @shenxianpeng (5caeef9)

- sync template workflow action versions with own repo by @shenxianpeng (5656526)

- migrate to setuptools-scm for automatic versioning by @shenxianpeng (468cefb)



### Fixed


- block .github/workflows/ modifications to prevent push rejection by @shenxianpeng (92296c1)

- handle 404 on get_repo with GITHUB_TOKEN fallback by @shenxianpeng (8cf4185)

- write radar/patrol summary to GITHUB_STEP_SUMMARY by @shenxianpeng (aaedb8e)

- auto-strip blocked paths instead of skipping whole issue by @shenxianpeng (73282cf)

- add real-time logging and resilient LLM JSON parsing by @shenxianpeng (c3a4d2b)


<!-- generated by git-cliff -->
