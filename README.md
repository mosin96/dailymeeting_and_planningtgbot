# Daily Meeting & Planning helper bot for Telegram

This bot allows to play Planning Poker game in group chat and manages daily standup rotations with reminders.

# Usage
You can launch your own instance, see `Self-hosted usage` below.

## Planning poker
```
/poker task url or description
```
Multiline is also supported:
```
/poker some long description
of task
across multiple lines
```

Only the initiator can open cards or restart the game at any moment.

Currently there is only one scale: 1, 2, 3, 5, 8, 13, 20, 40, ❔, ☕

## Daily standup
The bot reminds the team who leads today's daily standup. Configuration is per chat:

| Command | Description |
|---|---|
| `/daily` | Main menu (status + quick actions) |
| `/team` | Team members: add (by reply or @username), remove |
| `/time` | Set daily standup time (HH:MM) |
| `/who` | Who leads today |
| `/substitute` | Substitute today's leader |
| `/setleader @ник` | Manually set today's leader (or reply to a member's message) |
| `/vacation @ник ДД.ММ.ГГГГ` | Set a member's vacation end date (`снять` — restore) |
| `/help` | Full help text |

The "who leads today" reminder message contains two buttons available to any chat member:

- **«Подмените меня»** — one-time substitution: the next non-skipped member leads today, the requester leads tomorrow.
- **«Пропуск»** — one-time skip without substitution (the position in the rotation stays the same).

The `/daily` menu also has two quick-action buttons:

- **«Выбрать ведущего»** — opens a member picker to manually set today's leader.
- **«Отпуск»** — opens a member picker to set a member's vacation end date.

### Manual leader override
`/setleader @ник` (or reply to a member's message, or pick from the `/daily` menu) makes the chosen member lead **today**. The rotation continues from the member after them — the queue order is not changed.

### Vacation

`/vacation @ник 05.08.2026` marks a member as on vacation until that date (inclusive). While on vacation the member is automatically skipped in every rotation path (reminder tag, `/who`, `/daily` status, substitute, skip). `/vacation @ник снять` restores them. The team list shows a `(в отпуске до ДД.ММ.ГГГГ)` suffix, and the 15-minute reminder appends `В отпуске: Имя (до ДД.ММ.ГГГГ)` using plain names (no `@` mention, no link).

Workdays are synchronized with the public `isdayoff.ru` API (Russian corporate calendar, includes public holidays and rescheduled days). If the API is unavailable, the bot falls back to Mon–Fri.

All commands are registered in the Telegram command menu (bottom-left "/" button) via `set_my_commands`.

# Self-hosted usage
The bot requires **Python 3.12** and uses `aiogram` 3.x. There is a `Dockerfile` and `run.sh` script for convenience.

You need to obtain your own bot token from https://t.me/BotFather, then run

```
PP_BOT_TOKEN=11111424242:some-token ./run.sh
```

It will recreate the image and container `ppbot`. The bot uses a sqlite database at host in `~/.ppbot/tg_pp_bot.db` (default).

## Environment variables
| Variable | Required | Default | Description |
|---|---|---|---|
| `PP_BOT_TOKEN` | yes | — | Telegram bot token |
| `PP_BOT_DB_PATH` | no | `~/.tg_pp_bot.db` | SQLite database path |
| `PP_BOT_TZ` | no | `Europe/Moscow` | Timezone for daily reminders (IANA name, e.g. `Europe/Moscow`) |

## Development
```
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
