# 02. Action Approval Policy Specification

## Цель

Убрать множественные подтверждения одного и того же действия и дать агенту больше свободы, не превращая систему в опасный автопилот.

Проблемный пример:

1. Пользователь просит отправить письмо.
2. Агент просит подтверждение.
3. Пользователь подтверждает.
4. Агент снова просит подтверждение на выполнение скрипта.
5. Пользователь подтверждает.
6. Агент снова просит подтверждение.
7. В какой-то момент письмо все-таки отправляется.

Такой UX убивает доверие быстрее, чем плохая модель. Пользователь не должен проходить квест “докажи, что ты действительно хотел то, что только что сказал”.

---

## Принцип

Для каждого действия должен существовать один `ActionIntent` и один `ApprovalDecision`.

Если пользователь подтвердил конкретный intent, backend должен выполнить действие без повторных LLM-подтверждений, пока не изменились существенные параметры действия.

---

## Классы действий

### Safe actions

Не требуют подтверждения:

- поиск по собственной истории;
- краткое резюме документов;
- составление черновика;
- анализ текста;
- подготовка списка задач;
- чтение уже загруженного пользователем файла;
- просмотр собственной квоты/профиля.

### Review actions

Требуют подтверждения перед внешним эффектом:

- отправка email;
- отправка Telegram-сообщения другому человеку;
- создание календарного события с участниками;
- изменение календаря;
- создание/закрытие задачи во внешней системе;
- отправка файла наружу;
- удаление пользовательских данных.

### Blocked actions

Запрещены или требуют отдельного admin policy:

- отправка массовой рассылки;
- отправка секретов наружу;
- выполнение произвольного shell-кода;
- доступ к данным другого пользователя;
- удаление всех данных пользователя без явного отдельного flow;
- отключение audit/security механизмов.

---

## ActionIntent schema

Добавить таблицу `action_intents` или хранить pending intent в SQLite.

Минимальная схема:

```sql
CREATE TABLE IF NOT EXISTS action_intents (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    approved_at TEXT,
    executed_at TEXT,
    result_json TEXT,
    error TEXT
);
```

Статусы:

- `drafted`
- `pending_approval`
- `approved`
- `executing`
- `executed`
- `rejected`
- `expired`
- `failed`

---

## Approval token

Когда агент формирует действие, backend создает `ActionIntent` и возвращает пользователю карточку подтверждения.

Для Telegram:

```text
Подтверди отправку письма:
Кому: person@example.com
Тема: ...
Текст: ...

Ответь: подтверждаю отправку
```

Для Web:

- показать карточку;
- кнопки: `Отправить`, `Изменить`, `Отменить`.

После подтверждения backend:

1. находит последний pending intent этого пользователя;
2. проверяет payload hash;
3. проверяет TTL;
4. переводит status в `approved`;
5. сразу вызывает backend tool;
6. пишет результат в `action_intents`;
7. возвращает пользователю итог.

LLM не должна второй раз спрашивать разрешение на shell/script/tool, если backend уже получил approval на конкретное внешнее действие.

---

## Ключевое правило против повторных подтверждений

Если действие уже подтверждено пользователем и payload не изменился, любые внутренние шаги выполнения считаются частью уже подтвержденного действия.

Пример:

- Пользователь подтвердил отправку email.
- Backend сам использует SMTP/email tool.
- Нельзя снова спрашивать: “подтверждаешь выполнение скрипта?”
- Нельзя перекладывать approval на upstream Hermes CLI/TUI.

Если upstream Hermes требует отдельный manual approval для shell, это значит, что email action реализован неправильно. Отправка email должна идти через backend tool, а не через shell-команду, которую сгенерировала модель.

---

## Исправление сценария отправки email

### Требуемый happy path

Диалог:

```text
Пользователь: отправь письмо andrey@example.com с просьбой подключиться к тестированию агента
Агент: Подтверди отправку письма:
- Кому: andrey@example.com
- Тема: Приглашение к тестированию AI-агента
- Текст: ...

Пользователь: подтверждаю отправку
Агент: Готово, письмо отправлено.
```

Максимум одно подтверждение.

### Недопустимый UX

