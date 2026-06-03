# 03. Manager Skills Pack Specification

## Цель

Сразу предусмотреть полезные навыки и приемы использования агента для управленцев, чтобы демонстрация была не про “чат-бот умеет болтать”, а про понятную управленческую пользу:

- экономит время;
- фиксирует договоренности;
- готовит письма и follow-up;
- помогает с календарем;
- анализирует документы;
- напоминает о рисках;
- превращает хаос переписки в задачи.

Управленцу не нужен еще один текстовый попугай. Ему нужен помощник, который снижает операционный шум.

---

## Продуктовая идея

Позиционирование MVP:

> Multi-user AI assistant for managers: email, Telegram, files, meeting follow-ups, task tracking, personal memory and controlled action execution.

На русском:

> Персональный AI-агент руководителя: помогает с письмами, встречами, файлами, задачами, напоминаниями и управленческими решениями, при этом помнит контекст и действует только в рамках понятных правил.

---

## Базовые user scenarios для демо

### Scenario 1. Быстрое письмо

**Команда:**

```text
Отправь Ивану письмо: попроси подключиться к тестированию агента завтра до 12:00.
```

**Поведение:**

1. Агент составляет письмо.
2. Показывает карточку подтверждения.
3. После одного подтверждения отправляет.
4. Сохраняет action в audit log.
5. Предлагает создать follow-up reminder, если ответа не будет.

**Acceptance:**

- максимум одно подтверждение;
- нет “подтверди выполнение скрипта”;
- письмо в деловом стиле;
- follow-up опционален, но предлагается.

---

### Scenario 2. Follow-up после встречи

**Команда:**

```text
Сформируй follow-up после встречи: мы договорились запустить пилот, до пятницы собрать список пользователей, я готовлю КП.
```

**Поведение:**

Агент формирует:

- краткое резюме;
- список договоренностей;
- задачи по участникам;
- письмо follow-up;
- напоминания.

**Output template:**

```md
## Итоги встречи
- ...

## Договоренности
- ...

## Задачи
| Ответственный | Задача | Срок |
|---|---|---|

## Черновик письма
...
```

---

### Scenario 3. Разбор документа для руководителя

**Команда:**

```text
Проанализируй этот договор как руководитель: риски, деньги, сроки, обязательства, что надо уточнить.
```

**Поведение:**

Агент выдает:

- executive summary;
- финансовые обязательства;
- сроки;
- риски;
- неясные места;
- вопросы юристу/контрагенту;
- красные флаги.

**Важно:**

Агент не должен притворяться юристом. Формулировка: “не юридическое заключение, а управленческий risk review”.

---

### Scenario 4. Контроль задач из переписки

**Команда:**

```text
Из этой переписки выдели задачи, сроки, ответственных и риски.
```

**Output:**

```md
## Задачи
| Задача | Ответственный | Срок | Статус | Риск |
|---|---|---|---|---|

## Риски
- ...

## Что уточнить
- ...
```

---

### Scenario 5. Подготовка к встрече

**Команда:**

```text
Подготовь меня к встрече с клиентом по пилоту AI-агента. Нужны тезисы, вопросы, риски и next steps.
```

**Output:**

- цель встречи;
- позиция;
- 5 ключевых тезисов;
- вопросы клиенту;
- возможные возражения;
- ответы на возражения;
- next steps.

---

### Scenario 6. Ежедневный управленческий дайджест

**Команда:**

```text
Сделай утренний дайджест: что важно, какие просрочки, какие письма требуют ответа, какие встречи сегодня.
```

**MVP behavior:**

Если интеграции еще не подключены, агент использует доступные источники:

- chat history;
- загруженные файлы;
- ручной ввод пользователя;
- Telegram history внутри системы.

Если email/calendar tools подключены, добавляет:

- письма без ответа;
- встречи дня;
- подготовку к встречам;
- просроченные follow-up.

---

## Skill taxonomy

### 1. Communication skills

- `draft_email`
- `send_email_with_approval`
- `rewrite_tone`
- `summarize_thread`
- `create_follow_up`
- `prepare_reply_options`

### 2. Meeting skills

- `meeting_brief`
- `meeting_follow_up`
- `extract_decisions`
- `extract_action_items`
- `create_calendar_event_with_approval`
- `prepare_agenda`

### 3. Document skills

- `executive_summary`
- `risk_review`
- `compare_documents`
- `extract_obligations`
- `extract_numbers`
- `create_checklist`

### 4. Task and control skills

- `task_extract`
- `task_prioritize`
- `deadline_risk_scan`
- `weekly_status_report`
- `decision_log_update`
- `delegation_plan`

### 5. Strategy and decision skills

- `pros_cons`
- `scenario_analysis`
- `stakeholder_map`
- `risk_matrix`
- `decision_memo`
- `one_page_brief`

### 6. Personal productivity skills

- `daily_digest`
- `weekly_review`
- `reminder_create`
- `focus_plan`
- `context_recall`

---

## Skill format

Каждый skill должен иметь единый формат.

