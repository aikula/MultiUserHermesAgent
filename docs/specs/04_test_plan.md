# 04. MVP Hardening and Manager Skills Test Plan

## Цель

Дать кодовому агенту минимальный, но достаточный тест-план для проверки исправлений:

- безопасность секретов;
- approval flow без повторных подтверждений;
- hard quota;
- auth/session;
- file upload restrictions;
- управленческие skills;
- smoke запуск через Docker Compose.

Тесты должны быть быстрыми. Если тестовый набор требует отдельного шамана DevOps, значит мы уже проиграли самому себе.

---

## Test stack

Рекомендуемый минимум:

- `pytest`
- `pytest-asyncio`
- `httpx.AsyncClient` для FastAPI tests
- `tempfile`/`tmp_path` для изоляции файлов
- SQLite temporary DB
- monkeypatch env

Добавить в `webapp/requirements-dev.txt`:

```txt
pytest
pytest-asyncio
ruff
bandit
pip-audit
```

---

## CI minimum

Добавить GitHub Actions workflow `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  webapp-tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: webapp
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: ruff check app tests
      - run: pytest -q
      - run: bandit -r app -ll
      - run: pip-audit || true
```

`pip-audit || true` допустимо на первом этапе, чтобы не блокировать MVP из-за upstream advisory, но отчет должен быть виден.

---

## 1. Secret safety tests

### `test_prompt_does_not_include_email_password`

**Given:** пользователь с подключенной почтой и сохраненным секретом.  
**When:** вызывается `build_system_prompt(uid)`.  
**Then:** prompt не содержит пароль, encrypted secret, env secrets или raw credentials.

Проверки:

- пароль отсутствует;
- строка encrypted secret отсутствует;
- internal secret отсутствует;
- JWT secret отсутствует;
- API key отсутствует.

### `test_profile_does_not_render_password`

**Given:** пользователь с подключенной почтой.  
**When:** открывается `/profile`.  
**Then:** HTML не содержит сохраненный пароль.

### `test_email_tool_decrypts_only_inside_backend`

**Given:** encrypted password в DB.  
**When:** вызывается backend email tool.  
**Then:** tool может получить decrypted value внутри функции, но не возвращает его наружу и не пишет в логи.

---

## 2. Approval flow tests

### `test_confirmation_parser_positive_ru`

Проверить фразы:

- да;
- подтверждаю;
- подтверждаю отправку;
- отправляй;
- можно отправлять;
- согласен.

### `test_confirmation_parser_negative_ru`

Проверить фразы:

- нет;
- не отправляй;
- отмена;
- стоп;
- подожди;
- измени текст.

### `test_email_flow_one_confirmation_web`

**Given:** пользователь просит отправить email.  
**When:** assistant/backend создает pending action intent.  
**And:** пользователь пишет “подтверждаю отправку”.  
**Then:** email tool вызывается один раз, intent становится `executed`, пользователь получает success.

### `test_repeated_confirmation_does_not_duplicate_email`

**Given:** intent уже `executed`.  
**When:** пользователь повторно пишет “подтверждаю”.  
**Then:** email второй раз не отправляется.

### `test_changed_payload_requires_new_approval`

**Given:** пользователь подтвердил письмо на recipient A.  
**When:** recipient меняется на B.  
**Then:** старый approval не применяется, требуется новый pending intent.

### `test_expired_intent_cannot_execute`

**Given:** pending intent истек.  
**When:** пользователь подтверждает.  
**Then:** action не выполняется, возвращается сообщение о необходимости повторить команду.

---

## 3. Quota tests

### `test_web_chat_rejects_when_quota_exhausted`

**Given:** `quota_remaining = 0`.  
**When:** POST `/api/chat`.  
**Then:** Hermes API не вызывается, response сообщает о лимите.

### `test_telegram_chat_rejects_when_quota_exhausted`

**Given:** Telegram user linked, quota exhausted.  
**When:** приходит text message.  
**Then:** relay отправляет сообщение о лимите, Hermes API не вызывается.

### `test_quota_never_negative_with_hard_quota`

**Given:** hard quota enabled.  
**When:** запрос дороже остатка.  
**Then:** вызов отклонен, quota не уходит ниже нуля.

---

## 4. Auth/session tests

### `test_register_requires_password_min_length`

Пароль короче 10 символов отклоняется.

### `test_login_rate_limit`

После нескольких неудачных попыток login endpoint возвращает 429.

### `test_session_cookie_flags`

После login cookie содержит:

- HttpOnly;
- Secure, если включен env-флаг;
- SameSite согласно env.

### `test_profile_update_requires_csrf`

POST `/api/profile/update` без CSRF token получает 403.

### `test_internal_endpoint_does_not_require_csrf_but_requires_internal_secret`

