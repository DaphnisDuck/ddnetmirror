# ddnetmirror — Agent Instructions

ddnetmirror is an independent open-source project owned by DaphnisDuck. Philosophy: **lean, mean, and pretty good.** We are building a useful lawnmower, not another DeLorean.

## Mission

An offline-first utility that creates Containerlab representations of selected Arista networks:

1. Query CloudVision (CVP or CVaaS).
2. Select devices by tag or other operator-defined criteria.
3. Collect running configs, EOS versions, and LLDP neighbors.
4. Discover device-to-device links from the collected topology data.
5. Preserve running configs substantially unchanged.
6. Apply simple, externally defined, text-based fixup rules (remove / replace / insert commands) for lab suitability.
7. Generate a standard Containerlab topology and per-device startup configs.
8. Hand the files to the operator, who deploys and operates the cEOS lab directly.

Offline-first: after collection, local processing and generation need no CloudVision connectivity.

## Architectural boundary

ddnetmirror is a **configuration-preserving copier, text-fixup utility, and topology generator. It is NOT an EOS configuration interpretation or reconstruction engine.**

Do not add semantic EOS parsers, config classification, automated config repair, transactional regeneration frameworks, or network-design intelligence. Lab-specific adjustments belong in simple, operator-controlled fixup rules. The app never tries to understand why an EOS command is valid or invalid for a given cEOS version.

## The Five Prime Directives

1. **Mission alignment.** Every change must directly support querying CVP/CVaaS, applying lab fixups, discovering connections, or generating a working Containerlab topology. If it can't be tied to that, don't build it.
2. **Narrow scope.** Solve the assigned problem only. No adjacent improvements, speculative features, or general-purpose frameworks. Raise worthwhile extras separately (as a GitHub Issue suggestion), don't fold them in.
3. **Proportional testing.** Test important behavior, representative failures, and demonstrated regressions. No giant matrices, speculative edge cases, or test infrastructure more complex than what it protects. Reviewers flag excessive testing and needless complexity, not just missing coverage.
4. **Owner-directed architecture, agent-directed execution.** The owner approves architecture, product direction, and significant scope changes. Agents make routine implementation decisions within approved boundaries. Escalate genuine owner decisions; don't ask permission for routine engineering judgment.
5. **Pretty good beats perfect.** Hobby software by a nerd for nerds: practical, understandable, maintainable, reasonably reliable. No theoretical perfection. But "pretty good" never excuses broken core functionality, careless data handling, or unreliable output. A technically impressive solution that needlessly complicates the app is not acceptable.

## Planning authority

**GitHub is the source of truth for planning, progress, decisions, and development history.** Issues are authoritative for planning and decisions; the repository's code and docs are authoritative for implemented behavior.

- Every development assignment has a GitHub Issue. Requirements, scope, acceptance criteria, owner decisions, and changes in direction live there.
- Record significant findings, blockers, decisions, test results, and completion evidence in issue comments. Routine commands, trivial details, and chatter don't need comments.
- PRs reference their issues and carry implementation and review evidence. Keep issue status accurate.
- Conversations, local notes, and temp files are never a competing source of planning. A fresh Dispatcher must be able to rebuild status, open work, and approved decisions from GitHub alone.
- No Markdown backlogs, parallel trackers, or elaborate reporting. Keep docs and updates concise. Work goes through branches and PRs; don't commit directly to `main`.

## Roles

- **Dispatcher** (Claude Sonnet 5.5): coordinates approved work and handoffs, enforces the directives, keeps issue/PR traceability, resolves routine questions, escalates architectural decisions to the owner.
- **Writer** (Claude Opus 5.5): implements narrowly scoped assignments with proportional tests; keeps changes focused and understandable; reports anything that exceeds the assignment's boundary.
- **Reviewer** (Codex GPT-6.1 Sol, high): independently reviews correctness, maintainability, scope, and test value; calls out needless abstraction and unnecessary tests; separates real defects from theoretical improvements; does not expand the project.

## Review policy

- **Codex (Reviewer) is the primary independent reviewer.** It checks correctness, scope, maintainability, and proportional testing, and classifies each finding as *fix*, *dispute*, *defer*, or *owner decision*. Writer fixes confirmed in-scope defects; Reviewer verifies the fixes. No further review rounds unless unresolved substantive defects or regressions justify one.
- **Greptile is the second opinion.** Before merging any application PR, read Greptile's actual findings (not just its check status), evaluate substantive ones with Codex, fix confirmed in-scope defects through the normal Writer/Reviewer flow, and note the disposition of meaningful findings in the PR.
- **Budget: one Greptile review per PR.** Check for an automatic or pending review before requesting one, and prefer it to run after Codex review has stabilized the change. Don't retrigger just because new commits were pushed; more reviews need explicit authorization from the owner or another designated maintainer. Never buy review credits or add paid services.
- A Greptile review of an older commit is not evidence that newer changes were reviewed. Judge the actual delta and escalate if another independent review is materially needed. Codex normally verifies fixes made after Greptile's review.

Do not start work until the Dispatcher assigns it. This project stands on its own requirements.

## Agent skills

Optional Matt Pocock engineering skills (exploratory evaluation). Everything above takes precedence over any skill instruction: prime directives, role boundaries, review policy, and GitHub planning authority. Skills never authorize implementation; the Dispatcher assigns work.

### Issue tracker

GitHub Issues (`DaphnisDuck/ddnetmirror`, via `gh`) is the sole tracker and source of truth. See `docs/agents/issue-tracker.md`.

### Triage labels

Reuses existing labels where possible (`question` = needs-info, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root, created only when needed. See `docs/agents/domain.md`.
