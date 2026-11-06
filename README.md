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
| `/costremind` | Напоминания о списании трудозатрат (вкл/выкл, время) |
| `/vacplan` | Запланированные отпуска |
| `/help` | Full help text |

The "who leads today" reminder message contains two buttons available to any chat member:

- **«Подмените меня»** — today's scheduled leader and the scheduled leader of the next working day (следующего РАБОЧЕГО дня) swap places in the schedule for these two days (the member order in the team list is not changed).
- **«Пропуск»** — one-time skip without substitution (the position in the rotation stays the same).

Ротация сдвигается только в рабочие дни (государственные праздники по данным международного API date.nager.at; при недоступности — Пн–Пт). Выходные и праздники не занимают слот.

The `/daily` menu also has these quick-action buttons:

- **«Выбрать ведущего»** — opens a member picker to manually set today's leader.
- **«Запланир. отпуска»** — planned vacations view (also available via `/vacplan`).
- **«Напомн. о трудозатратах»** — opens a settings menu for work-hours logging reminders: a toggle to enable/disable (`/costremind`) and a reminder time (default 17:00). On the last workday of the week the bot sends «Не забудьте списать трудозатраты за неделю! Хороших выходных!», and on the last workday of the month «Не забудьте списать трудозатраты за месяц!» (last workday determined via the public-holidays calendar, date.nager.at).
- **«📅 Отпуска (мини-апп)»** — opens the mini-app vacation calendar (shown when the bot username is known at startup, see the Mini-app section).

### Vacation calendar mini-app

Vacations are managed in a Telegram mini-app (WebApp): open the `/daily` menu in the team chat and tap **«📅 Отпуска (мини-апп)»**. The app shows the team members (as colored chips) and a month calendar:

- Each member has a color; vacations are drawn as colored bars in the day cells of the range (inclusive).
- Team members are shown in a collapsible dropdown with **multi-select**: any combination of members can be displayed at once (the «Все» button selects everyone). By default the member who opened the app is selected, and the **«Я»** button quickly switches back to only your own vacations.
- To add a vacation: make sure exactly one member is selected (or press «Я»), tap the first day, then the last day (today or later), and confirm in the popup.
- To remove: tap the colored bar of the vacation and confirm in the popup.
- Hovering a day shows a tooltip with **everyone from the team who is on vacation that day** (name, color, vacation period; members hidden by the current selection are marked «скрыт»). On touch devices the same tooltip appears on a long press.

Any member of the daily standup roster can view and edit vacations. Every edit (add/remove) instantly rebuilds the daily rotation schedule, so `/who`, reminders and the calendar stay in sync.

How authorization works (`403 Вы не участник этой команды`):

1. Every request carries the Telegram `initData` payload; the server verifies its signature against the bot token and rejects payloads older than 24 hours.
2. The chat is taken from the signed `start_param` (the `t.me` deep link), never trusted from the query string.
3. The sender must be in the chat's `/team` roster (`daily_members`):
   - if the roster row has a Telegram id (member added via reply to their message), it must match the id from `initData`;
   - rows added manually by `@username` have no id, so they are matched by the signed `@username` from `initData`, and the numeric id is bound to the row on first open (after that the id alone is used, so a later username change does not lock the person out).
   - a row already bound to another Telegram id is never taken over by a username match.

Members added as an arbitrary name without `@username` (e.g. just «Иван») cannot be matched automatically. To grant them access, remove and re-add them via `/team` with `@username` or by replying to their message — the reply flow stores the Telegram id immediately.

#### Mini-app deployment (HTTPS)

Telegram opens mini-apps only via a public **HTTPS** URL. The cheapest free path is a Let's Encrypt certificate from [certbot](https://certbot.eff.org/) on the server that hosts the bot:

```bash
sudo certbot certonly --standalone -d BOT-DOMAIN.example.com
# certificates land in /etc/letsencrypt/live/BOT-DOMAIN.example.com/
sudo mkdir -p ~/.ppbot/certs
sudo cp /etc/letsencrypt/live/BOT-DOMAIN.example.com/fullchain.pem ~/.ppbot/certs/
sudo cp /etc/letsencrypt/live/BOT-DOMAIN.example.com/privkey.pem  ~/.ppbot/certs/
sudo chown -R $USER ~/.ppbot/certs
```

Then export the variables before `./run.sh` (the script mounts `~/.ppbot/certs` into the container and exposes port 8443):

```bash
export PP_WEBAPP_PUBLIC_URL=https://BOT-DOMAIN.example.com:8443
./run.sh
```

