# 06. Demo Notes

Цель: быстро вывести проект в рабочий демонстрационный экземпляр без рискованных изменений инфраструктуры.

## Не менять сейчас

- Docker user fallback.
- Gateway image and permissions.
- Current Telegram mapping.
- Large security refactoring.

## Исправить до demo

1. Web confirmation response всегда возвращает `content`.
2. Служебный action block не попадает в chat history.
3. Email settings update не требует повторного ввода пароля, если почта уже подключена.
4. Account removal реализуется отдельной задачей.
5. Prompt and history size limits реализуются отдельной задачей.

## Acceptance checklist

- [ ] Web chat работает без JS errors.
- [ ] Telegram chat работает.
- [ ] Email action выполняется после одного подтверждения.
- [ ] В истории нет служебного action block.
- [ ] Account removal работает из профиля.
- [ ] Prompt and history ограничены по размеру.
- [ ] Telegram timeout не создает повторный LLM call.
- [ ] Existing tests pass.
