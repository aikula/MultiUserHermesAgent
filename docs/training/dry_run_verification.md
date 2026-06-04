# Dry-run Verification: Lesson Scenario «Настройка персонального агента»

## Цель

Проверить, что **все блоки 90-минутного сценария** (`lesson_scenario_agent_setup.md`)
выполнимы текущим кодом без дополнительной разработки. Без media (скриншотов,
screencasts, demo recording) — только маркеры PASS/SKIP/GAP и ссылки на
commit/тест, который подтверждает работоспособность.

## Когда

Sprint 5 (после Files UI, Skills, Scheduler, Web tools).

## Acceptance criteria → Status

Чек-лист из `docs/specs/09_training_mvp_scope.md`, раздел Demo acceptance.

| # | Критерий | Статус | Где смотреть |
|---|----------|--------|--------------|
| 1 | Student can register and configure profile | PASS | `POST /register` + invite-code (`webapp/app/main.py`), `GET/POST /profile` |
| 2 | Student can add personal memory facts | PASS | `memory.md` под `HERMES_USERS_DIR/<uid>/`; chat «запомни» сохраняет в этот файл, `build_system_prompt` инжектит блок `## Твоя память о юзере` (`webapp/app/chat.py:52`) |
| 3 | Student can upload and download files via UI | PASS | Sprint 1: `webapp/app/file_service.py` + `/files` + `/api/files/{upload,download,delete,write-text,mkdir}` + `webapp/tests/test_file_service.py` (32 теста) + `test_files_api.py` (20) |
| 4 | Student can create folders via UI | PASS | `POST /api/files/mkdir` + UI modal в `files.html` |
| 5 | Student can ask agent to summarize uploaded file | PASS | `build_system_prompt` инжектит `## Файлы` список; агент читает через `open()` per `chat.py:68-70` |
| 6 | Student can connect or simulate email/calendar workflow | PASS | Email: `tools/email_tools.py` + `email_send` в `REVIEW_ACTIONS`. Calendar: `calendar_create/update` в `REVIEW_ACTIONS` (handler вне scope MVP, но approval-flow готов) |
| 7 | Student can schedule reminder | PASS | Sprint 3: `kind=reminder`, `POST /api/jobs`, UI в `/automations` |
| 8 | Agent can send proactive scheduled message | PASS | `handle_reminder` пишет в `chat_history (channel='scheduler')` + `notifications` + пытается Telegram через `relay.send_message` |
| 9 | Agent can run a simple recurring automation | PASS | `kind=morning_digest` или `custom_prompt`, `schedule_type=daily/weekly`, worker пересчитывает `next_run_at` через `compute_next_run_at` |
| 10 | Agent can search web and parse a page | PASS | Sprint 4: `/api/web/{search,fetch,parse,links,download}` + `web_tools.py` (26 тестов) |
| 11 | Agent can use at least 5 manager skills | PASS | 10 skills в `webapp/app/skills/library/*.md` (см. ниже) |

## Skill library (≥ 5)

| Skill | Назначение |
|-------|-----------|
| `meeting_followup` | Follow-up после встречи |
| `task_extraction` | Извлечение задач из текста |
| `decision_memo` | Memo для принятия решения |
| `risk_review` | Обзор рисков |
| `email_reply` | Краткий ответ на письмо |
| `daily_digest` | Ежедневный дайджест |
| `delegation_plan` | План делегирования |
| `stakeholder_map` | Карта стейкхолдеров |
| `weekly_status_report` | Еженедельный статус-репорт |
| `research_brief` | Research brief по списку источников |

Активация: `[Используй навык: meeting_followup]` в начале сообщения →
полный markdown инжектится в `messages` текущего хода. Compact list
подсказок виден в system prompt.

## Блоки занятия → готовность

| Block | Мин | Status | Заметки |
|-------|-----|--------|---------|
| 1. Введение | 10 | PASS | Архитектура — в `AGENTS.md` (раздел «Архитектура») |
| 2. Регистрация + профиль | 10 | PASS | `/register` + invite-code + `/profile` |
| 3. Память + кросс-сессионный контекст | 15 | PASS | SOUL.md + memory.md; reload page — память сохранена |
| 4. Файлы: загрузка, папки, обработка | 15 | PASS | Files UI с папками, upload, Ask-agent префилл в чат |
| 5. Skills для управленца | 15 | PASS | 10 skills, активация маркером |
| 6. Email + approval flow | 10 | PASS | `email_send` в REVIEW_ACTIONS; карточка подтверждения в чате |
| 7. Шедулинг + проактивность | 15 | PASS | `/automations`, reminder, daily digest, run-now |
| 8. Поиск + парсинг | 10 | PASS | SearxNG + trafilatura + safe-download (с approval) |
| 9. Самостоятельная практика | 10 | PASS | Все компоненты на месте |

