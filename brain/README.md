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
DEEPSEEK_API_KEY=... pytest -m golden   # real-model golden set (key = default provider's api_key_env)
```

## Config

A repo opts in with `.gatekeep.yml`:

```yaml
mode: suggest-only      # suggest-only | auto-gate
threshold: 0.85         # min confidence for auto-gate to close
```

## Models

All LLM access goes through `core/llm.py`; no agent imports a provider SDK.
Providers and the per-agent model map live in `config/models.toml` (committed,
no secrets); API keys come from per-provider env vars.

```toml
[providers.deepseek]
client      = "openai"
base_url    = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
extra_body  = { thinking = { type = "disabled" } }

# thinking on; json_mode since thinking disallows forced tool_choice
[providers.deepseek-think]
client            = "openai"
base_url          = "https://api.deepseek.com"
api_key_env       = "DEEPSEEK_API_KEY"
extra_body        = { thinking = { type = "enabled" } }
structured_method = "json_mode"

[roles]
default = "deepseek:deepseek-v4-pro"
judge   = "deepseek-think:deepseek-v4-pro"   # reasoning stage runs with thinking on
```

- **Keys:** set the env var each provider names in `api_key_env`
  (`OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, ...). Keys never live in the file.
- **Roles:** `default`, `intake`, `checks`, `judge`, `responder`. A role maps to
  `"provider:model"`; unlisted roles fall back to `default`.
- **Config path:** override with `GATEKEEP_MODELS_CONFIG`.
- **Adding a non-OpenAI-wire provider** (e.g. Anthropic native): register a
  builder in `core/llm.py` `BUILDERS` and set that provider's `client`, with no
  call-site changes.
