# 09. Training MVP Scope

## Цель

Подготовить демонстрационный MVP для учебных занятий по настройке персональных агентов.

MVP должен показывать не абстрактный чат, а набор агентных возможностей:

1. память и кросс-сессионный контекст;
2. работа с файлами через отдельную UI вкладку;
3. почта и календарь;
4. шедулинг и проактивные сообщения;
5. автоматические задания;
6. интеграции с сервисами;
7. skills;
8. поиск, browsing и парсинг сайтов.

Мультимодальность и прямое управление компьютером в этом этапе исключены.

## Что уже есть

- Web chat.
- Telegram chat.
- User memory через `SOUL.md` and `memory.md`.
- Summarizer памяти.
- Email backend tool.
- Approval flow для внешних действий.
- Telegram file upload.
- Manager prompt templates.
- Voice message STT flow, но это не основной фокус текущего этапа.

## Что добавить в этот sprint

1. Files UI tab.
2. Folder creation in Files UI.
3. File upload/download/delete/list in UI.
4. Scheduler for reminders and recurring jobs.
5. Proactive Telegram/Web notifications.
6. Automation jobs.
7. Calendar minimal tool.
8. Browsing and parsing tool, preferably SearxNG + fetch/readability first.
9. Optional Playwright MCP adapter for dynamic sites, but only after simple parser works.
10. Training scenario for users.

## Explicitly out of scope

- Vision/image understanding.
- Direct computer control.
- Full production security hardening.
- Large Docker permission refactor.
- Multi-tenant enterprise RBAC.

## Demo acceptance

- Student can register and configure profile.
- Student can add personal memory facts.
- Student can upload and download files via UI.
- Student can create folders via UI.
- Student can ask agent to summarize uploaded file.
- Student can connect or simulate email/calendar workflow.
- Student can schedule reminder.
- Agent can send proactive scheduled message.
- Agent can run a simple recurring automation.
- Agent can search web and parse a page.
- Agent can use at least 5 manager skills.