Certificates are renewed by certbot (add a `--renew` hook or a cron that copies the renewed files into `~/.ppbot/certs` and restarts the container). If no certificates are provided, the mini-app server starts in plain HTTP mode on port 8443 — fine for local debugging inside Telegram via [test tools](https://github.com/TelegramMessenger/Telegram-iOS/blob/main/submodules/TelegramUIPreferences/README.md), not for production.

##### Register the Mini App in @BotFather (required!)

Telegram hands `initData` (the signed user identity the API checks) **only** to real Mini Apps. A plain `https://...` URL button opens the page as a regular website inside Telegram's in-app browser — without `initData` — so the calendar API answers `401`. That is why the bot links use a `t.me` deep link instead.

1. Open [@BotFather](https://t.me/BotFather) → `/mybots` → your bot → **Bot Settings → Configure Mini App → Enable Mini App** (for a dedicated app use `/newapp` and remember its **short name**). Set the URL to `https://BOT-DOMAIN.example.com:8443/` (trailing slash optional, HTTPS only).
2. Optionally set **Main Mini App** (Bot Settings → Main Mini App) — then `https://t.me/<bot>?startapp=<chat_id>` opens it.
3. If you created a named Mini App, set `PP_WEBAPP_APP_NAME` (e.g. `vacations`) so the bot uses `https://t.me/<bot>/<app_name>?startapp=<chat_id>`; without it the bot uses the Main Mini App link.

The `/daily` menu button is built from the bot username (resolved via `getMe` at startup) and the chat id stored in the signed `start_param`, so the app knows which team it was opened from. If the username is unknown (e.g. `getMe` failed), the bot falls back to the raw `PP_WEBAPP_PUBLIC_URL?chat=...` link, which only works for debugging in a browser where you can inject init data manually.

| Variable | Required | Default | Description |
|---|---|---|---|
| `PP_WEBAPP_PORT` | no | `8443` | Port the mini-app server listens on (inside the container) |
| `PP_WEBAPP_CERT` | no | — | Path to the TLS certificate (PEM), enables HTTPS |
| `PP_WEBAPP_KEY` | no | — | Path to the TLS private key (PEM) |
| `PP_WEBAPP_APP_NAME` | no | — | Short name of the Mini App from @BotFather; makes `/daily` link to `t.me/<bot>/<name>` instead of the Main Mini App |
| `PP_WEBAPP_BOT_USERNAME` | no | auto (`getMe`) | Override the bot username used in `t.me` deep links |
| `PP_WEBAPP_PUBLIC_URL` | no | — | Public HTTPS base URL; used only as a debugging fallback when the bot username is unknown |

### Manual leader override
`/setleader @ник` (or reply to a member's message, or pick from the `/daily` menu) makes the chosen member lead **today**. The rotation continues from the member after them — the queue order is not changed.

### Vacation data model

Vacations live in the `daily_vacations` SQLite table (`id`, `chat_id`, `position`, `start_date`, `end_date`) — a member can have several periods, and the dates are inclusive. A row with `start_date = NULL` is the legacy form: «продолжается до end_date» (migrated from the old one-vacation-per-member columns; on the first start after the upgrade the bot copies `daily_members.vacation_until/vacation_start` into the table and clears the old columns, so the migration is idempotent). Removed members clean up their vacations; `/reset` also drops the calendar.

While a member is on vacation (any period covering today) they are automatically skipped in every rotation path (reminder tag, `/who`, `/daily` status, substitute, skip). The team list shows a `(в отпуске до ДД.ММ.ГГГГ)` suffix, and the 15-minute reminder appends `В отпуске: Имя (до ДД.ММ.ГГГГ)` using plain names (no `@` mention, no link).

Workdays are synchronized with the international [Nager.Date](https://date.nager.at) public-holidays API for the country configured in `PP_WORKDAY_COUNTRY` (ISO code, default `RU`, which includes New Year holidays and other Russian public holidays). A day is a workday when it is Mon–Fri and not a public holiday. The API needs no token and results are cached for 24 hours; if it is unavailable, the bot falls back to Mon–Fri (with a short negative cache, so an outage is logged once instead of every 30 seconds).

> Note: public-holiday services list official holidays but not ad-hoc government transfers of days off and working weekends («переносы»), so such days are treated as ordinary weekdays.

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
| `PP_WORKDAY_COUNTRY` | no | `RU` | ISO country code for the public-holidays calendar (date.nager.at), e.g. `RU`, `BY`, `KZ`, `DE` |
| `PP_WEBAPP_PORT` / `PP_WEBAPP_CERT` / `PP_WEBAPP_KEY` / `PP_WEBAPP_PUBLIC_URL` | no | see Mini-app section | Mini-app (vacation calendar) server settings |

## Development
```
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