- повторный запрос “подтверди выполнение скрипта”;
- просьба нажать approve в другом интерфейсе;
- сообщение “код написан, жду одобрения”;
- требование подтвердить то же самое другими словами.

---

## Natural language confirmation parser

Добавить функцию:

```python
def is_confirmation(text: str) -> bool:
    ...
```

Должна распознавать:

- `да`
- `подтверждаю`
- `подтверждаю отправку`
- `отправляй`
- `можно отправлять`
- `согласен`
- `approve`
- `send it`

Должна НЕ распознавать:

- `не отправляй`
- `отмена`
- `подожди`
- `измени текст`
- `нет`
- `стоп`

Добавить `is_rejection(text)`.

Если пользователь меняет параметры, старый intent отменяется, создается новый.

---

## Policy для большей свободы агента

Добавить пользовательские настройки autonomy level:

### Level 0 — strict

Подтверждение на каждое внешнее действие.

### Level 1 — trusted drafts

Агент может сам создавать черновики, задачи и предложения. Отправка наружу только после подтверждения.

### Level 2 — trusted routine actions

Агент может выполнять заранее разрешенные действия без подтверждения, если они попадают в user policy.

Примеры allowlist:

- отправлять письма только самому пользователю;
- создавать календарные события без внешних участников;
- отправлять Telegram-напоминание самому пользователю;
- делать summary документов;
- обновлять собственную memory.

### Level 3 — admin automation

Только для закрытого теста. Действия выполняются по user policy и audit log. Не включать по умолчанию.

---

## User policy examples

Добавить файл или DB-поле `user_policy_json`.

Пример политики:

```json
{
  "email": {
    "allow_send_without_confirmation_to_self": true,
    "always_confirm_external_recipients": true,
    "max_recipients_without_admin": 3
  },
  "calendar": {
    "allow_create_private_events_without_confirmation": true,
    "always_confirm_events_with_attendees": true
  },
  "files": {
    "allow_read_uploaded_files": true,
    "always_confirm_external_share": true
  }
}
```

---

## Audit log

Каждое внешнее действие пишет audit record:

- uid;
- action_type;
- payload_hash;
- approved_by;
- approval_source: web/telegram;
- executed_at;
- result;
- external ids, если есть.

Минимально можно использовать ту же таблицу `action_intents`.

---

## Acceptance checklist

- [ ] Отправка email требует максимум одно подтверждение.
- [ ] После подтверждения backend выполняет действие без shell/TUI approval.
- [ ] Повторное сообщение “подтверждаю” не создает дубль, если intent уже executed.
- [ ] Если пользователь меняет тему/текст/получателя, старый approval сбрасывается.
- [ ] Pending intent истекает по TTL.
- [ ] Все executed actions видны в audit log.
- [ ] Telegram и Web используют одну backend approval model.

---

## Suggested tests

### Unit

- `test_is_confirmation_positive_ru`
- `test_is_confirmation_positive_en`
- `test_is_confirmation_negative_phrases`
- `test_payload_hash_changes_when_recipient_changes`
- `test_expired_intent_cannot_execute`

### Integration

- `test_email_flow_one_confirmation_web`
- `test_email_flow_one_confirmation_telegram`
- `test_repeated_confirmation_does_not_duplicate_email`
- `test_changed_payload_requires_new_approval`

### Regression для конкретного диалога

Тест должен смоделировать:

1. user asks to send email;
2. assistant creates pending intent;
3. user says “да”;
4. backend executes email tool;
5. assistant returns success;
6. no second approval requested.

---

## Implementation order

1. Добавить `action_intents` schema.
2. Добавить `approval.py`:
   - create_intent;
   - approve_intent;
   - reject_intent;
   - execute_intent;
   - is_confirmation;
   - is_rejection.
3. Перевести email sending на backend tool.
4. Подключить approval flow в web chat.
5. Подключить approval flow в Telegram relay.
6. Добавить audit output в profile/admin debug page.
7. Добавить тесты.

## Definition of Done

Сценарий “отправь письмо самому себе с просьбой подключиться к тестированию агента” проходит за два пользовательских сообщения: просьба и подтверждение. Все. Не пять, не семь, не “нажми approve в астральном интерфейсе”.