```yaml
name: draft_email
category: communication
risk_level: safe
inputs:
  - recipient
  - goal
  - tone
  - constraints
outputs:
  - subject
  - body
  - assumptions
  - suggested_follow_up
requires_approval: false
```

Для внешних действий:

```yaml
name: send_email
category: communication
risk_level: review
requires_approval: true
approval_payload:
  - to
  - subject
  - body
  - attachments
```

---

## Prompt snippets для системного промпта

Добавить в user/system prompt не простыню, а короткий routing block.

```md
## Управленческие режимы
Если пользователь просит помочь с управленческой задачей, выбери один из режимов:
- письмо / коммуникация;
- встреча / follow-up;
- документ / анализ рисков;
- задачи / контроль исполнения;
- решение / сценарный анализ;
- дайджест / личная продуктивность.

Для внешних действий сначала подготовь draft/action card. Выполняй действие только после approval через backend policy.
```

---

## Demo commands

Эти команды должны работать красиво в демо.

### Email

```text
Напиши письмо команде: завтра в 11:00 созвон по тестированию агента, попроси подготовить вопросы.
```

```text
Отправь письмо мне с кратким планом тестирования агента на неделю.
```

### Meeting

```text
Подготовь agenda для встречи с клиентом по внедрению AI-агента в отдел продаж.
```

```text
Сделай follow-up: клиент согласился на пилот, мы до пятницы отправляем КП, они дают 5 тестовых пользователей.
```

### Documents

```text
Я загрузил договор. Найди риски для руководителя: деньги, сроки, ответственность, мутные места.
```

```text
Сравни два коммерческих предложения и дай рекомендацию, какое выбрать и почему.
```

### Tasks

```text
Из этого текста выдели задачи, ответственных, сроки и риски.
```

```text
Сделай план запуска пилота на 2 недели с контрольными точками.
```

### Decision

```text
Помоги принять решение: делать Telegram-first MVP или сначала webapp. Дай варианты, риски, критерии выбора.
```

### Digest

```text
Сделай краткий управленческий дайджест по моим последним задачам и диалогам.
```

---

## MVP implementation approach

Чтобы быстро работало, не надо сразу делать полноценный skills engine. Достаточно трех уровней.

### Level 1. Prompt-routed skills

- Добавить routing block в `build_system_prompt`.
- Добавить templates в `webapp/app/skills/manager_templates.py`.
- На запрос пользователя модель сама выбирает формат ответа.

### Level 2. Backend action tools

Для действий с внешним эффектом:

- email send;
- calendar create;
- reminder create;
- task create.

Все через approval policy.

### Level 3. Persistent artifacts

Сохранять результаты:

- action items;
- decisions;
- reminders;
- follow-ups;
- user preferences.

Можно начать с SQLite tables:

- `tasks`
- `decisions`
- `reminders`
- `action_intents`

---

## Минимальные DB additions

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    owner TEXT,
    due_date TEXT,
    status TEXT DEFAULT 'open',
    source TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    title TEXT NOT NULL,
    context TEXT,
    decision TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    title TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    channel TEXT DEFAULT 'telegram',
    status TEXT DEFAULT 'scheduled',
    created_at TEXT NOT NULL
);
```

На первом этапе можно не делать UI, достаточно backend и текстовых команд.

---

## Quick wins для демо

Реализовать в первую очередь:

1. `draft_email` + `send_email_with_approval`.
2. `meeting_follow_up`.
3. `extract_tasks`.
4. `document_executive_summary`.
5. `decision_memo`.
6. `daily_digest_stub` на основе chat history.

Этого достаточно, чтобы управленец увидел пользу за 10 минут.

---

## Acceptance checklist

- [ ] Агент умеет отличать draft от external action.
- [ ] Для email send используется approval policy.
- [ ] Есть минимум 6 demo commands из этого файла, которые дают хороший результат.
- [ ] Есть короткий manager routing block в prompt.
- [ ] Есть templates для follow-up, executive summary, task extraction, decision memo.
- [ ] Результаты action items можно сохранить хотя бы в SQLite или history.
- [ ] Демо не требует ручного объяснения “ну вообще потом оно будет полезным”. Оно уже полезно.

---

## Suggested tests

- `test_manager_followup_contains_decisions_tasks_next_steps`
- `test_extract_tasks_outputs_owner_due_risk_columns`
- `test_email_request_creates_action_intent_not_shell_script`
- `test_send_email_requires_approval_for_external_recipient`
- `test_self_email_can_follow_user_policy`
- `test_decision_memo_has_options_risks_recommendation`

---

## Definition of Done

За одну демонстрацию агент должен уверенно показать:

1. подготовку письма;
2. отправку после одного подтверждения;
3. follow-up после встречи;
4. извлечение задач из текста;
5. краткий анализ документа;
6. управленческую рекомендацию по решению.

Если после этого управленец говорит “ну и что?”, значит проблема уже не в агенте, а в демонстрации. Или в управленце, но это социально менее удобная гипотеза.
