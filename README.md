# 🚨 Moderator Report & Logging Tool

A **button-driven Telegram reporting system** built on Pyrogram. Validate targets across multiple user sessions, log the results in a dedicated log group, and run complaint submissions with live progress updates.

## Deployment-time setup (minimal)

Only three values are required when you deploy:

1. `PRIMARY_SESSION` — exactly one bootstrap Telegram client session string.
2. `LOG_GROUP_LINK` — the group/channel invite or username link where panels and logs will be posted. Every session
   (including additional user sessions) will automatically join this log group via the invite link before use.
3. `OWNER_ID` — Telegram user ID of the owner who can change any setting after deployment.

API credentials (`API_ID` and `API_HASH`) can be provided via environment variables or `config.json`. Everything else is configured later from the log group.

`config.json` contains the minimal deployment fields and optional API credentials:

```json
{
  "API_ID": null,
  "API_HASH": "",
  "PRIMARY_SESSION": "",
  "LOG_GROUP_LINK": "",
  "OWNER_ID": null
}
```

## Post-deployment owner controls

From the log group (owner only):

- Add more sessions at any time without redeploying.
- Set or change the target group/channel link and the exact message link.
- Configure report type, reason, body text, session limits, and number of reports.
- Update log-group metadata shown on panels.

Changes are persisted immediately to `state.json` for runtime data and `config.json` for the minimal deployment values.

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
