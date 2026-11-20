# 🌍 Global Anonymous Chat Bot

Anonymous random chat on Telegram. Python 3.11+ · aiogram 3 · MongoDB · Telegram Stars · Railway/Docker.

- Mandatory profile (name, age, gender) with validation and editing; country is optional
- Random global matchmaking + **Premium-only gender matchmaking** that honours *both* users' preferences
- Anonymous relay of text, photos, videos, voice, audio, documents, stickers, GIFs, video notes, albums
  (contacts/locations only after an explicit privacy confirmation) using `copyMessage`/`copyMessages`
- Free (100 searches/day) and Premium (200/day) plans, daily counters reset at 00:00 UTC
- Premium via Telegram Stars (Monthly / Yearly), activated only after verified payment
- Report, block, unblock, ban, admin dashboard, rate limiting, health check, migrations, backups
- **Owner role**: every Premium feature always on (no payment), plus owner-only controls — broadcast,
  maintenance mode, runtime admin management, direct messages, bot info (see section 10)

## 1. Honest status

| Verified here | **Not** verified here (you must check) |
|---|---|
| 159 automated tests pass against a real MongoDB (matchmaking, both-way gender rules, limits, UTC reset, concurrency, payments, refunds, blocking, reporting, admin/owner authorization, owner Premium, broadcast, maintenance mode, relay, registration) | Talking to the real Telegram API with your token (this sandbox has no Telegram access) |
| Startup → migrations → health endpoint → graceful shutdown (with a faked Telegram session) | Docker image build and the Railway deployment |
| Config safety checks (refuses to start without `MIN_AGE`; production needs explicit confirmation) | Webhook mode against Telegram, real Stars purchases/refunds, real album delivery |

Run the test-suite yourself (section 8) and do the smoke test in section 9 before inviting users.

## 2. Decisions you must make before launch

1. **Minimum age** – deliberately *not* assumed. Set `MIN_AGE`; in `ENVIRONMENT=production` the bot also
   requires `MIN_AGE_POLICY_CONFIRMED=true`. For a stranger-chat service 18+ is the safer choice; check local law.
2. **Prices** – `MONTHLY_PRICE_STARS=250` and `YEARLY_PRICE_STARS=2000` are placeholders. Change them.
3. **Legal texts** – `docs/PRIVACY_POLICY.md` and `docs/TERMS_OF_SERVICE.md` are templates. Fill them in, get
   them reviewed, and keep the in-bot summaries (`app/utils/legal.py`) consistent.
4. **Moderation capacity** – anonymous chat attracts abuse. Plan who reviews `/reports` and how fast.
   Read Telegram's Bot Developer Terms and its rules on adult/illegal content, and Telegram's Stars policy.

## 3. Create the bot (BotFather)

1. Message **@BotFather** → `/newbot` → choose a name and a username ending in `bot` → copy the **token**.
2. `/setcommands` → paste:
   ```
   start - Start the bot
   help - Show help
   find - Find a random partner
   next - Disconnect and search again
   stop - End conversation
   profile - View profile
   report - Report current partner
   block - Block current partner
   unblock - Manage blocked users
   premium - Premium plans
   ```
   (The bot also sets these itself on startup.)
3. Get your numeric Telegram ID from **@userinfobot** → put it in `OWNER_IDS` (you are the owner). Moderators go in
   `ADMIN_IDS`, or you add them later with `/addadmin`.
4. Telegram Stars need no payment provider setup. If a token ever leaks: BotFather → `/revoke`.

## 4. Run locally

**Option A – Docker Compose (bot + MongoDB):**
```bash
cp .env.example .env     # fill BOT_TOKEN, ADMIN_IDS, MIN_AGE
docker compose up --build
```

**Option B – virtualenv + your own MongoDB:**
```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # fill it in; MONGODB_URI must point to a MongoDB
python -m app.main
```
Check `http://localhost:8080/health` → `{"status": "ok"}`. A free MongoDB: docker `mongo:7` or MongoDB Atlas.

## 5. Configuration (environment variables)

See `.env.example` for every variable with comments. Essentials: `BOT_TOKEN`, `OWNER_IDS`, `ADMIN_IDS`, `MONGODB_URI`,
`MIN_AGE`, `FREE_DAILY_LIMIT`, `PREMIUM_DAILY_LIMIT`, `MONTHLY_PRICE_STARS`, `YEARLY_PRICE_STARS`,
`BROADCAST_RATE_PER_SECOND`, `RUN_MODE` (`polling`|`webhook`), `QUEUE_TIMEOUT_SECONDS`, `CONVERSATION_IDLE_MINUTES`.

