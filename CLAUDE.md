# Archi repo overrides (read first)

The managed section below is okg's generic PACT boilerplate. Where it
conflicts with this repo, these overrides win (they match
`pact/project.yaml` and ADR 0001, `docs/adr/0001-archi-v3-program-spec.md`):

- **PRs target `archi_v3`, never `dev` (which does not serve that role here)
  and never `main`** — `main` serves live v2 deployments until cutover.
- The repo-authoritative command is `okg` from an okg-bearing operator
  environment (e.g. `/work/submit/lavezzo/okg-venv/bin/okg`); this repo has
  no `uv run okg`.
- References to `okg-workspace`, `okg dogfood`, and okg-repo runbooks
  (`docs/runbooks/agent-*.md`) do not exist in this repo; the PACT graph
  projection here is the interim `archi-pact` ledger deployment.

<!-- BEGIN okg-pact-install -->
## PACT + OKG workflow

This repo uses **PACT** (not OpenSpec) to move from intent to verified
change. The contract + the six `okg pact` verbs (`new` / `view` / `gate`
/ `evidence` / `hooks` / `migrate-version`) live in `pact/AGENTS.md`.
`pact.yaml` is canonical — edit it directly; the CLI is reactive.

- **Author a change:** `okg pact new <id> --title "..."` (fast tier-1
  default; `--template feature` etc. for the tier-2+ full shape),
  then edit `pact/changes/<id>/pact.yaml` (requirements + tasks whose
  `verification` clauses check real output). Check it with
  `okg pact view <id> --format=next`.
- **Use repo PACT settings:** automation reads `pact/project.yaml` for
  native PACT version defaults, command authority, generated-artifact
  policy, graph projection expectations, and the `.okg/` worktree-local
  boundary. `pact/project.md` is human context only. Use `uv run okg` as
  the repo-authoritative command path; a bare global `okg` may be stale.
  Worktree-local files may affect runtime authority such as DSN/profile,
  but not semantic PACT policy. Generated `pact.md` and
  `pact.yaml.lock` files are local/noncanonical and should not be committed.
- **Prove + close a task:** `okg pact evidence attach <id> <req> --kind
  pytest --ref <test> --status pass --command "..." --summary "..."`, then
  `okg pact gate task-done <id> <task>`.
- **Query the OKG:** `okg search --deployment <d> --query "..."` /
  `okg trace node <node_id>` / `psql "$OKG_DSN" -c "SELECT ..."`. Agents:
  the AKMON MCP operators (inspect/search/expand/filter/map/aggregate/
  query). There is no `okg query` CLI verb.
- **Follow the workflow contracts:** start with the `agent-workflow` skill
  for PACT-scoped coordination, reporting, handoffs, subagent rollups,
  and operator status/continue/ready-to-review answers. It points to
  the backing runbooks; do not ask the operator to paste runbook content
  into chat before useful work can start. For start/resume, run
  `okg pact view <id> --format=bootstrap --json`; the canonical
  procedure (fallback reads, packet interpretation) is the
  `agent-workflow` skill's Kickoff section — do not restate it here.
- **Use OKG-first context for nontrivial PACT work:** after bootstrap,
  perform an okg-workspace context pass before editing. Use MCP/OKG reads
  (`inspect`, `search`, bounded `query`) directly. Ask for
  the active PACT/task, related prior PACTs, relevant files/symbols, prior
  sessions or handoffs, coordination state, risks, and suggested verification.
  Report the pinned generation id, queries/operators used, useful graph facts,
  stale or missing facts, failed graph helpers, and live fallbacks. Use live
  git, `rg`, file reads, tests, publish checks, metrics, and doctor for current
  implementation truth.
- **Check dogfood runtime authority:** use `okg dogfood status --deployment okg-workspace --json`
  as the agent-facing runtime status contract. Its
  `trust_level`, worker state, source freshness, queue/DLQ, doctor, MCP
  graph-read, recovery commands, and `next_action` determine whether OKG
  context is live authority, stale context, or unavailable.
