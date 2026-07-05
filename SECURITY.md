# Security & API Keys

## Secrets policy

**No real API keys, tokens, or credentials belong in this repository.**
All Stage-1 (vision) and Stage-2 (reasoning) LLM providers are configured
through environment variables loaded from `backend/.env`, which is
git-ignored.

### Where keys are loaded

- `backend/.env` (git-ignored) is the single source of truth for local dev.
  See `backend/.env.example` for the full list of variables.
- CI/CD and container deployments should inject the same variables via
  the runtime environment (e.g. `docker run -e ARK_API_KEY=...`,
  Kubernetes secrets, etc.).
- Any `backend/_llm_*.py`, `backend/_kimi_*.py`, `backend/_vl_*.py`,
  `backend/_perf*.py`, `backend/_probe_*.py`, `backend/_bench*.py`
  files are treated as **local scratch** and are git-ignored. If you
  paste a key into one of these files for a one-off probe, it stays on
  your machine.

### Variables used by the AI-coach chain

The Stage-2 provider chain in `backend/fitness_agent/vision_pipeline.py`
reads these keys (all optional; the chain will skip a provider that
isn't configured):

| Variable | Provider |
|---|---|
| `BAILIAN_API_KEY` | Aliyun Bailian (qwen3.7-max, kimi-k2.7-code, deepseek-v4-pro, qwen3.6-flash, deepseek-v4-flash) |
| `QWEN_API_KEY` | Aliyun DashScope legacy (qwen-plus) |
| `VOLC_ARK_API_KEY` | Volcano Ark (doubao Seed 1.6) |
| `DEEPSEEK_API_KEY` | DeepSeek direct |
| `MOONSHOT_API_KEY` | Moonshot (Kimi) direct |
| `HUNYUAN_API_KEY` | Tencent Hunyuan |
| `JWT_SECRET` | Backend JWT signing key (must be strong random in prod) |

## Reporting a leaked key

If you spot a real key in a commit, push, or issue:

1. Rotate the key with the provider **immediately**.
2. Open a private issue or email the repo owner.
3. Do not comment the key in a public issue.

## Rotation history

See `SECURITY_ROTATION.md` for the v1.0 rotation record.