## 6. Deploy on Railway

1. Push this folder to a GitHub repository (never commit `.env`).
2. Railway → **New Project → Deploy from GitHub repo**. It builds the `Dockerfile`; `railway.toml` sets the
   health check (`/health`) and restart policy.
3. In the same project: **New → Database → MongoDB**.
4. In the bot service → **Variables**:
   ```
   BOT_TOKEN=...
   OWNER_IDS=123456789
   MONGODB_URI=${{MongoDB.MONGO_URL}}      # reference the MongoDB service (check the exact name in your project)
   MIN_AGE=18
   ENVIRONMENT=production
   MIN_AGE_POLICY_CONFIRMED=true
   RUN_MODE=webhook
   WEBHOOK_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(32))">
   MONTHLY_PRICE_STARS=...  YEARLY_PRICE_STARS=...
   ```
5. Bot service → **Settings → Networking → Generate Domain**. The app picks it up from `RAILWAY_PUBLIC_DOMAIN`
   (or set `WEBHOOK_BASE_URL=https://<domain>` yourself). Redeploy.
6. Logs should show `Starting @yourbot in webhook mode`. Message the bot `/start`.

**Why webhook on Railway?** With polling, two instances overlap during a redeploy and Telegram answers
`Conflict: terminated by other getUpdates request`. Webhook avoids that. Polling is fine locally.
**Run exactly one replica** (see Limitations).

## 7. Backups

- Use your MongoDB host's volume/snapshot backups **and** a logical dump:
  `MONGODB_URI=... ./scripts/backup.sh` (needs `mongodump`; keeps 14 days, configurable). Schedule it
  (cron / a Railway cron service) and copy the archives to storage outside the DB host.
- Restore: `mongorestore --uri "$MONGODB_URI" --gzip --archive=backups/<file>.archive --drop`
- **Test a restore** before you rely on it.

## 8. Tests

```bash
pip install -r requirements-dev.txt
# needs a running MongoDB; default mongodb://127.0.0.1:27017 (a throwaway DB is created and dropped per test)
TEST_MONGODB_URI=mongodb://127.0.0.1:27017 pytest -q
```
Tests are skipped (not failed) if MongoDB is unreachable. The handler tests drive the real Dispatcher with a
fake Telegram session, so no token is needed.

## 9. Smoke test after deploying

1. `/start` as a new user → terms → register → menu shows `Membership: FREE`, `Daily Searches: 0/100`.
2. With a second account: `/find` on both → both get "Partner found"; send text, photo, voice; try `/next`.
3. `/report`, `/block`, `/unblock`; as admin `/admin`, `/stats`, `/reports`, `/user <id>`.
4. Buy Monthly with Stars (test with a small price first) → Premium only after the payment succeeds;
   try an admin `/refund <user_id> <charge_id>`.
5. Redeploy while a chat is active → the chat must continue afterwards.

## 10. Commands

Users: `/start /help /find /next /stop /profile /report /block /unblock /premium /cancel /terms /privacy /paysupport`

Admins (`ADMIN_IDS`, admins added with `/addadmin`, and all owners): `/admin /stats /reports /user <id> /ban <id> [reason] /unban <id> /grant <id> <days> /revoke <id> /refund <id> <charge_id>`

**Owner** (`OWNER_IDS` only; send `/owner` in the bot for this list):

| Command | What it does |
|---|---|
| `/broadcast [--all\|--free\|--premium\|--active] <text>` | Message a group of users. HTML tags (`<b>`, `<i>`, `<a href>`) work in the text. |
| reply to any message with `/broadcast [--audience]` | Send that exact message (photo, video, formatting…) to users |
| `/broadcasts` | Last broadcasts with delivered / blocked / failed counts |
| `/maintenance on [message]` · `off` · *(none)* | Pause the bot for normal users (staff unaffected) / show status |
| `/addadmin <id>` · `/removeadmin <id>` · `/admins` | Manage moderators without redeploying |
| `/dm <id> <text>` | Private message to one user, sent as the bot |
| `/botinfo` | Uptime, database health/latency, traffic, running broadcasts |

**How the owner role works**
- Owners always have **Premium** (gender search, the Premium daily limit of 200) without paying; nothing is written
  to the subscription records, so it can never "expire". The menu shows `PREMIUM (owner)`; the buy buttons are hidden.
