---
name: master-worker-agents
description: Run a build/refactor/research task as a cost-aware multi-agent team — an Opus "master" plans, splits the work into independent pieces with explicit interfaces, delegates to cheaper Sonnet/Haiku "worker" agents in parallel, then verifies and integrates their output. Use when a task has 3+ separable parts (services, docs, tests, dashboards) or when you want an independent review before shipping.
---

# Master / Worker multi-agent pattern

**Idea:** the expensive, high-judgement model (Opus) does only the things that need judgement —
*decomposing, defining contracts, verifying, and deciding*. Cheaper models (Sonnet, Haiku) do the
bulk typing, in parallel, against a written contract. Nobody trusts anybody's summary: the master
re-runs the tests itself.

## Roles

| Role | Model | Does | Never does |
|---|---|---|---|
| **Master** | Opus | plan, write the contract (`docs/SPEC.md`), pick worker/model per task, integrate, run acceptance tests, final review | bulk file generation, repetitive edits |
| **Worker** | Sonnet | implement one self-contained component end-to-end (code + unit tests) inside the files it owns | touch files it does not own, change the contract, publish/push |
| **Scout** | Haiku | mechanical work: grep/inventory, boilerplate, doc formatting, log triage, renaming | design decisions, security-sensitive code |
| **Reviewer** | Opus (fresh context) | adversarial review of the *integrated* result: secrets, correctness, README-vs-reality | writing features |

Escalation ladder: Haiku → Sonnet → Opus. Escalate when a worker fails the acceptance test twice,
never because a task "feels hard". De-escalate anything repetitive.

## Procedure

1. **Decompose.** List components. A component is delegable only if it has (a) a clear owner-set of
   files, (b) an interface contract, (c) an acceptance command that exits non-zero on failure.
2. **Write the contract first** (`docs/SPEC.md`): ports, env vars, HTTP routes, metric names, JSON
   shapes, file ownership. Contract bugs are the #1 cause of integration failure — spend effort here.
3. **Dispatch in parallel** — one message, several `Agent` calls, each with `model:` set
   (`sonnet` for implementation, `haiku` for mechanical). Each prompt must be self-contained:
   goal, files owned (and forbidden), contract excerpt, acceptance command, "report in ≤150 words".
4. **Verify, don't trust.** Run every acceptance command yourself. Read the diff. A worker saying
   "done, tests pass" is a claim, not evidence.
5. **Integrate** in the master context (compose files, wiring, cross-component fixes).
6. **Independent review** by a fresh Opus reviewer with no knowledge of how it was built.
7. **Fix, re-verify, ship.** Only then publish.

## Hard rules (best practice)

- **Secrets:** API keys live only in git-ignored `.env`. Never put a key in a prompt to a worker,
  a committed file, a log, a test fixture, or a commit message. Grep the tree for it before every push.
- **One owner per file.** Two workers never edit the same file. Overlap → serialise.
- **Workers may not publish** (push, release, delete shared resources). Only the master does.
- **Disprove-first:** acceptance tests must be able to fail. Prefer a ground-truth cross-check
  (e.g. iperf3's own number vs what the dashboard saw) over "the page loaded".
- **Report failures verbatim.** A skipped or failing test is reported as such, never dropped.
- **Keep worker context small:** give paths and excerpts, not whole transcripts.
- **Cost guard:** budget ≈ 1 Opus planning pass + N Sonnet workers + ≤1 Opus review. Use Haiku for
  anything a regex could almost do.

## Worker prompt template

```
You are a <Sonnet|Haiku> worker. GOAL: <one sentence>.
YOU OWN (only edit these): <paths>
DO NOT TOUCH: everything else, especially <paths>
CONTRACT: <paste the relevant SPEC.md section>
ACCEPTANCE (must pass): <command>
RULES: no secrets in files; no network publishing; no placeholder/stub code.
REPORT (<=150 words): files written, acceptance output, anything you could not do.
```

## In this repository

Agent definitions live in `.claude/agents/` (`worker-sonnet`, `scout-haiku`, `reviewer-opus`).
The contract is `docs/SPEC.md`. The acceptance suite is `scripts/e2e_test.sh`.