## Smoke test (live boot)

| Endpoint | Anon response | Notes |
|----------|---------------|-------|
| `GET /health` | 200 `{"status":"ok"}` | OK |
| `GET /` | 302 → `/login` | OK |
| `GET /login` | 200 | OK |
| `GET /register` | 200 | OK |
| `GET /api/jobs` | 401 | OK (auth required) |
| `POST /api/web/search` | 401 | OK |
| `GET /automations` | 302 → `/login` | OK |
| `GET /skills` | 302 → `/login` | OK |
| `GET /files` | 302 → `/login` | OK |

При старте `uvicorn app.main:app`:

- `scheduler: scheduler loop started; tick=30s` — worker активен
- `relay: TELEGRAM_BOT_TOKEN not set, relay disabled` — корректный fallback
  (web-уведомления продолжают работать)

## Тестовое покрытие

```
290 passed, 2 warnings in 64.19s
```

Разбивка по модулям:

| Модуль | Тестов |
|--------|--------|
| `test_auth.py` | регистрация, login, session |
| `test_chat_prompt.py` | UTC injection |
| `test_chat_skills.py` | skill marker + system prompt |
| `test_skill_loader.py` | loader (13 тестов) |
| `test_skills_api.py` | skills API + страница |
| `test_file_service.py` | path-traversal, quota, allowlist (32) |
| `test_files_api.py` | files API (20) |
| `test_jobs_store.py` | CRUD + next_run_at (23) |
| `test_job_handlers.py` | reminder / digest / prompt (9) |
| `test_scheduler.py` | run_due + run_now (6) |
| `test_jobs_api.py` | /api/jobs + /automations (11) |
| `test_web_tools.py` | search/fetch/parse/links/download + SSRF (26) |
| `test_web_api.py` | /api/web/* + approval flow (11) |
| `test_relay_robustness.py` | relay stability |
| `test_quota.py` | quota tracking |
| `test_approval.py` | approval flow |
| `test_secrets.py` | secrets_store |
| `test_manager.py` | manager prompt templates |

## Известные gaps (не блокеры)

| Gap | Где | Workaround |
|-----|-----|------------|
| Calendar tool handler — нет реализации, есть только `calendar_*` в `REVIEW_ACTIONS` | Approval flow сработает, но execute вернёт `Unknown action type` | На занятии использовать email как пример «внешнего действия» |
| Telegram bot token — нужно настроить перед занятием | Без токена relay отключён, fallback на web-уведомления | Преподаватель настраивает `TELEGRAM_BOT_TOKEN` заранее или проводит блок 7 без Telegram |
| Playwright MCP (Phase 2 spec 12) | Не реализован | Не нужен для 90% сценариев; HTML-страницы парсятся через trafilatura |
| Image upload + vision | Explicitly out of scope (spec 09) | Студенты загружают текстовые файлы |
| Multi-modal input | Explicitly out of scope | — |

## Demo prep checklist (для преподавателя)

Перед занятием:

- [ ] `cp .env.example .env.hermes && chmod 600 .env.hermes`
- [ ] Заполнить `HERMES_API_KEY`, `SEARXNG_URL`, `INVITE_CODE_BOOTSTRAP`
- [ ] `docker compose --env-file .env.hermes up -d`
- [ ] `docker exec hermes-gateway hermes doctor` — healthy
- [ ] Создать 3 demo файла: `meeting_notes.txt`, `commercial_offer.md`, `tasks_messy.txt`
- [ ] Положить их в `HERMES_USERS_DIR/_demo/` чтобы студенты могли скачать
- [ ] Проверить `curl http://localhost:9000/automations` → 302 → /login
- [ ] Зарегистрировать преподавательский аккаунт, настроить SOUL
- [ ] Создать один тестовый reminder с `Run now` для проверки proactive flow
- [ ] Проверить SearxNG: `curl "$SEARXNG_URL/search?q=test&format=json" | head`

## Итог

Все блоки 90-минутного сценария выполнимы. Покрытие тестами — 290/290.
Smoke test проходит. Известные gaps не блокируют демо и явно перечислены
для преподавателя.
