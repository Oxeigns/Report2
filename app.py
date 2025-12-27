import asyncio
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple, Union

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, InviteHashExpired, RPCError
from pyrogram.raw import functions, types

CONFIG_PATH = "config.json"
SESSIONS_DIR = "sessions"


# ----------------------
# Configuration helpers
# ----------------------
def load_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError("Missing config.json")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "OWNER_ID" not in config:
        config["OWNER_ID"] = None

    config.setdefault("REPORT_REASON", None)
    config.setdefault("REPORT_TEXT", "")
    config.setdefault("TOTAL_REPORTS", None)
    config.setdefault("LOG_GROUP_LINK", "")
    config.setdefault("GROUP_MESSAGE_LINK", "")

    return config


def save_config(config: Dict) -> None:
    tmp_path = f"{CONFIG_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def load_session_strings(max_count: int) -> List[Tuple[str, str]]:
    sessions: List[Tuple[str, str]] = []

    # Environment sessions
    for key, value in sorted(os.environ.items()):
        if key.startswith("SESSION_") and value.strip():
            sessions.append((key, value.strip()))

    # Session files
    if os.path.isdir(SESSIONS_DIR):
        for filename in sorted(os.listdir(SESSIONS_DIR)):
            path = os.path.join(SESSIONS_DIR, filename)
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        sessions.append((filename, content))

    if max_count:
        sessions = sessions[:max_count]
    return sessions


CONFIG = load_config()
API_ID = int(os.getenv("API_ID", CONFIG.get("API_ID", 0)))
API_HASH = os.getenv("API_HASH", CONFIG.get("API_HASH", ""))
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", CONFIG.get("LOG_GROUP_ID", 0) or 0))
OWNER_ID = CONFIG.get("OWNER_ID")
LOG_GROUP_LINK = CONFIG.get("LOG_GROUP_LINK", "")
GROUP_MESSAGE_LINK = CONFIG.get("GROUP_MESSAGE_LINK", "")

if not API_ID or not API_HASH:
    raise RuntimeError("API_ID and API_HASH must be configured")

if not load_session_strings(1):
    raise RuntimeError("At least one SESSION string or session file is required")

PRIMARY_SESSION = load_session_strings(1)[0][1]

# ----------------------
# Utilities
# ----------------------

def format_help() -> str:
    return (
        "**Moderator Report & Logging Tool**\n"
        "Quickly validate a target Telegram message across multiple user sessions, log the results, and submit reports with "
        "clear, auditable updates.\n\n"
        "Core commands (owner only):\n"
        "• `/run <target_link> <sessions_count> <requested_count>` — validate and report against a target message.\n"
        "• `/set_owner <telegram_id>` — assign or change the OWNER_ID when authorized.\n"
        "• `/set_reason <reason>` — update the report reason (child_abuse, violence, illegal_goods, illegal_adult, personal_data, scam, copyright, spam, other).\n"
        "• `/set_report_text <text>` — set the report text/message body.\n"
        "• `/set_total_reports <count>` — record or revise the total number of reports for the log group.\n"
        "• `/set_links <log_group_link> <group_message_link>` — refresh invite and message links shown in the review panel.\n"
        "• `/add_session <name> <session_string>` — register an additional session string without redeploying.\n\n"
        "Input rules for `/run`:\n"
        "• target_link: https://t.me/<username>/<message_id> or https://t.me/c/<internal_id>/<message_id>\n"
        "• sessions_count: integer 1-100 (number of sessions to use)\n"
        "• requested_count: integer 1-500 (for logging reference)\n\n"
        "Authorization & safety:\n"
        "• Only OWNER_ID can run owner-level commands.\n"
        "• Reports are sent via Telegram API (functions.messages.Report) and all logging remains in the configured log group.\n"
    )


def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    link = link.strip()
    pattern_username = r"^https://t\.me/([A-Za-z0-9_]+)/([0-9]+)$"
    pattern_c = r"^https://t\.me/c/([0-9]+)/([0-9]+)$"

    m_username = re.match(pattern_username, link)
    if m_username:
        chat = m_username.group(1)
        msg_id = int(m_username.group(2))
        return chat, msg_id

    m_c = re.match(pattern_c, link)
    if m_c:
        internal_id = m_c.group(1)
        msg_id = int(m_c.group(2))
        chat_id = int(f"-100{internal_id}")
        return chat_id, msg_id

    return None, None


