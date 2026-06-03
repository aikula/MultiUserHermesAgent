# 01. Fast MVP Hardening Specification

## Цель

Быстро довести MultiUserHermesAgent до состояния, где его можно показывать внешним пользователям в пилоте без очевидных P0-рисков: утечки пользовательских секретов, бесконтрольной траты токенов, слабой авторизации и опасной загрузки файлов.

Это не production-hardening на полгода. Это короткий обязательный слой защиты перед демонстрациями с реальными пользователями и рабочими данными.

## Scope

Исправляем в первую очередь:

- хранение и использование пользовательских email credentials;
- hard quota до вызова Hermes;
- session/cookie security;
- CSRF и rate limiting;
- Telegram/file upload безопасность;
- базовую изоляцию пользовательских файлов;
- документацию фактической архитектуры.

Не делаем на этом этапе:

- enterprise IAM;
- Kubernetes;
- сложный RBAC;
- отдельный sandbox per user;
- биллинг;
- полноценную админку.

Человечество как-нибудь переживет один релиз без Kubernetes.

---

## P0-1. Убрать секреты из LLM prompt

### Проблема

Сейчас `chat.build_system_prompt()` может добавлять IMAP/SMTP параметры и пользовательский пароль прямо в system prompt. Так делать нельзя: LLM не должна видеть пользовательские секреты.

### Требуемое поведение

LLM никогда не получает:

- email password или app password;
- OAuth refresh/access tokens;
- Telegram bot token;
- internal shared secret;
- API keys;
- JWT secret;
- любые значения из env, являющиеся секретами.

LLM получает только описание возможности: у пользователя подключена почта, а действия выполняются через backend tools. Модель может запросить действие, но секрет остается в backend.

### Реализация

1. В `webapp/app/chat.py` удалить добавление email password и любых секретов в system prompt.
2. Добавить backend-модуль `webapp/app/tools/email_tools.py`.
3. Для MVP реализовать минимум:
   - проверка статуса подключения почты;
   - создание черновика письма;
   - отправка письма только через approval flow из `02_action_approval_policy.md`.
4. Если backend-tool не готов, временно отключить UI подключения почты и убрать использование email credentials из prompt полностью.
5. В system prompt передавать только capability, например: “почта подключена, используй backend email tool”. Без host/login/password, если это не нужно модели для принятия решения.

### Acceptance checklist

- [ ] В `build_system_prompt()` нет пользовательских паролей, токенов, API keys или env secrets.
- [ ] Поиск по `chat.py` не находит вставку email password в prompt.
- [ ] При подключенной почте system prompt содержит только capability.
- [ ] Отправка письма идет через backend tool, а не через сгенерированный LLM Python-скрипт.
- [ ] В логах нет email password.
- [ ] В `chat_history` нет email password.

### Suggested tests

- `test_prompt_does_not_include_email_password`
- `test_prompt_does_not_include_env_secrets`
- `test_email_send_uses_backend_tool`

---

## P0-2. Шифровать пользовательские секреты at rest

### Проблема

Пользовательские email credentials не должны храниться plaintext в SQLite и не должны возвращаться в HTML профиля.

### Требуемое поведение

- Секреты шифруются перед записью в SQLite.
- Ключ шифрования берется из отдельной env-переменной.
- Если ключ не задан, подключение секретных интеграций недоступно.
- UI не возвращает сохраненный пароль обратно в HTML.

### Реализация

1. Добавить модуль `webapp/app/secrets_store.py` с функциями encrypt/decrypt.
2. Добавить новое поле для зашифрованного email password.
3. Старое plaintext-поле перестать использовать.
4. Миграция должна переносить старое значение в encrypted-поле только при наличии ключа, затем очищать plaintext.
5. В `profile.html` поле пароля всегда пустое. UI показывает только статус: подключено или не подключено.
6. При сохранении нового пароля backend шифрует значение.
7. При отключении почты backend очищает encrypted secret.

### Acceptance checklist

- [ ] В HTML профиля нет сохраненного email password.
- [ ] В SQLite нет plaintext email password после миграции.
- [ ] При отсутствии ключа шифрования email tools возвращают controlled error.
- [ ] Старый plaintext очищается после успешной миграции.

### Suggested tests

- `test_secret_encrypt_decrypt_roundtrip`
- `test_profile_does_not_render_password`
- `test_email_tool_requires_encryption_key`

---

## P0-3. Ввести hard quota до вызова Hermes

### Проблема

Сейчас quota является наблюдательной: система считает использование и отправляет alert, но не блокирует вызовы до Hermes.

### Требуемое поведение

Перед каждым LLM-вызовом:

