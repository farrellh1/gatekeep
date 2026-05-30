# Gatekeep Brain

The decision engine for Gatekeep's slop firewall. Takes a normalized GitHub
event, returns a verdict (`slop` / `needs-info` / `legit`) and the gated actions
to take. Pure JSON in, JSON out — no GitHub access lives here.

## Flow

```
event → intake → investigator → judge → responder → verdict + actions
                 (run checks)   (decide)  (draft + gate)
```

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Test

```bash
pytest                      # offline suite (llm calls mocked); golden set auto-skips
OPENROUTER_API_KEY=... pytest -m golden   # real-model golden set (slop caught, legit passes)
```

## Config

A repo opts in with `.gatekeep.yml`:

```yaml
mode: suggest-only      # suggest-only | auto-gate
threshold: 0.85         # min confidence for auto-gate to close
```

## Model

LLM access goes through `core/llm.py` over OpenRouter — model-agnostic, set
`GATEKEEP_MODEL` and `OPENROUTER_API_KEY`.