def reason_from_config() -> types.TypeInputReportReason:
    mapping = {
        "child_abuse": types.InputReportReasonChildAbuse,
        "violence": types.InputReportReasonViolence,
        "illegal_goods": types.InputReportReasonIllegalDrugs,
        "illegal_adult": types.InputReportReasonPornography,
        "personal_data": types.InputReportReasonPersonalDetails,
        "scam": types.InputReportReasonSpam,
        "copyright": types.InputReportReasonCopyright,
        "spam": types.InputReportReasonSpam,
        "other": types.InputReportReasonOther,
    }

    configured_reason = os.getenv("REPORT_REASON", CONFIG.get("REPORT_REASON"))
    if configured_reason:
        normalized = str(configured_reason).strip().lower()
        if normalized in mapping:
            return mapping[normalized]()

    legacy_mapping = {
        "REPORT_REASON_CHILD_ABUSE": types.InputReportReasonChildAbuse,
        "REPORT_REASON_VIOLENCE": types.InputReportReasonViolence,
        "REPORT_REASON_ILLEGAL_GOODS": types.InputReportReasonIllegalDrugs,
        "REPORT_REASON_ILLEGAL_ADULT": types.InputReportReasonPornography,
        "REPORT_REASON_PERSONAL_DATA": types.InputReportReasonPersonalDetails,
        "REPORT_REASON_SCAM": types.InputReportReasonSpam,
        "REPORT_REASON_COPYRIGHT": types.InputReportReasonCopyright,
        "REPORT_REASON_SPAM": types.InputReportReasonSpam,
        "REPORT_REASON_OTHER": types.InputReportReasonOther,
    }
    for key, cls in legacy_mapping.items():
        val = os.getenv(key, str(CONFIG.get(key, "false"))).lower()
        if val == "true":
            return cls()
    return types.InputReportReasonOther()


REPORT_REASON = reason_from_config()
REPORT_TEXT = os.getenv("REPORT_TEXT", CONFIG.get("REPORT_TEXT", ""))


async def send_log_message(client: Client, chat_id: int, text: str) -> Optional[int]:
    try:
        msg = await client.send_message(chat_id, text)
        return msg.id
    except InviteHashExpired:
        if LOG_GROUP_ID:
            msg = await client.send_message(LOG_GROUP_ID, text)
            return msg.id
    except RPCError:
        return None
    return None


async def edit_log_message(client: Client, chat_id: int, message_id: int, text: str) -> None:
    try:
        await client.edit_message_text(chat_id, message_id, text)
    except InviteHashExpired:
        if LOG_GROUP_ID:
            await client.edit_message_text(LOG_GROUP_ID, message_id, text)
    except RPCError:
        pass


async def evaluate_session(session_name: str, session_str: str, target: str, message_id: int) -> Tuple[str, str]:
    try:
        async with Client(
            name=f"session_{session_name}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_str,
            no_updates=True,
        ) as user_client:
            me = await user_client.get_me()
            try:
                msg = await user_client.get_messages(target, message_id)
                peer = await user_client.resolve_peer(target)
                await user_client.invoke(
                    functions.messages.Report(
                        peer=peer,
                        id=[msg.id],
                        reason=REPORT_REASON,
                        message=REPORT_TEXT,
                    )
                )
                return "reachable", f"Session {me.id} ok"
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return "floodwait", f"FloodWait {e.value}s"
            except RPCError as e:
                return "inaccessible", f"RPC error: {e.MESSAGE or e}"  # type: ignore
    except RPCError as e:
        if isinstance(e, FloodWait):
            await asyncio.sleep(e.value)
            return "floodwait", f"FloodWait {e.value}s"
        return "invalid", f"Session error: {e.MESSAGE or e}"  # type: ignore
    except Exception as e:  # noqa: BLE001
        return "invalid", f"Unexpected: {e}"