- проверяется остаток квоты;
- если остаток ниже резерва, Hermes не вызывается;
- web получает controlled JSON error;
- Telegram получает понятное сообщение;
- отрицательная квота не появляется.

### Реализация

1. В `quota.py` добавить функцию проверки доступной квоты до списания.
2. В `api_chat` до вызова `chat.call_hermes()` оценивать стоимость запроса и проверять лимит.
3. В Telegram relay делать такую же проверку перед обработкой сообщения.
4. Добавить env-настройки:
   - hard quota enabled;
   - минимальный резерв токенов;
   - максимум символов на запрос;
   - максимум токенов ответа.
5. Если лимит исчерпан, вернуть пользователю нормальное сообщение: “Лимит тестовой квоты исчерпан. Обратись к администратору для увеличения лимита.”

### Acceptance checklist

- [ ] При нулевой квоте Hermes API не вызывается.
- [ ] Для web возвращается controlled JSON error.
- [ ] Для Telegram отправляется понятное сообщение.
- [ ] Admin alert остается, но не заменяет hard cap.
- [ ] Negative quota больше не появляется.

### Suggested tests

- `test_web_chat_rejects_when_quota_exhausted`
- `test_telegram_chat_rejects_when_quota_exhausted`
- `test_quota_record_does_not_go_negative_when_hard_quota_enabled`

---

## P0-4. Session, CSRF, rate limit

### Требуемое поведение

- Session cookie использует secure flags в production.
- State-changing browser POST защищены CSRF token.
- Login/register/chat/profile имеют rate limit.
- Минимальная длина пароля: 10 символов.
- После смены пароля желательно ротировать session token.

### Реализация

1. Вынести установку session cookie в helper.
2. Добавить env-флаги для cookie secure и SameSite.
3. Добавить простой in-memory rate limiter для MVP.
4. Добавить CSRF token для browser forms и fetch-запросов.
5. Internal endpoints с shared-secret не требуют CSRF, но требуют constant-time сравнение секрета.

### Acceptance checklist

- [ ] Cookie secure включается env-флагом и включен по умолчанию для production.
- [ ] Login больше не принимает пароль длиной 6.
- [ ] Несколько неправильных логинов подряд получают 429.
- [ ] POST `/api/profile/update` без CSRF получает 403.
- [ ] `/api/internal/*` работает по internal secret без CSRF.

---

## P0-5. File upload hardening

### Проблема

Telegram relay разрешает слишком много типов файлов, включая scripts, archives и HTML/JS. Для MVP это лишний риск.

### Требуемое поведение

Разрешенные расширения MVP:

- `.txt`
- `.md`
- `.csv`
- `.json`
- `.pdf`
- `.docx`
- `.xlsx`

Запрещенные расширения отклоняются. Их нельзя молча переименовывать в `.txt`.

### Реализация

1. Изменить file validation в `relay.py`.
2. Если расширение не в whitelist, вернуть пользователю отказ.
3. Физическое имя файла делать через UUID, оригинальное имя хранить отдельно.
4. Добавить лимиты:
   - максимальный размер файла;
   - максимальное число файлов на пользователя;
   - максимальный общий storage на пользователя.
5. Проверять path traversal через resolve-проверку внутри user directory.

### Acceptance checklist

- [ ] Script/archive/html/js файлы отклоняются.
- [ ] Имя вида `../../x.txt` не может выйти из user dir.
- [ ] При превышении storage quota файл не сохраняется.
- [ ] Физическое имя файла безопасное и не зависит напрямую от пользовательского имени.

---

## P1. Документация архитектуры

### Требуемое изменение

README и AGENTS.md должны отражать, что проект содержит не только deployment, но и собственный `webapp`.

### Обновить разделы

- Что это.
- Состав сервисов.
- Multi-user data layout.
- Security model.
- Known limitations.
- Quick start.
- Testing.

### Acceptance checklist

- [ ] README упоминает `webapp`.
- [ ] AGENTS.md не говорит, что в репо нет исходного кода.
- [ ] Описаны env-переменные webapp.
- [ ] Описаны P0 ограничения текущей версии.

---

## Implementation order для кодового агента

1. `chat.py`: убрать секреты из prompt.
2. `profile.html/main.py/db.py`: убрать plaintext password из UI/DB path.
3. `quota.py/main.py/relay.py`: hard quota.
4. `main.py`: secure cookies, password min length.
5. Добавить rate limiter.
6. Добавить CSRF для browser POST.
7. `relay.py`: file upload whitelist + reject unsafe.
8. README/AGENTS update.
9. Tests.

## Final smoke command

```bash
docker compose --env-file .env.hermes build webapp
docker compose --env-file .env.hermes up -d
docker compose --env-file .env.hermes ps
curl -fsS http://localhost:9000/health
pytest -q
```
