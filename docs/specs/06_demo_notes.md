# 06. Demo Notes

Цель: быстро вывести проект в рабочий демонстрационный экземпляр без рискованных изменений инфраструктуры.

## Состояние на 2026-06-04

Этот документ — **исторический**. Текущее состояние MVP см. в:

- `docs/training/dry_run_verification.md` — результат E2E dry-run по
  `lesson_scenario_agent_setup.md` (после Sprint 0–4)
- `docs/specs/09_training_mvp_scope.md` — общий scope

## Не менять сейчас

- Docker user fallback.
- Gateway image and permissions.
- Current Telegram mapping.
- Large security refactoring.

## Исправить до demo

1. Web confirmation response всегда возвращает `content`. ✅ Сделано (см. `relay.py`).
2. Служебный action block не попадает в chat history. ✅ Сделано (`_strip_intent_block`).
3. Email settings update не требует повторного ввода пароля, если почта уже подключена. ✅ Сделано (`api_profile_email`).
4. Account removal реализуется отдельной задачей. (вне scope Sprint 0–4)
5. Prompt and history size limits реализуются отдельной задачей. (вне scope Sprint 0–4)

## Acceptance checklist

- [x] Web chat работает без JS errors.
- [x] Telegram chat работает.
- [x] Email action выполняется после одного подтверждения.
- [x] В истории нет служебного action block.
- [ ] Account removal работает из профиля. (вне scope MVP)
- [x] Prompt and history ограничены по размеру. (chat.py MAX_HISTORY, len(content) ≤ 8000)
- [x] Telegram timeout не создает повторный LLM call.
- [x] Existing tests pass. (290/290)

## Что добавлено в Sprint 0–4 (демо-готовность)

- `/files` — вкладка с CRUD, ask-agent префиллом
- `/skills` — библиотека 10 управленческих шаблонов
- `/automations` — scheduler с формами (one-time / daily / weekly)
- `/api/web/*` — search, fetch, parse, links, download
- Proactive notifications в `chat_history` (channel='scheduler') + `notifications` table
- `[Используй навык: X]` маркер для активации skill на ход
- UTC injection в system prompt
- Relay robustness: типизация, gateway-confused fallback, /login alias
- 290 unit-тестов покрывают все сценарии

## См. также

- `docs/training/lesson_scenario_agent_setup.md` — 90-мин сценарий для студентов
- `docs/training/dry_run_verification.md` — per-block PASS/SKIP с матрицей тестов
