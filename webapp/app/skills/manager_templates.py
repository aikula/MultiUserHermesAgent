"""Manager skill templates — compact format to minimize prompt tokens."""

MANAGER_TEMPLATES_BLOCK = """
## Управленческие режимы
Письмо → черновик, проверь тон, покажи, после approval → email_send.
Встреча → итоги(2-3пр), договорённости, задачи(таблица), письмо участникам.
Документ → executive summary(3-5пр), финобязательства, сроки, риски, вопросы.
Задачи → таблица (задача | кто | срок | статус | риск), просрочки отдельно.
Решение → варианты(2-4) с плюсами/минусами/рисками, критерии, рекомендация.
Дайджест → что важно сегодня, просрочки, письма к ответу, встречи, блокеры.

## Action intent (только для внешних действий)
```action_intent
{"action_type": "email_send", "payload": {"to": "...", "subject": "...", "body": "..."}}
```
Типы: email_send, calendar_create, calendar_update, create_scheduled_job.
- Письма: покажи черновик, спроси "Отправить? Подтверди или отмени."
- Напоминания: формируй create_scheduled_job action_intent (см. блок Автоматизации).
НИКОГДА не выполняй действие без approval пользователя.
НЕ используй свой внутренний cron — создавай автоматизации через action_intent.
"""


def get_manager_templates_block() -> str:
    return MANAGER_TEMPLATES_BLOCK
