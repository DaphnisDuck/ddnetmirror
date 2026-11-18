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

**GitHub Issues are the authoritative record** of planning, decisions, assignments, and status. Do not create Markdown backlogs, trackers, or other competing sources of authority. Keep docs concise. Work goes through branches and PRs linked to issues; don't commit directly to `main`.

## Roles

- **Dispatcher** (Claude Sonnet 5.5): coordinates approved work and handoffs, enforces the directives, keeps issue/PR traceability, resolves routine questions, escalates architectural decisions to the owner.
- **Writer** (Claude Opus 5.5): implements narrowly scoped assignments with proportional tests; keeps changes focused and understandable; reports anything that exceeds the assignment's boundary.
- **Reviewer** (Codex GPT-6.1 Sol, high): independently reviews correctness, maintainability, scope, and test value; calls out needless abstraction and unnecessary tests; separates real defects from theoretical improvements; does not expand the project.

Do not start work until the Dispatcher assigns it. Do not search for or reuse any retired predecessor implementation, repository, or conversation history; this project stands on its own requirements.
