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

- **Auth** — register with invite code, login, session cookies (rate-limited)
- **Chat** — per-user memory scoping via `X-Hermes-Session-Key`, server UTC time injected into system prompt, skill activation via `[Используй навык: name]` marker
- **Profile** — name, password, SOUL.md, email settings, Telegram link
- **Files UI** (`/files`) — upload, download, create folders, write text, Ask-agent prefix
- **Skills** (`/skills`) — 10 manager skill templates (meeting followup, task extraction, decision memo, risk review, etc.)
- **Automations** (`/automations`) — scheduler with reminders (one-time/daily/weekly), morning digest, custom prompts, `Run now`, delivery via web or Telegram
- **Web tools** (`/api/web/*`) — search (SearxNG), fetch + trafilatura parse, extract links, bulk download with approval
- **Email integration** — IMAP/SMTP with encrypted credentials
- **Telegram relay** — file handling, voice message STT, slash-command whitelist, typing indicator
- **Approval flow** — single confirmation for external actions (email, scheduled jobs, web downloads, calendar)
- **Manager templates** — action intent instructions in system prompt
- **Gateway cron** — read-only display + delete in UI (with explanation that Telegram delivery requires webapp automations)

## Telegram Relay

Slash-commands handled locally (anything unknown is NOT sent to LLM, avoiding
the "No main session found" hallucination):

| Command | Effect |
|---|---|
| `/start CODE` | Link existing account or register with invite-code (alias: `/login`) |
| `/login CODE` | Alias for `/start` |
| `/whoami` | Show current UID |
| `/files` | List saved files |
| `/unlink` | Unlink Telegram from account |
| `/help` | Show this list |

Plus, while a chat request is in flight, the bot shows a "печатает…" indicator.
If the gateway ever hallucinates a "no main session" error, the relay
intercepts it and shows a human-friendly recovery hint instead.

## Security

### P0 Hardening status

| Пункт | Статус |
|---|---|
| Secrets not in LLM prompt | ✅ Implemented |
| Encryption at rest (Fernet) | ✅ Implemented |
| Hard quota with reserve tokens | ✅ Implemented |
| Rate limiting (login) | ✅ Implemented |
| CSRF protection | ✅ Implemented |
| Secure cookies (httponly, samesite, secure via env) | ✅ Implemented |
| File upload safety (UUID names, extension rejection) | ✅ Implemented |
| Constant-time comparison (hmac.compare_digest) | ✅ Implemented |
| Deterministic action pre-router (P1-4) | ❌ Not implemented — LLM-dependent |
| README status honesty (P1-7) | ✅ This table |

### Security Model

- Each user has isolated memory, files, and credentials
- External actions (email, calendar, scheduled jobs, web downloads) require single confirmation via `action_intent`
- Internal endpoints use `X-Internal-Secret` header with constant-time comparison
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
pytest -q           # 298 tests
ruff check app tests
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
- `USER_SECRET_ENCRYPTION_KEY` — dedicated key for email credential encryption (recommended; fallback: WEBAPP_INTERNAL_SECRET → JWT_SECRET)
- `WELCOME_QUOTA` — initial token quota (default: 2M)
- `ALERT_THRESHOLD_PCT` — quota alert threshold (default: 80%)
- `MIN_QUOTA_RESERVE_TOKENS` — reserve tokens for hard quota (default: 2048)
- `MAX_TOKENS_PER_RESPONSE` — max tokens per response estimate (default: 1024)
- `COOKIE_SECURE` — set Secure flag on session cookie (default: true)
- `COOKIE_SAMESITE` — set SameSite flag (default: lax)

## License

Deployment code — MIT. Hermes Agent — MIT (Nous Research).

## Links

- Hermes Agent: https://github.com/NousResearch/hermes-agent
- Documentation: https://hermes-agent.nousresearch.com/docs/
- Discord: https://discord.gg/NousResearch
