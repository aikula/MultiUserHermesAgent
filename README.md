# Multi-User Hermes Agent

Multi-user AI assistant for managers with webapp, Telegram relay, per-user email integration, and controlled action execution.

## What is this

A multi-user deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research) with:
- **Webapp** — FastAPI multi-user interface with auth, chat, profile, email integration
- **Telegram relay** — per-user Telegram bot with file handling
- **Approval flow** — single-confirmation UX for external actions (email, calendar)
- **P0 security** — encrypted secrets, hard quota, CSRF, rate limiting

## Architecture

```
Internet ──► Traefik (:443)
              ├─► hermes-dashboard :9119  (admin UI)
              ├─► hermes-webapp   :9000   (multi-user chat)
              └─► hermes-gateway  :8642   (Hermes API)
                   │
                   └─► /opt/data → /root/.hermes (bind-mount)
```

### Services

| Service | Port | Description |
|---|---|---|
| `hermes-gateway` | 8642 | Hermes Agent API (OpenAI-compatible) |
| `hermes-webapp` | 9000 | Multi-user FastAPI webapp + Telegram relay |
| `hermes-dashboard` | 9119 | Admin dashboard (basic-auth) |

## Quick Start

```bash
# Setup
cp .env.example .env.hermes
chmod 600 .env.hermes
$EDITOR .env.hermes   # fill in real values

# Run
docker compose --env-file .env.hermes up -d

# Verify
docker ps --filter name=hermes
curl -fsS http://localhost:9000/health
```

## Webapp Features

- **Auth** — register with invite code, login, session cookies
- **Chat** — per-user memory scoping via `X-Hermes-Session-Key`
- **Profile** — name, password, SOUL.md, email settings
- **Email integration** — IMAP/SMTP with encrypted credentials
- **Telegram relay** — `@aik_hermesbot` with file handling
- **Approval flow** — single confirmation for external actions
- **Manager templates** — 6 demo scenarios (email, meeting, tasks, etc.)

## Security

### P0 Hardening (implemented)

- **Secrets not in LLM prompt** — email passwords never exposed to model
- **Encryption at rest** — Fernet encryption for email credentials
- **Hard quota** — blocks requests when `quota_remaining <= 0`
- **Rate limiting** — 10 login attempts per 5 minutes
- **CSRF protection** — token validation for browser POST
- **Secure cookies** — `httponly`, `samesite=lax`, `secure`
- **File upload safety** — UUID filenames, dangerous extension rejection

### Security Model

- Each user has isolated memory, files, and credentials
- External actions (email send) require single confirmation
- Internal endpoints use `X-Internal-Secret` header
- All secrets in `.env.hermes` (chmod 600, not in git)

## Configuration

| File | Purpose | In git? |
|---|---|---|
| `docker-compose.yml` | Service definitions | Yes |
| `.env.example` | Secret template | Yes |
| `.env.hermes` | Actual secrets | No |
| `/root/.hermes/config.yaml` | Agent config | No |

## Testing

```bash
cd webapp
pip install -r requirements.txt -r requirements-dev.txt
pytest -q           # 77 tests
ruff check app      # lint
bandit -r app -ll   # security
```

## Environment Variables

Required:
- `HERMES_API_KEY` — gateway API key
- `WEBAPP_INTERNAL_SECRET` — internal API secret
- `JWT_SECRET` — session signing key
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_ADMIN_CHAT_ID` — admin notifications

Optional:
- `ENCRYPTION_KEY` — email credential encryption
- `WELCOME_QUOTA` — initial token quota (default: 2M)
- `ALERT_THRESHOLD_PCT` — quota alert threshold (default: 80%)

## License

Deployment code — MIT. Hermes Agent — MIT (Nous Research).

## Links

- Hermes Agent: https://github.com/NousResearch/hermes-agent
- Documentation: https://hermes-agent.nousresearch.com/docs/
- Discord: https://discord.gg/NousResearch
