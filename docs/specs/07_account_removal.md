# 07. Account Removal Requirements

## Цель

Добавить пользователю возможность полностью удалить свой аккаунт и связанные данные из профиля.

Для демонстратора это обязательная функция: система хранит историю, память, файлы, настройки интеграций и Telegram-привязку. Пользователь должен иметь понятный способ все удалить.

## UX

В `profile.html` добавить danger zone:

- предупреждение о необратимости действия;
- поле текущего пароля;
- поле контрольной фразы;
- кнопка удаления аккаунта;
- после успешного удаления redirect на login page.

Контрольная фраза: `УДАЛИТЬ`.

## API

Добавить endpoint:

`POST /api/profile/delete-account`

Требования:

- пользователь должен быть авторизован;
- CSRF обязателен;
- текущий пароль обязателен для web-account;
- контрольная фраза обязательна;
- после успешного удаления session cookie удаляется.

## Что удалить

Для uid удалить:

- user row;
- chat history;
- action intents;
- telegram links;
- quota rows;
- user directory under `HERMES_USERS_DIR`;
- quota directory under `QUOTAS_DIR`;
- uid mapping from current Telegram auth mapping file, если он используется;
- email settings and credentials вместе с user row.

Invite codes можно обработать мягко: очистить `used_by`, если нужно сохранить сам invite code, или удалить связанные одноразовые invite records. Для demo достаточно очистить `used_by`.

## Safety rules for file removal

- Не удалять путь напрямую из пользовательского ввода.
- Строить путь только как base dir plus uid.
- Проверить, что resolved target находится внутри base dir.
- Только после этого удалять directory tree.

## Acceptance checklist

- [ ] В профиле есть danger zone удаления аккаунта.
- [ ] Без CSRF endpoint возвращает 403.
- [ ] Без текущего пароля удаление невозможно.
- [ ] Без контрольной фразы удаление невозможно.
- [ ] После удаления пользователь не может войти.
- [ ] User row удалена.
- [ ] Chat history удалена.
- [ ] Action intents удалены.
- [ ] User files directory удалена.
- [ ] Quota files directory удалена.
- [ ] Telegram mapping очищен.
- [ ] Session cookie удалена.

## Tests to add

- `test_delete_account_requires_csrf`
- `test_delete_account_requires_password`
- `test_delete_account_requires_confirm_text`
- `test_delete_account_removes_db_rows`
- `test_delete_account_removes_user_files`
- `test_delete_account_removes_quota_files`
- `test_delete_account_removes_telegram_mapping`
- `test_deleted_user_cannot_login`

## Implementation note

SQLite cascade сейчас не гарантирован для всех связанных данных. Для demo использовать явное удаление в правильном порядке через helper function, например `delete_user_account(uid)`.
