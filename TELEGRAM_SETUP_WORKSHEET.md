# Telegram Setup Worksheet

Use this file as a hands-on checklist for BackendScout's Telegram approval loop.
Fill the `TODO` values in your local `.env` after you complete the steps.

## 1. Create the bot

1. Open Telegram and talk to `@BotFather`.
2. Run `/newbot`.
3. Choose:
   - bot display name: `BackendScout`
   - bot username: `TODO_unique_bot_username`
4. Copy the token BotFather gives you.

Put it in `.env`:

```bash
TELEGRAM_BOT_TOKEN=TODO_bot_token_from_botfather
```

## 2. Start a direct chat with the bot

1. Search for your new bot by username.
2. Open the chat.
3. Press `Start` or send any message like `hello`.

This creates the first update the bot can read.

## 3. Find your Telegram numeric user ID

For the current slice, use a direct chat with the bot. In that case, your chat
ID is typically the same as your numeric Telegram user ID.

Run:

```bash
./.venv/bin/python -m backend_scout.cli telegram peek-updates
```

Look for output like:

```text
update_id=41 message from user_id=123456789 text='hello'
```

Then set:

```bash
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Then rerun:

```bash
./.venv/bin/python -m backend_scout.cli telegram check
```

## 4. Test the bot connection

Run:

```bash
./.venv/bin/python -m backend_scout.cli telegram check
```

Expected result:
- the bot token is accepted
- the bot username is printed
- your allowed user IDs are printed

## 5. Send a digest

Once the test Notion table contains jobs in `found` status, run:

```bash
./.venv/bin/python -m backend_scout.cli telegram send-digest --chat-id TODO_numeric_user_id
```

Expected result:
- one Telegram message per job
- inline buttons:
  - `Approve tailoring`
  - `Close`
- the job status in Notion moves from `found` to `digest_sent`

## 6. Process approval clicks

After pressing a button in Telegram, run:

```bash
./.venv/bin/python -m backend_scout.cli telegram poll-once
```

Expected result:
- the approval is processed once
- the Notion status changes accordingly
- the Telegram buttons are cleared from that message
- a confirmation message is sent back to the chat

## Local `.env` Template

```bash
TELEGRAM_BOT_TOKEN=TODO_bot_token_from_botfather
TELEGRAM_ALLOWED_USER_IDS=TODO_numeric_user_id
```
