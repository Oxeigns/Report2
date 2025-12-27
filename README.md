# 🚨 Moderator Report & Logging Tool

A **button-driven Telegram reporting system** built on Pyrogram. Validate targets across multiple user sessions, log the results in a dedicated log group, and run complaint submissions with live progress updates.

## Deployment-time setup (one-time)

Set `API_ID`, `API_HASH`, and provide exactly one primary session string (via `SESSION_1` or a file in `sessions/`). During first start, set:

- `OWNER_ID` — only this user can change settings.
- `LOG_GROUP_LINK` — where control panels and logs are posted (optional `LOG_GROUP_ID` fallback).

All other options are configured later from inside the log group.

`config.json` defaults are provided for convenience:

```json
{
  "API_ID": "your_api_id",
  "API_HASH": "your_api_hash",
  "REPORT_TEXT": "",
  "REPORT_REASON": "other",
  "REPORT_TYPE": "standard",
  "TOTAL_REPORTS": null,
  "REPORT_SESSION_LIMIT": 0,
  "OWNER_ID": null,
  "LOG_GROUP_ID": 0,
  "LOG_GROUP_LINK": "",
  "GROUP_MESSAGE_LINK": "",
  "TARGET_GROUP_LINK": "",
  "TARGET_MESSAGE_LINK": ""
}
```

## Post-deployment owner controls

From the log group (owner only):

- Add more sessions at any time without redeploying.
- Set or change the target group/channel link and the exact message link.
- Configure report type, reason, body text, session limits, and number of reports.
- Update log-group metadata shown on panels.

Changes are persisted immediately to `config.json`.

## Guided usage

1. Send `/start` in the log group or a private chat with the owner to open the control panel.
2. Use the buttons to add sessions, set/change the target, configure report settings, and launch reporting.
3. The bot validates targets by joining with every session (public or private links, including `https://t.me/+invite` and `https://t.me/c/<id>/<msg>`).
4. A live reporting panel shows total/active sessions, successes, failures, and lets you pause/resume or retarget instantly.

Legacy commands (`/set_owner`, `/set_reason`, `/set_report_text`, `/set_total_reports`, `/set_links`, `/add_session`, `/run`) remain available but the button flow is recommended.

## Safety Notice

- This tool sends real Telegram complaints via `functions.messages.Report`. Use responsibly and only on content that violates Telegram rules.
- Keep your session strings private and secure.

## Running locally

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Oxeigns/Report2/tree/main)

```bash
pip install -r requirements.txt
python app.py
```

Ensure the primary session and API credentials are set before starting the tool.
