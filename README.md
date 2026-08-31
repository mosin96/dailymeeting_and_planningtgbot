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
| `/who` | Who leads today (on a non-working day — «нерабочий день» + ближайший дейлик) |
| `/substitute` | Substitute today's leader |
| `/setleader @ник` | Manually set today's leader (or reply to a member's message) |
| `/vacation @ник ДД.ММ.ГГГГ-ДД.ММ.ГГГГ` | Отпуск с даты по дату (или ДД.ММ.ГГГГ — до даты; «снять» — убрать) |
| `/costremind` | Напоминания о списании трудозатрат (вкл/выкл, время) |
| `/vacplan` | Запланированные отпуска |
| `/help` | Full help text |

The "who leads today" reminder message contains two buttons available to any chat member:

- **«Подмените меня»** — today's scheduled leader and the scheduled leader of the next working day (следующего РАБОЧЕГО дня) swap places in the schedule for these two days (the member order in the team list is not changed).
- **«Пропуск»** — one-time skip without substitution (the position in the rotation stays the same).

Ротация сдвигается только в рабочие дни (производственный календарь РФ, isdayoff.ru; при недоступности API — Пн–Пт). Выходные и праздники не занимают слот.

The `/daily` menu also has two quick-action buttons:

- **«Выбрать ведущего»** — opens a member picker to manually set today's leader.
- **«Отпуск»** — opens a member picker to set a member's vacation (single date or date range).

The `/daily` menu also has a **«Напомн. о трудозатратах»** quick-action button that opens a settings menu for work-hours logging reminders: a toggle to enable/disable (`/costremind`) and a reminder time (default 17:00). On the last workday of the week the bot sends «Не забудьте списать трудозатраты!», and on the last workday of the month «Не забудьте списать трудозатраты за месяц!» (last workday determined via the RU production calendar, isdayoff.ru). The «Отпуск» member picker also has a **«Планируемые отпуска»** button (and a direct `/vacplan` command) that shows future vacations (с ДД.ММ.ГГГГ по ДД.ММ.ГГГГ).

### Manual leader override
`/setleader @ник` (or reply to a member's message, or pick from the `/daily` menu) makes the chosen member lead **today**. The rotation continues from the member after them — the queue order is not changed.

### Vacation

`/vacation @ник 05.08.2026-10.08.2026` marks a member as on vacation from the start to the end date (both inclusive). A single date `/vacation @ник 05.08.2026` is the legacy form and means "until that date (inclusive)". `/vacation @ник снять` clears both. While on vacation the member is automatically skipped in every rotation path (reminder tag, `/who`, `/daily` status, substitute, skip). The team list shows a `(в отпуске до ДД.ММ.ГГГГ)` suffix, and the 15-minute reminder appends `В отпуске: Имя (до ДД.ММ.ГГГГ)` using plain names (no `@` mention, no link). The suffix and the reminder appear only while the member is currently on vacation.

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
