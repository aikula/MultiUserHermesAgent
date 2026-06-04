# 11. Scheduler, Proactivity and Automations Specification

## Цель

Добавить демонстрационную проактивность и автоматические задания:

- одноразовые напоминания;
- повторяющиеся напоминания;
- утренний дайджест;
- автоматические задания по расписанию;
- отправку proactive сообщений в Telegram and/or web notification center.

Для учебного MVP важно показать, что агент не только отвечает, но и действует по расписанию. Иначе это не агент, а очень разговорчивый FAQ.

## Scope for demo

### Must have

- Create one-time reminder.
- Create recurring reminder: daily, weekly.
- List scheduled jobs.
- Enable/disable job.
- Delete job.
- Proactive Telegram message when job fires.
- Job execution log.
- Simple morning digest job.

### Nice to have

- Web notification center.
- Calendar-aware morning digest.
- Email-aware morning digest.
- Conditional automation later.

### Out of scope now

- Complex cron editor UI.
- Distributed scheduler.
- External queue system.
- Full natural language date parser for every possible phrase.

## DB schema

Add table `scheduled_jobs`:

- `id` text primary key;
- `uid` text;
- `title` text;
- `kind` text;
- `status` text;
- `schedule_type` text;
- `run_at` text nullable;
- `rrule` text nullable;
- `next_run_at` text;
- `channel` text;
- `payload_json` text;
- `created_at` text;
- `updated_at` text;
- `last_run_at` text nullable;
- `last_result` text nullable.

Add table `job_runs`:

- `id` text primary key;
- `job_id` text;
- `uid` text;
- `started_at` text;
- `finished_at` text;
- `status` text;
- `result` text;
- `error` text.

## Job kinds

### reminder

Payload:

- `message`;
- `context` optional.

Execution:

- send message to Telegram if linked;
- also save assistant message into chat history with channel `scheduler`.

### morning_digest

Payload:

- `include_memory` bool;
- `include_recent_history` bool;
- `include_tasks` bool;
- `include_email` bool later;
- `include_calendar` bool later.

Execution for demo:

- build digest from memory, recent history, action intents, scheduled jobs;
- optionally call Hermes to summarize;
- send to Telegram.

### custom_prompt

Payload:

- `prompt`;
- `send_result` bool.

Execution:

- run prompt through Hermes with user context;
- save and send result.

Use carefully. Add quota check before running.

## API

### List jobs

`GET /api/jobs`

### Create job

`POST /api/jobs`

Body fields:

- title;
- kind;
- schedule_type: one_time, daily, weekly;
- run_at or time_of_day;
- weekdays optional;
- channel: telegram, web, both;
- payload.

### Disable job

`POST /api/jobs/{id}/disable`

### Enable job

`POST /api/jobs/{id}/enable`

### Delete job

`POST /api/jobs/{id}/delete`

### Run now

`POST /api/jobs/{id}/run-now`

Useful for demo and tests. Requires CSRF and user ownership check.

## UI

Add tab `Автоматизации`.

Sections:

- Create reminder.
- Create morning digest.
- Scheduled jobs list.
- Last runs.

Simple form is enough:

- title;
- message;
- schedule type;
- time;
- channel.

## Background worker

Implement in webapp startup:

- periodic task every 30 or 60 seconds;
- select due enabled jobs;
- execute one by one;
- update next_run_at;
- write job_runs.

Avoid duplicate execution:

- single-process demo can use in-memory lock;
- if more than one process later, add DB lock.

## Natural language support

For demo, agent can create jobs through structured action intent.

Example user command:

`Каждое утро в 9:00 присылай мне дайджест по задачам и важным договоренностям.`

Agent response creates action intent:

- action_type: create_scheduled_job;
- payload: job definition.

Requires approval before creating recurring automation.

## Proactive channels

### Telegram

Use existing relay `send_message` if available.

If Telegram not linked:

- save notification in DB;
- show in web notification center later;
- return warning in UI.

### Web

For demo, simplest option:

- table `notifications`;
- bell or topbar indicator later;
- not mandatory for first pass if Telegram is the main proactive channel.

## Quota and cost guard

Before any job that calls Hermes:

- check quota;
- skip if quota low;
- write job run status `skipped_quota`;
- send short message if appropriate.

Morning digest should use compact context.

## Acceptance checklist

- [ ] User can create one-time reminder.
- [ ] User can create daily reminder.
- [ ] User can list jobs.
- [ ] User can disable and delete job.
- [ ] Job fires and sends proactive Telegram message.
- [ ] Job run is logged.
- [ ] Morning digest job works with current memory/history.
- [ ] Job does not run if quota is exhausted.
- [ ] Job is scoped to correct user.
- [ ] Restart does not lose jobs.

## Tests

- `test_create_one_time_reminder`
- `test_create_daily_reminder`
- `test_list_jobs_scoped_to_user`
- `test_disable_job`
- `test_delete_job`
- `test_run_due_job_sends_message`
- `test_job_run_logged`
- `test_job_skips_when_quota_low`
- `test_morning_digest_uses_compact_context`
- `test_run_now_endpoint_requires_csrf`

## Demo script

1. User says: `Напомни мне через 1 минуту проверить письмо клиенту`.
2. Agent creates scheduled job after approval.
3. Wait one minute.
4. Telegram receives proactive message.
5. User creates daily morning digest.
6. Click `Run now` to avoid waiting until tomorrow.
7. Agent sends compact digest.
