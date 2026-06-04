# 10. Files UI Specification

## Цель

Добавить отдельную вкладку `Файлы` в web UI, чтобы студент мог работать с файлами без Telegram:

- просматривать папки и файлы;
- создавать папки;
- загружать файлы;
- скачивать файлы;
- удалять файлы и пустые папки;
- выбирать файл для обработки агентом;
- создавать новый markdown/text file из результата агента.

Это must-have для учебного MVP: пользователю надо руками увидеть, что у агента есть рабочее пространство, а не только чатик с красивыми словами.

## UI

Добавить navigation link:

- `Чат`
- `Файлы`
- `Профиль`

Route:

- `GET /files`

Page layout:

- breadcrumb текущей папки;
- кнопка `Создать папку`;
- upload zone;
- таблица файлов;
- actions per item.

Columns:

- name;
- type;
- size;
- updated_at;
- actions.

Actions:

- open folder;
- download file;
- delete;
- copy path/name;
- ask agent about this file.

## API

All endpoints require authenticated user and CSRF for state-changing actions.

### List

`GET /api/files?path=optional/subdir`

Response:

- current path;
- breadcrumbs;
- directories;
- files;
- total size;
- storage limit.

### Create folder

`POST /api/files/mkdir`

Body:

- `path`;
- `name`.

Rules:

- no empty name;
- no path traversal;
- no names starting with dot;
- max name length 80;
- allowed chars: letters, numbers, spaces, dash, underscore, dot;
- duplicate folder returns 409.

### Upload

`POST /api/files/upload`

Multipart:

- `path`;
- `file`.

Rules:

- same allowlist as Telegram for demo;
- reject dangerous and unknown extensions;
- max file size from env;
- user storage quota from env;
- physical name can preserve safe original name or use UUID plus metadata. Prefer safe original name for demo UX, but still sanitize.

### Download

`GET /api/files/download?path=...`

Rules:

- file must be inside user files dir;
- directories not downloadable in demo unless zip support is explicitly added later.

### Delete

`POST /api/files/delete`

Body:

- `path`.

Rules:

- delete file;
- delete empty folder;
- non-empty folder requires explicit `recursive=true`, but for demo keep recursive disabled unless needed.

### Create text file

`POST /api/files/write-text`

Body:

- `path`;
- `name`;
- `content`.

Use case: agent creates `meeting_followup.md`, `decision_memo.md`, `tasks.md`.

## Backend helper

Add `webapp/app/file_service.py`.

Functions:

- `user_files_root(uid)`;
- `resolve_user_path(uid, relative_path)`;
- `list_files(uid, path)`;
- `create_folder(uid, path, name)`;
- `save_upload(uid, path, upload_file)`;
- `delete_path(uid, path)`;
- `write_text_file(uid, path, name, content)`;
- `storage_usage(uid)`.

Every path operation must use safe resolve and verify path is inside user root. Yes, this is boring. So are seatbelts. They still work.

## Agent integration

When user clicks `Ask agent`, insert into chat input:

`Проанализируй файл: <relative_path>`

For demo, agent can use file list from prompt. Later add backend file tools.

Recommended manager actions:

- summarize file;
- extract tasks;
- create checklist;
- create executive brief;
- compare selected files.

## Acceptance checklist

- [ ] User sees `Файлы` tab.
- [ ] User can create folder.
- [ ] User can upload allowed file.
- [ ] User cannot upload dangerous extension.
- [ ] User can download file.
- [ ] User can delete file.
- [ ] User cannot access another user's files.
- [ ] Path traversal is blocked.
- [ ] Storage limit is enforced.
- [ ] `Ask agent` sends useful prompt to chat.
- [ ] Agent can create a markdown file through API or helper.

## Tests

- `test_files_page_requires_login`
- `test_list_files_empty`
- `test_create_folder_success`
- `test_create_folder_rejects_traversal`
- `test_upload_allowed_file_success`
- `test_upload_dangerous_file_rejected`
- `test_download_file_success`
- `test_delete_file_success`
- `test_cannot_access_other_user_file`
- `test_write_text_file_success`
- `test_storage_quota_enforced`

## Demo script

1. Open `Файлы`.
2. Create folder `Занятие 1`.
3. Upload `meeting_notes.txt`.
4. Ask agent: `Выдели задачи и сроки из файла meeting_notes.txt`.
5. Ask agent: `Сохрани результат как tasks.md`.
6. Download `tasks.md`.
