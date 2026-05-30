# Gatekeep

An AI slop firewall for open source. Gatekeep is a multi-agent GitHub App that reads every new issue and pull request, gathers grounded evidence, and posts a specific, polite comment when a contribution looks like low-effort or AI-generated noise. It never closes anything by default. A human still decides.

## Why

Maintainers are drowning in low-effort and AI-generated contributions: hallucinated function names, whitespace-only diffs that claim to "fix the auth bug," duplicate issues with no reproduction. Gatekeep catches the noise by symptom, not by guessing whether an AI wrote it, and it leaves every real contribution untouched.

## How it works

Two services, one seam:

```
                   GitHub
                     |  webhook              ^  comment / label
                     v                       |
        +-------------------------------+
        |  Gateway  (TypeScript)        |   verify, authenticate,
        |                               |   normalize, clone, execute
        +-------------------------------+
                     |  HTTP /process        ^  verdict + actions
                     v                       |
        +-------------------------------+
        |  Brain  (Python + LangGraph)  |   Intake -> Investigator
        |                               |   -> Judge -> Responder
        +-------------------------------+
                     |  reads a local clone (filesystem only)
                     v
                  repo files
```

- **Gateway (TypeScript):** verifies the webhook, authenticates as the App, normalizes the event, produces a local clone, and executes the actions the Brain returns. It is the only thing that touches the GitHub API.
- **Brain (Python + LangGraph):** four agents (Intake, Investigator, Judge, Responder) decide what to do. It reads repo content from the local clone and returns intended actions as JSON. It never calls GitHub.

Two invariants make the design defensible:

1. **The Gateway decides nothing.** No thresholds, no classification, no business logic in TypeScript. It authenticates, normalizes, and executes vetted actions.
2. **The Brain never touches GitHub.** It has no token and no client. It is handed a directory, so it is structurally incapable of acting on GitHub.

The agent team separates evidence from judgment from action. The Investigator gathers grounded check results, the Judge weighs only those findings into a verdict, and the Responder drafts the comment and runs a policy gate before anything is executed. The whole run is one JSON object, so every decision is logged, replayable, and testable offline.

## What it checks (v1)

Static repo reads plus existing CI status. No untrusted code is executed.

- **cited symbols exist:** does a function the contribution references actually exist in the repo
- **diff matches description:** does the diff do what the body claims
- **cosmetic only:** is the diff pure formatting with no behavior change
- **touches real files:** are the changed paths real
- **ci status:** is existing CI already failing
- **issues:** duplicate detection, reproduction present, version coherence

Each check returns a human-readable evidence string, and that string becomes the comment. Verdicts are never bare scores.

## Example

A PR titled "Add token validation to login" whose diff only reformats whitespace and whose body cites a `validateToken()` that does not exist:

> @contributor thanks for the PR. I noticed a few issues: the description mentions `validateToken()` but that function doesn't exist anywhere in the repository. The diff only changes whitespace, there's no behavioral change. This means the code change doesn't match what the description says it does. Please review.

Label applied: `possible-slop`. The PR stays open, because the default posture is read-only.

## Policy and autonomy

A per-repo `.gatekeep.yml` controls posture. The default is `suggest-only`: comment and label, never close. The autonomy dial is a single config value. Legit contributions receive no action at all, so Gatekeep is invisible to good contributors and loud only on slop.

```yaml
mode: suggest-only      # suggest-only | auto-gate
threshold: 0.85         # min confidence to auto-close under auto-gate
```

## Tech

- **Gateway:** TypeScript, Node, Octokit, vitest
- **Brain:** Python, LangGraph, Pydantic, FastAPI, pytest
- **Models:** via OpenRouter, model-agnostic, bring your own key

## Local development

Gatekeep runs as two local processes plus a webhook relay.

1. Register a GitHub App. Permissions: Issues read and write, Pull requests read and write, Contents read. Subscribe to Issues and Pull request events. Note the App ID, generate a private key, and pick a webhook secret.
2. **Brain:** `cd brain && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`. Put `OPENROUTER_API_KEY` in `brain/.env`, then run `uvicorn server:app --port 8000`.
3. **Gateway:** `cd gateway && npm install`. Copy `.env.example` to `.env` and fill the App credentials, then run `npm start`.
4. **Relay webhooks** to your laptop with smee: `npx smee-client --url <your smee channel> --target http://localhost:3000`.

Run the tests with `cd brain && pytest -m "not golden"` and `cd gateway && npm test`.

## Status

v1 is a read-only slop firewall, proven end to end on a dogfood repo through real GitHub webhooks. On the roadmap: hosted always-on deployment, a sandbox executor that builds untrusted PRs in isolation, and a conversational maintainer-assistant tier.
