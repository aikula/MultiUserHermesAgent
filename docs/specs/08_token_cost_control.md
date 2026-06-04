# 08. Token Cost Control Requirements

## Цель

Найти и ограничить места, которые могут создавать огромный расход токенов в demo instance.

Задача не в том, чтобы идеально оптимизировать LLM pipeline. Задача в том, чтобы демонстратор не начал жечь бюджет только потому, что пользователь загрузил длинный файл, память разрослась, а Telegram retry сделал второй дорогой запрос. Чудесный способ объяснить инвестору burn rate, но лучше не надо.

## Основные источники расхода

### TOKEN-001. Memory в каждом prompt

`build_system_prompt()` добавляет `memory.md` каждый раз. Если память разрастается, каждый запрос становится дорогим.

Требование:

- добавить env `MAX_MEMORY_CHARS`, default для demo: `6000`;
- если memory длиннее лимита, обрезать перед вставкой в prompt;
- не менять сам файл памяти, только prompt view.

Acceptance:

- [ ] Большой `memory.md` не добавляет в prompt больше лимита.
- [ ] Тест проверяет truncation.

---

### TOKEN-002. История сообщений

`MAX_HISTORY_MESSAGES` ограничивает число сообщений, но одно сообщение может быть очень длинным.

Требование:

- добавить env `MAX_HISTORY_MESSAGE_CHARS`, default: `3000`;
- добавить env `MAX_TOTAL_HISTORY_CHARS`, default: `12000`;
- в Hermes prompt отправлять truncated history;
- в DB сохранять полную историю.

Acceptance:

- [ ] Одно длинное сообщение не раздувает prompt.
- [ ] Суммарная история в prompt ограничена.
- [ ] DB still stores full content.

---

### TOKEN-003. Служебные action blocks в истории

Служебные action blocks нельзя сохранять в history. Это и UX-баг, и лишние токены.

Acceptance:

- [ ] В history нет служебных action blocks.
- [ ] Approval flow продолжает работать.

---

### TOKEN-004. Список файлов пользователя

Если пользователь загрузит много файлов, prompt может разрастись из-за списка файлов.

Требование:

- добавить env `MAX_FILES_IN_PROMPT`, default: `30`;
- показывать не больше этого числа файлов;
- если файлов больше, добавить короткую строку “и еще N файлов”.

Acceptance:

- [ ] При большом числе файлов prompt не содержит полный список.
- [ ] Пользователь видит, что файлов больше лимита.

---

### TOKEN-005. Summarizer расходует LLM calls

`maybe_summarize()` может запускаться часто у активного пользователя.

Требование для demo:

- поднять `SUMMARY_THRESHOLD` до `40` или `60` в env example;
- добавить cooldown env `SUMMARY_MIN_INTERVAL_SECONDS`, default: `1800`;
- не запускать summarizer, если quota below reserve;
- usage summarizer хотя бы логировать.

Acceptance:

- [ ] Summarizer не запускается чаще cooldown.
- [ ] Summarizer не запускается при почти исчерпанной квоте.
- [ ] Есть test на cooldown.

---

### TOKEN-006. Telegram timeout retry doubles LLM call

В Telegram relay при timeout не делать второй автоматический запрос к Hermes с теми же messages.

Требование:

- убрать automatic retry after timeout;
- вернуть пользователю понятное сообщение о timeout;
- один user message должен создавать максимум один Hermes call.

Acceptance:

- [ ] Timeout не делает второй LLM call.
- [ ] Есть test или mock check.

---

### TOKEN-007. Max response tokens из env

Сейчас max response tokens может быть задан прямо в payload.

Требование:

- использовать `MAX_TOKENS_PER_RESPONSE` из env для web and Telegram;
- одно место конфигурации для response max tokens.

Acceptance:

- [ ] Env влияет на web chat response max tokens.
- [ ] Env влияет на Telegram relay response max tokens.

## Tests to add

- `test_memory_truncation_limits_prompt_size`
- `test_history_message_truncation`
- `test_total_history_truncation`
- `test_file_list_capped_in_prompt`
- `test_summarizer_cooldown`
- `test_summarizer_skips_when_quota_low`
- `test_telegram_timeout_does_not_retry_hermes_call`
- `test_max_response_tokens_from_env`

## Demo acceptance checklist

- [ ] Prompt size controlled by env limits.
- [ ] History prompt view controlled by env limits.
- [ ] File list controlled by env limit.
- [ ] Summarizer has cooldown and quota guard.
- [ ] Telegram timeout does not double spend.
- [ ] Hard quota still blocks before Hermes call.
