# 05. Coding Agent Execution Plan

## Цель

Дать кодовому агенту порядок реализации исправлений так, чтобы быстро получить работающий MVP, а не идеальную архитектуру, которая никогда не будет запущена.

Главный приоритет: сначала закрыть P0 и сделать один красивый demo flow для управленца.

---

## Рабочее правило

Не расширять функциональность, пока не закрыты P0:

1. секреты не попадают в prompt;
2. email action не идет через shell/script approval;
3. отправка требует максимум одно подтверждение;
4. quota hard cap работает;
5. опасные файлы отклоняются;
6. cookie/rate limit/CSRF включены хотя бы минимально.

---

## Phase 1. P0 security and action flow

### 1. Убрать секреты из prompt

Файлы:

- `webapp/app/chat.py`
- `webapp/app/main.py`
- `webapp/app/db.py`
- `webapp/app/templates/profile.html`

Задачи:

- удалить вставку email password в `build_system_prompt`;
- заменить ее на capability text;
- не возвращать сохраненный password в HTML;
- добавить encrypted storage или временно отключить email password до реализации encrypted storage.

Приемка:

- тест `test_prompt_does_not_include_email_password` проходит;
- ручной grep не находит password в prompt generation.

---

### 2. Добавить action approval model

Файлы:

- `webapp/app/db.py`
- `webapp/app/approval.py` новый файл
- `webapp/app/main.py`
- `webapp/app/relay.py`

Задачи:

- добавить таблицу `action_intents`;
- реализовать parser подтверждений и отказов;
- создать функции create/approve/reject/execute;
- pending intent должен иметь TTL;
- repeated confirmation не должен повторять действие.

Приемка:

- сценарий email отправки проходит через одно подтверждение;
- повторное подтверждение не создает дубль;
- изменение payload требует нового approval.

---

### 3. Email backend tool вместо shell/script

Файлы:

- `webapp/app/tools/email_tools.py` новый файл
- `webapp/app/approval.py`
- `webapp/app/chat.py`
- `webapp/app/relay.py`

Задачи:

- реализовать минимальный email tool на backend;
- LLM не пишет и не запускает скрипт для отправки;
- action intent payload содержит to/subject/body;
- после approval backend вызывает email tool.

MVP shortcut:

Если реальная SMTP-отправка не готова, сделать `create_draft` или mock backend с явным статусом `dry_run=false/true`. Но UX approval должен быть уже правильным.

Приемка:

- нет второго approval на выполнение скрипта;
- audit record создан;
- tool вызван один раз.

---

### 4. Hard quota

Файлы:

- `webapp/app/quota.py`
- `webapp/app/main.py`
- `webapp/app/relay.py`

Задачи:

- добавить preflight quota check;
- web и Telegram блокируют запрос до Hermes;
- отрицательная квота невозможна при hard quota enabled.

Приемка:

- при нулевой квоте Hermes API не вызывается;
- пользователь получает понятное сообщение.

---

### 5. Auth/session minimum

Файлы:

- `webapp/app/main.py`
- templates/static JS при необходимости

Задачи:

- helper установки session cookie;
- secure flags;
- password min length 10;
- rate limit login/register/chat;
- CSRF для browser POST.

Приемка:

- login brute force получает 429;
- POST без CSRF получает 403;
- cookie flags корректны.

---

### 6. File upload hardening

Файл:

- `webapp/app/relay.py`

Задачи:

- whitelist safe extensions;
- reject unsafe extensions;
- UUID physical filename;
- storage limits;
- path traversal protection.

Приемка:

- `.sh`, `.py`, `.js`, `.html`, archives отклоняются;
- `.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`, `.xlsx` принимаются.

---

## Phase 2. Manager skills quick wins

Файлы:

- `webapp/app/chat.py`
- `webapp/app/skills/manager_templates.py` новый файл
- `webapp/app/skills/__init__.py` новый файл

Задачи:

- добавить короткий manager routing block в system prompt;
- добавить templates:
  - email draft;
  - meeting follow-up;
  - task extraction;
  - executive summary;
  - decision memo;
  - daily digest stub.

Не делать сложный skills engine в первой итерации. Хватит template helpers и prompt routing.

Приемка:

- 6 demo commands из `03_manager_skills_pack.md` дают полезный структурированный результат;
- email send создает action intent, а не shell script.

---

## Phase 3. Docs and CI

Файлы:

- `README.md`
- `AGENTS.md`
- `.github/workflows/ci.yml`
- `webapp/requirements-dev.txt`
- `webapp/tests/*`

Задачи:

- обновить README и AGENTS под фактическую архитектуру;
- добавить pytest;
- добавить ruff/bandit/pip-audit;
- добавить smoke instructions.

Приемка:

- CI запускается;
- P0 tests проходят;
- документация не утверждает, что в репозитории нет webapp-кода.

---

## Запрещенные направления в рамках этого sprint

Не тратить время на:

- Kubernetes;
- сложную админку;
- новый frontend framework;
- переписывание всего на другую БД;
- полноценный marketplace skills;
- LangChain/LlamaIndex “потому что модно”;
- многоагентную архитектуру ради многоагентности.

Если очень хочется усложнить — записать в tech debt и вернуться к P0. Это неприятно, зато работает.

---

## Минимальный demo script после реализации

### Demo 1. Email

```text
Отправь письмо мне с просьбой подключиться к тестированию агента завтра до 12:00.
```

Ожидаемо:

1. агент показывает email card;
2. пользователь пишет “подтверждаю отправку”;
3. письмо отправлено;
4. нет второго подтверждения.

### Demo 2. Follow-up

```text
Сделай follow-up после встречи: клиент согласился на пилот, мы отправляем КП до пятницы, они дают 5 тестовых пользователей.
```

Ожидаемо:

- итоги;
- договоренности;
- задачи;
- сроки;
- письмо.

### Demo 3. Tasks

```text
Из этого текста выдели задачи, ответственных, сроки и риски: ...
```

Ожидаемо:

- таблица задач;
- список рисков;
- вопросы для уточнения.

### Demo 4. Document risk review

```text
Я загрузил документ. Дай executive summary и риски для руководителя.
```

Ожидаемо:

- summary;
- риски;
- деньги;
- сроки;
- вопросы.

### Demo 5. Decision memo

```text
Помоги решить: сначала делать Telegram-first MVP или webapp-first MVP.
```

Ожидаемо:

- варианты;
- критерии;
- риски;
- рекомендация.

---

## Финальная проверка перед merge

```bash
cd webapp
pip install -r requirements.txt -r requirements-dev.txt
ruff check app tests
pytest -q
bandit -r app -ll

cd ..
docker compose --env-file .env.hermes build webapp
docker compose --env-file .env.hermes up -d
curl -fsS http://localhost:9000/health
```

## Definition of Done

- P0 закрыты.
- Email flow проходит через одно подтверждение.
- Manager demo flow работает.
- Tests проходят.
- README/AGENTS обновлены.
- Никакие пользовательские секреты не попадают в prompt/history/logs/HTML.

Все остальное — потом. Быстрый работающий MVP лучше, чем архитектурный собор, который показывает только 500 Internal Server Error.
