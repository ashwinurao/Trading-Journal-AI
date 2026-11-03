# Trading Journal AI working agreement

Rules for Claude Code and other agents working on this fork
(`ashwinurao/Trading-Journal-AI`, upstream `simonro/Trading-Journal-AI`).
Read [CONTRIBUTING.md](CONTRIBUTING.md) first: its "What the project cares about"
list (local first, deterministic numbers, refuse rather than guess) wins over
anything here.

## Scope and protected logic

- Stop at the requested scope. No new or upgraded dependencies, new network calls,
  external services, public exposure or new open ports unless the owner asked.
- Money logic is deterministic code, never an LLM. A change to trade grouping,
  P&L, fees or statistics needs a test with hand-worked expected values.
- Never change an approved test expectation to make a failure go away. If an
  approved rule changed, list each changed expectation and why in the PR.
- Never put secrets, tokens, account numbers, real trades, `.env` files or
  databases in the repo, logs, screenshots, PRs or docs.
- Live systems: beyond the standard pull and rebuild of `main` on the server, no
  data deletion, token-file changes or other live-container operations without the
  owner's explicit go. The server only ever runs `main`, published on the LAN
  address only (never through a tunnel or to the internet).

## Tests and evidence

- Run `python -m pytest backend/tests -q` before calling work done, and again on
  the PR's head commit after merging the latest `main` in. `main` stays green.
- Frontend changes: `npx craco test --watchAll=false` and `npm run build` in
  `frontend/`, plus a screenshot from a real browser.
- A fix comes with a test that fails without it.
- Keep scratch artifacts in the ignored `.logs/` folder.

## Branches and pull requests

- Branch from the latest `origin/main`. Agent branches are `claude/<topic>`.
- One PR per coherent change. Stack a PR on another only when it truly depends on
  it, and say so in the description.
- PR titles say what changed for the trader, in plain words.
- Describe every PR with [.github/pull_request_template.md](.github/pull_request_template.md).
  Fill every section and write "None" rather than deleting one.
- Docs change in the same PR as the code: README, CONTRIBUTING and anything else
  that describes the changed behavior.
- Merge PRs one at a time.
- Keep edits to upstream files small (`backend/main.py`, `backend/csv_parser.py`,
  `frontend/src/components/Import.js`, README, CONTRIBUTING). Put new logic in new
  files where it fits, so `git pull upstream main` stays easy.

## Ask the owner first

In the thread, with the options and a recommendation. Keep building on the
recommended option only where it is reversible and not protected.

- Money logic rules and approved test expectations.
- New or upgraded dependencies, new network calls or external services
  (including an Anthropic or Alpaca key on the server).
- Anything on the live server beyond the standard pull and rebuild of `main`.
- Turning on GitHub-hosted CI for the fork.

## Style

- Code: match the code around you (PEP 8, snake_case; React function components).
  Comment why, not what, and name the reason or PR a rule comes from. User-facing
  text says what happened and what to do next.
- Writing (docs, PRs, comments): plain direct English, short sentences, present
  tense. Dates `YYYY-MM-DD`, times UTC. Name the file that holds each rule or
  number. If something is not confirmed in code, say so; never guess.
