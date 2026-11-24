# Issue tracker: GitHub

GitHub Issues for `DaphnisDuck/ddnetmirror` is the sole tracker and the source of truth for planning, decisions, and progress (see `AGENTS.md`). Use the `gh` CLI.

## Conventions

- **Create**: `gh issue create --title "..." --body "..."` (heredoc for multi-line bodies).
- **Read**: `gh issue view <number> --json number,title,body,labels,comments`.
- **List**: `gh issue list --state open --json number,title,labels` with `--label` / `--state` filters.
- **Comment**: `gh issue comment <number> --body "..."`.
- **Labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`.
- **Close**: comment with the explanation, then `gh issue close <number>`.
- **Sub-issues / blocking**: `gh issue edit <parent> --add-sub-issue <child>`, `gh issue edit <child> --add-blocked-by <blocker>`; otherwise put `Part of #<n>` / `Blocked by: #<n>` at the top of the body.

`gh` infers the repo from `git remote -v`. Confirm the target is `DaphnisDuck/ddnetmirror` before creating an issue; never file into `mattpocock/skills`.

## Rules

- Issues are created through Dispatcher-approved workflows or explicitly owner-authorized skill sessions.
- Creating an issue or applying `ready-for-agent` is not implementation authorization. The Dispatcher retains assignment authority.

**PRs as a request surface: no.**

"Publish to the issue tracker" means create a GitHub issue. "Fetch the ticket" means read it as above.