Internal endpoint:

- без internal secret получает 403;
- с правильным internal secret работает;
- CSRF ему не нужен.

---

## 5. File upload tests

### `test_rejects_script_upload`

Файлы с расширениями script/html/archive отклоняются.

### `test_accepts_safe_document_types`

Принимаются:

- `.txt`;
- `.md`;
- `.csv`;
- `.json`;
- `.pdf`;
- `.docx`;
- `.xlsx`.

### `test_filename_path_traversal_blocked`

Имя `../../evil.txt` не может выйти за пределы user files dir.

### `test_storage_quota_enforced`

Если storage quota превышена, файл не сохраняется.

### `test_physical_filename_is_safe`

Физическое имя не должно напрямую равняться опасному пользовательскому имени. Использовать UUID/slug.

---

## 6. Manager skills regression tests

Эти тесты можно начать как snapshot/contract tests без вызова реальной LLM: проверять templates, routing и action intent creation.

### `test_meeting_followup_template_sections`

Follow-up должен содержать секции:

- итоги;
- договоренности;
- задачи;
- сроки;
- next steps.

### `test_task_extraction_template_columns`

Task extraction должен выдавать колонки:

- задача;
- ответственный;
- срок;
- статус;
- риск.

### `test_decision_memo_template_sections`

Decision memo должен содержать:

- контекст;
- варианты;
- критерии;
- риски;
- рекомендацию;
- next steps.

### `test_email_request_creates_action_intent_not_shell_script`

Запрос на отправку письма должен создавать `ActionIntent`, а не shell/python script approval.

---

## 7. E2E smoke tests

### Local smoke

```bash
cp .env.example .env.test
# заполнить тестовые значения без реальных пользовательских секретов

docker compose --env-file .env.test build webapp
docker compose --env-file .env.test up -d
docker compose --env-file .env.test ps
curl -fsS http://localhost:9000/health
```

### Web smoke

1. Открыть `/chat/register`.
2. Зарегистрировать пользователя через invite-code.
3. Открыть `/chat/profile`.
4. Проверить, что пароль email не отображается.
5. Отправить обычное сообщение.
6. Получить ответ.
7. Проверить usage.

### Telegram smoke

1. Создать link-code в профиле.
2. Отправить `/start CODE` боту.
3. Получить успешную привязку.
4. Отправить текст.
5. Получить ответ.
6. Отправить запрещенный файл и получить отказ.
7. Отправить разрешенный `.txt` и получить сохранение.

### Email approval smoke

1. Попросить отправить письмо самому себе.
2. Получить карточку подтверждения.
3. Написать “подтверждаю отправку”.
4. Получить “письмо отправлено”.
5. Проверить, что повторное “подтверждаю” не отправляет дубль.

---

## 8. Manual QA checklist для демо

Перед показом управленцу:

- [ ] Webapp открывается по `/chat/`.
- [ ] Регистрация работает.
- [ ] Telegram привязка работает.
- [ ] Сообщение в web получает ответ.
- [ ] Сообщение в Telegram получает ответ.
- [ ] Письмо самому себе отправляется после одного подтверждения.
- [ ] Follow-up после встречи выглядит полезно.
- [ ] Extract tasks из текста выглядит таблично и понятно.
- [ ] Document summary не падает на простом `.txt` или `.md`.
- [ ] Запрещенный файл отклоняется.
- [ ] При нулевой квоте запрос блокируется.
- [ ] В system prompt нет пользовательских секретов.
- [ ] README не врет про состав проекта.

---

## 9. Security regression checklist

- [ ] Нет plaintext пользовательских secrets в prompt.
- [ ] Нет plaintext пользовательских secrets в HTML.
- [ ] Нет plaintext пользовательских secrets в logs.
- [ ] External actions требуют approval или user policy allowlist.
- [ ] Shell/script execution не используется для email sending.
- [ ] Upload scripts/archives/html/js заблокирован.
- [ ] Rate limit включен.
- [ ] CSRF включен для browser POST.
- [ ] Cookie flags корректны.
- [ ] Hard quota включена.

---

## 10. Exit criteria

MVP считается готовым к демонстрации, если:

1. Все P0 tests проходят.
2. Email flow требует максимум одно подтверждение.
3. Запросы сверх quota не вызывают Hermes.
4. Секреты не попадают в prompt, history, HTML и logs.
5. Telegram upload не принимает опасные типы.
6. Есть 5-6 готовых manager demo commands.
7. README/AGENTS описывают фактическую архитектуру.

Если хотя бы один P0 не выполнен, демо можно проводить только на фейковых данных. Да, скучно. Зато потом не придется объяснять, почему “тестовый агент” разнес реальные пароли по контексту модели.