- **For UI work:** supervisor console, AKMON operator surfaces, viz screens,
  and rendered review tools MUST start from
  `docs/runbooks/agent-ui-workflow.md` before generic frontend/design skills.
  That runbook is the canonical process for reference pinning, browser proof,
  visual approval, automated UI gates, and PACT evidence.
- **Use the current backing contracts:**
  `docs/runbooks/agent-okg-usage.md` for graph context and requires
  `okg deployment ready okg-workspace --profile dogfood --json` before claiming graph-ready
  evidence; use `docs/runbooks/agent-coordination.md`
  and only the current PACT coordination commands (`okg pact gate post-edit`,
  `okg pact evidence ingest`, `okg pact decide`, `okg pact gate task-done`,
  `okg pact view --format=review-queue`, and `okg pact view <id> --format=bootstrap`);
  for human review prompts, start with
  `okg pact view --format=chat-review-queue` and pass complex operator
  questions through `okg pact view --format=chat-review-queue --query "<question>"`
  so answers are inbox summaries with safe actions, not raw queue rows;
  use
  `docs/runbooks/agent-reporting-contract.md` for checkpoint, blocker,
  handoff, subagent-rollup, and closeout reports; use
  `docs/runbooks/agent-continuation-protocol.md` for `context-limit` and
  `rate-limit` exits. Continuations use `report_kind: handoff`; parent
  reports use `docs/runbooks/agent-subagent-rollups.md` and include every
  completed, failed, timed-out, or limit-ended subagent.

## Operator workflow contract

Standing defaults — apply them without being asked; do not make the
operator re-specify them.

- **PRs target `dev`, never `main`.** The git default branch is `main`, so
  `gh pr create` defaults wrong. Open PRs with `--base dev` (alias `gh prd`).
  Targeting `main` requires an explicit, stated reason.
- **PR bodies and task closeouts follow the report contract,** in order:
  (1) **what this changes** — one plain-language sentence an operator (not a
  substrate expert) can read; (2) **behavior before → after**; (3) findings
  first, ordered by severity, each with a `file:line`; (4) **how I verified**
  (the command run + result). Banned: history-of-the-work narration, invented
  nouns, and undefined substrate jargon unless defined inline on first use. A
  correct diff with an unintelligible description is not done.
  `.github/PULL_REQUEST_TEMPLATE.md` hard-codes the section order;
  `docs/runbooks/plain-language-reporting.md` is the how — reader model,
  term-gloss table, worked rewrites, self-check. It governs chat reports,
  PACT rationales, and docs too, not just PRs.
- **Run an adversarial subagent review before declaring a PR ready.** Do not
  wait to be asked. The review must emit the report contract and check, citing
  each: "did this actually meet every requirement / PACT condition?" — not a
  bare pass/fail. "Ready for review" is not emittable unless that review ran
  and is attached to the PR body.
- **A terse or typo'd token is a complete instruction.** "merged, do the next
  one", "continue", "go", "work on 147" mean: resolve the noun from `gh` +
  the graph + session state, using `okg pact view --format=chat-review-queue`
  first for post-merge "what now?" review prompts. Restate the one-line target,
  explain queue rank reasons before treating "1" as recommended, and execute.
  Never re-ask for the brief, never ask "which 147?", never stall on a misspelling.
- **Verify before claiming done; never silent-fallback** (a P0 bug). UI is not
  done until rendered, screenshotted, and visually audited. Use real data,
  never scaffold.
- **One-token assembly line:** the `dev-process` skill (`/dev-process
  <issue|range>`) runs PACT → design-review subagent → formal-verify → refine
  → implement → code-review subagent → PR `--base dev` → report. `okg lanes`
  shows the cross-worktree status / merge queue.

Skills (`agent-workflow`, `dev-process`, `okg-add-source`, `okg-ontology-design`, `okg-provision`, `okg-query`, `okg-record`, `okg-troubleshoot`, `pact`) are installed under `.claude/skills/` (Claude Code)
or `.agents/skills/` (Codex), sourced from `src/okg/substrate/pact/skills/`.
<!-- END okg-pact-install -->