async def handle_run_command(client: Client, message) -> None:
    global OWNER_ID
    if OWNER_ID is None or OWNER_ID != message.from_user.id:
        await message.reply_text("❌ Authorization failed. Only OWNER_ID can run this command.")
        return

    parts = message.text.split()
    if len(parts) != 4:
        await message.reply_text("Usage: /run <target_link> <sessions_count> <requested_count>")
        return

    _, target_link, sessions_count_raw, requested_count_raw = parts

    try:
        sessions_count = int(sessions_count_raw)
    except ValueError:
        await message.reply_text("sessions_count must be an integer between 1 and 100")
        return

    try:
        requested_count = int(requested_count_raw)
    except ValueError:
        await message.reply_text("requested_count must be an integer between 1 and 500")
        return

    if not 1 <= sessions_count <= 100:
        await message.reply_text("sessions_count must be between 1 and 100")
        return
    if not 1 <= requested_count <= 500:
        await message.reply_text("requested_count must be between 1 and 500")
        return

    chat_identifier, msg_id = parse_link(target_link)
    if chat_identifier is None or msg_id is None:
        await message.reply_text("❌ Invalid link. Use https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>")
        return

    sessions = load_session_strings(sessions_count)
    if not sessions:
        await message.reply_text("No session strings found to run validation")
        return

    available_sessions = len(sessions)

    panel_lines = [
        "🛰️ **Review Panel Initialized**",
        f"Target message: {target_link}",
        f"Chat reference: {chat_identifier}",
        f"Message ID: {msg_id}",
        f"Requested sessions: {sessions_count}",
        f"Requested count: {requested_count}",
        f"Available sessions: {available_sessions}",
        f"Configured total reports: {CONFIG.get('TOTAL_REPORTS') or '—'}",
        f"Report reason: {CONFIG.get('REPORT_REASON') or 'other'}",
        f"Report text: {REPORT_TEXT or 'Not set'}",
    ]
    if LOG_GROUP_LINK:
        panel_lines.append(f"Log group link: {LOG_GROUP_LINK}")
    if GROUP_MESSAGE_LINK:
        panel_lines.append(f"Group message link: {GROUP_MESSAGE_LINK}")
    panel_lines.append("Status: processing…")
    panel_text = "\n".join(panel_lines)
    panel_chat = message.chat.id if message.chat else LOG_GROUP_ID
    panel_id = await send_log_message(client, panel_chat, panel_text)

    results: List[str] = []
    reachable = 0
    processed = 0

    for session_name, session_str in sessions:
        status, detail = await evaluate_session(session_name, session_str, chat_identifier, msg_id)
        processed += 1
        if status == "reachable":
            reachable += 1
        results.append(f"• **{session_name}** — {status} ({detail})")

        panel_text = (
            "🛰️ **Review Panel**\n"
            "**Target details**\n"
            f"• Link: {target_link}\n"
            f"• Chat: {chat_identifier} | Message: {msg_id}\n"
            f"• Requested sessions: {sessions_count} | Requested count: {requested_count}\n"
            f"• Configured total reports: {CONFIG.get('TOTAL_REPORTS') or '—'}\n"
            f"• Report reason: {CONFIG.get('REPORT_REASON') or 'other'} | Text: {REPORT_TEXT or 'Not set'}\n"
            + (f"• Log group link: {LOG_GROUP_LINK}\n" if LOG_GROUP_LINK else "")
            + (f"• Group message link: {GROUP_MESSAGE_LINK}\n" if GROUP_MESSAGE_LINK else "")
            + "\n"
            "**Session results**\n"
            f"• Available sessions: {available_sessions}\n"
            f"• Validated: {processed}/{min(sessions_count, available_sessions)}\n"
            f"• Reachable: {reachable}/{processed}\n\n"
            "\n".join(results)
        )
        if panel_id:
            await edit_log_message(client, panel_chat, panel_id, panel_text)

    await message.reply_text("✅ Run completed. Check the review panel for details.")


async def handle_set_owner(client: Client, message) -> None:
    global OWNER_ID
    parts = message.text.split()
    if len(parts) != 2:
        await message.reply_text("Usage: /set_owner <telegram_id>")
        return

    if OWNER_ID is not None and message.from_user.id != OWNER_ID:
        await message.reply_text("❌ Only the current owner can change OWNER_ID.")
        return

    try:
        new_owner = int(parts[1])
    except ValueError:
        await message.reply_text("telegram_id must be an integer")
        return

    CONFIG["OWNER_ID"] = new_owner
    OWNER_ID = new_owner
    save_config(CONFIG)
    await message.reply_text(f"✅ OWNER_ID set to {new_owner}")


def owner_required(message) -> bool:
    if OWNER_ID is None or not message.from_user:
        return False
    return message.from_user.id == OWNER_ID


