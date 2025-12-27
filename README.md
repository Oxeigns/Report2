# 🚨 Moderator Report & Logging Tool

This project is a **Pyrogram-based moderator helper** that validates whether a target Telegram message is reachable across multiple user sessions, logs the results in a log group, and issues automated Telegram complaints using `functions.messages.Report`.

## Features

- Multi-session validation with detailed per-session status
- Automated complaint submission using `functions.messages.Report`
- Live review panel inside the log group with ongoing updates
- Strict command permissions via `OWNER_ID`
- Clear help command and safe error handling

## Configuration

Update `config.json` with your credentials:

```json
{
  "API_ID": "your_api_id",
  "API_HASH": "your_api_hash",
  "REPORT_TEXT": "Illegal content detected",
  "REPORT_REASON_CHILD_ABUSE": true,
  "REPORT_REASON_VIOLENCE": false,
  "REPORT_REASON_ILLEGAL_GOODS": false,
  "REPORT_REASON_ILLEGAL_ADULT": false,
  "REPORT_REASON_PERSONAL_DATA": false,
  "REPORT_REASON_SCAM": false,
  "REPORT_REASON_COPYRIGHT": false,
  "REPORT_REASON_SPAM": false,
  "REPORT_REASON_OTHER": false,
  "OWNER_ID": null,
  "LOG_GROUP_ID": 0
}
```

Set the report reason flags so that exactly one is `true`. You must also provide session strings via environment variables (`SESSION_1`, `SESSION_2`, …) or files inside a `sessions/` directory. The first session is used to run the command listener; additional sessions are used for validation.

## Usage

Commands are issued in the configured log group.

- `/help` — show all usage instructions.
- `/set_owner <telegram_id>` — can be used once when `OWNER_ID` is `null`, or later by the current owner to update ownership.
- `/run <target_link> <sessions_count> <requested_count>` — runs validation and reporting.

### Input rules for `/run`

- `target_link` must be `https://t.me/<username>/<message_id>` or `https://t.me/c/<internal_id>/<message_id>`.
- `sessions_count` must be an integer between **1** and **100** (how many sessions to test).
- `requested_count` must be an integer between **1** and **500** (logged for reference).

If any validation fails, the bot returns a clear error instead of running.

### Review panel

When `/run` is executed by the owner, the tool:

1. Iterates through the available sessions (up to `sessions_count`).
2. For each session, connects, validates with `get_me()`, fetches the target message, and attempts `functions.messages.Report`.
3. Logs the accessibility result (reachable, inaccessible, floodwait, invalid) in the log group.
4. Edits a live **Review Panel** message with target link, parsed chat/message IDs, requested counts, validated session totals, reachable totals, and per-session errors.

FloodWaits are handled automatically with pauses, and if the log group invite link expires the tool falls back to `LOG_GROUP_ID` when possible.

## Safety Notice

- This tool sends real Telegram complaints via `functions.messages.Report`. Use responsibly and only on content that violates Telegram rules.
- Keep your session strings private and secure.

## Running locally

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Oxeigns/Report2/tree/main)


```bash
pip install -r requirements.txt
python app.py
```

Ensure you have set the required environment variables and sessions before starting the tool.