- Owners are also administrators and cannot be banned.
- **Broadcast safety:** every broadcast shows an exact preview first and sends only after you press **Send**; it is throttled
  (`BROADCAST_RATE_PER_SECOND`, default 20/s), handles Telegram flood waits, skips banned/unregistered users and users who
  blocked the bot (they are flagged and skipped next time), can be stopped while running, and reports its result to you.
  A restart during a broadcast marks it `interrupted` (it is not resumed).
- Owners and admins from the environment can only be changed in the environment; `/addadmin` admins are stored in MongoDB.

## 11. Architecture

```
app/
  main.py          startup: config → Mongo → migrations → bot → polling|webhook + /health + background tasks
  bootstrap.py     builds the Dispatcher (middlewares, routers; relay catch-all is LAST)
  config.py        env settings and safety validation
  container.py     wires services together
  handlers/        Telegram layer (start, registration, profile, matchmaking, messaging, premium,
                   payments, reports(+blocking), admin, owner)
  services/        business logic (users, usage, matchmaking, messages, subscriptions, moderation, stats,
                   staff, runtime settings, broadcast)
  database/        session.py (collections), models.py (schema reference), migrations/
  middlewares/     throttle (rate limit), access (private chats only, ban check)
  keyboards/ states/ utils/    UI, FSM states, validators, countries, legal texts
tests/             159 tests
```

**Collections** (your suggested tables, MongoDB style): `users`, `profiles`, `subscriptions`, `payments`,
`daily_usage`, `conversations`, `conversation_participants`, `messages_metadata`, `reports`, `blocked_users`,
`banned_users`, plus `matchmaking_queue`, `stats_daily`, `staff`, `broadcasts`, `bot_settings`, `schema_migrations`. Field reference: `app/database/models.py`.
`SQLAlchemy`/`Alembic` from the original brief do not work with MongoDB; `database/migrations/` is a small
versioned migration runner (index creation) that plays Alembic's role.

**Matchmaking concurrency** (no transactions needed, so it works on any MongoDB):
a searching user is a document in `matchmaking_queue`. A matcher *claims* a compatible partner with the atomic
`find_one_and_delete`, then claims itself with `delete_one`; if that fails someone else matched it first and the
partner is put back. `conversation_participants` uses the Telegram ID as `_id`, so the database itself prevents a
second active conversation. A background sweeper retries pairing and handles rare mutual-claim collisions.
**Gender rule:** partner must satisfy my preference *and* I must satisfy theirs; "prefer not to say" users can only
be matched by "any gender" searches. **Limits:** one atomic upsert (`count < limit`) per search; rejected requests
(already searching, in chat, free user choosing a gender, not registered) are never counted; "Next" counts.
**Payments:** `pre_checkout_query` validates payload/currency/amount; Premium is activated only from
`successful_payment`; the Stars charge ID has a unique index so replays and concurrent duplicates activate once.
Telegram Stars subscriptions are modelled as one-time purchases of 30 / 365 days that extend the expiry
(Telegram's native recurring Stars subscriptions only support 30-day periods).

## 12. Security & privacy notes

- Token, DB URI and admin IDs only via environment; `.env` is git-ignored; container runs as non-root.
- Partners never receive username, phone or Telegram ID; callback data uses opaque ids (no Telegram IDs).
- `copyMessage` is used, never `forwardMessage`. Message **content is never stored or logged**; only metadata
  (auto-deleted after `METADATA_TTL_DAYS`). Reports keep message *ids*, which admins can view from the reporter's chat.
- Inputs validated; all user text is HTML-escaped; MongoDB queries are parameterised (no string-built queries).
- Rate limiting per user, one report per conversation, ban checks on every update, admin handlers behind `IsAdmin`.
- Webhook is protected by a secret path **and** Telegram's `secret_token` header.
- Anonymity is between users only; the bot says so at registration, in `/help`, `/privacy`.

## 13. Known limitations

- **Single instance.** FSM state (registration/report dialogs) is in memory, and the in-process matchmaking lock
  assumes one bot process (the DB operations stay correct with several, but FSM state would not be shared).
  A restart only loses half-finished dialogs; chats, queue, limits and subscriptions live in MongoDB.
- Owner Premium uses the normal Premium limits (200 searches/day); it is not unlimited.
- Broadcasts are not resumed after a restart, and a very large audience takes time (20/s ≈ 1,200 users/minute).
- No "delete my account" command yet (do it manually via the database and your privacy contact).
- No automatic bans/AI moderation: reports are reviewed by humans.
- Telegram-side limits apply (file size limits, ~30 messages/second per bot overall). Very large traffic needs
  webhook behind several workers plus a shared FSM storage (e.g. Redis).