async def handle_set_reason(message) -> None:
    global REPORT_REASON
    if not owner_required(message):
        await message.reply_text("❌ Only the log group owner can update the report reason.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply_text("Usage: /set_reason <child_abuse|violence|illegal_goods|illegal_adult|personal_data|scam|copyright|spam|other>")
        return

    value = parts[1].strip().lower()
    reason_map = {
        "child_abuse": types.InputReportReasonChildAbuse,
        "violence": types.InputReportReasonViolence,
        "illegal_goods": types.InputReportReasonIllegalDrugs,
        "illegal_adult": types.InputReportReasonPornography,
        "personal_data": types.InputReportReasonPersonalDetails,
        "scam": types.InputReportReasonSpam,
        "copyright": types.InputReportReasonCopyright,
        "spam": types.InputReportReasonSpam,
        "other": types.InputReportReasonOther,
    }

    if value not in reason_map:
        await message.reply_text("❌ Invalid reason. Choose one of: child_abuse, violence, illegal_goods, illegal_adult, personal_data, scam, copyright, spam, other.")
        return

    CONFIG["REPORT_REASON"] = value
    save_config(CONFIG)
    REPORT_REASON = reason_map[value]()
    await message.reply_text(f"✅ Report reason updated to `{value}`.")


async def handle_set_report_text(message) -> None:
    global REPORT_TEXT
    if not owner_required(message):
        await message.reply_text("❌ Only the log group owner can update the report text.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.reply_text("Usage: /set_report_text <text>")
        return

    REPORT_TEXT = parts[1].strip()
    CONFIG["REPORT_TEXT"] = REPORT_TEXT
    save_config(CONFIG)
    await message.reply_text("✅ Report text updated.")


async def handle_set_total_reports(message) -> None:
    if not owner_required(message):
        await message.reply_text("❌ Only the log group owner can update the total reports.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply_text("Usage: /set_total_reports <count>")
        return

    try:
        total_reports = int(parts[1])
    except ValueError:
        await message.reply_text("❌ total_reports must be an integer.")
        return

    if total_reports < 0:
        await message.reply_text("❌ total_reports cannot be negative.")
        return

    CONFIG["TOTAL_REPORTS"] = total_reports
    save_config(CONFIG)
    await message.reply_text(f"✅ Total reports set to {total_reports}.")


async def handle_set_links(message) -> None:
    global LOG_GROUP_LINK, GROUP_MESSAGE_LINK
    if not owner_required(message):
        await message.reply_text("❌ Only the log group owner can update links.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.reply_text("Usage: /set_links <log_group_link> <group_message_link>")
        return

    log_group_link = parts[1].strip()
    message_link = parts[2].strip()

    if not (log_group_link.startswith("http://") or log_group_link.startswith("https://")):
        await message.reply_text("❌ log_group_link must start with http:// or https://")
        return

    if not (message_link.startswith("http://") or message_link.startswith("https://")):
        await message.reply_text("❌ group_message_link must start with http:// or https://")
        return

    LOG_GROUP_LINK = log_group_link
    GROUP_MESSAGE_LINK = message_link
    CONFIG["LOG_GROUP_LINK"] = log_group_link
    CONFIG["GROUP_MESSAGE_LINK"] = message_link
    save_config(CONFIG)
    await message.reply_text("✅ Links updated for the review panel.")


async def handle_add_session(message) -> None:
    if not owner_required(message):
        await message.reply_text("❌ Only the log group owner can add sessions.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.reply_text("Usage: /add_session <name> <session_string>")
        return

    name = parts[1].strip()
    session_str = parts[2].strip()

    if not name or not re.match(r"^[A-Za-z0-9_\-]{1,64}$", name):
        await message.reply_text("❌ Session name must be 1-64 characters (letters, numbers, underscores, hyphens).")
        return

    if len(session_str) < 10:
        await message.reply_text("❌ Session string looks too short. Please provide a valid session string.")
        return

    os.makedirs(SESSIONS_DIR, exist_ok=True)
    dest = os.path.join(SESSIONS_DIR, f"{name}.session")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(session_str)

    await message.reply_text(f"✅ Session `{name}` added. It will be used on the next /run.")


async def main():
    app = Client(
        "moderator_tool",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=PRIMARY_SESSION,
    )

    @app.on_message(filters.command("help"))
    async def _help(_, msg):
        await msg.reply_text(format_help())

    @app.on_message(filters.command("set_owner"))
    async def _set_owner(client, msg):
        await handle_set_owner(client, msg)

    @app.on_message(filters.command("run"))
    async def _run(client, msg):
        await handle_run_command(client, msg)

    @app.on_message(filters.command("set_reason"))
    async def _set_reason(_, msg):
        await handle_set_reason(msg)

    @app.on_message(filters.command("set_report_text"))
    async def _set_report_text(_, msg):
        await handle_set_report_text(msg)

    @app.on_message(filters.command("set_total_reports"))
    async def _set_total_reports(_, msg):
        await handle_set_total_reports(msg)

    @app.on_message(filters.command("set_links"))
    async def _set_links(_, msg):
        await handle_set_links(msg)

    @app.on_message(filters.command("add_session"))
    async def _add_session(_, msg):
        await handle_add_session(msg)

    await app.start()
    print("Moderator tool is running...")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
