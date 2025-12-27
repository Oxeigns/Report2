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
        "Validate access to a target Telegram message across multiple user sessions, "
        "log the results, and trigger automated reports.\n\n"
        "Commands:\n"
        "• `/help` — show this message.\n"
        "• `/set_owner <telegram_id>` — set or change the OWNER_ID (only current owner or unset).\n"
        "• `/run <target_link> <sessions_count> <requested_count>` — run validation & reporting.\n\n"
        "Input format:\n"
        "• target_link: https://t.me/<username>/<message_id> or https://t.me/c/<internal_id>/<message_id>\n"
        "• sessions_count: integer 1-100 (number of sessions to use)\n"
        "• requested_count: integer 1-500 (for logging reference)\n\n"
        "Authorization:\n"
        "• Only OWNER_ID can run /run.\n"
        "• /set_owner allowed when OWNER_ID is unset or by current owner.\n\n"
        "Safety:\n"
        "• Uses Telegram API reports (functions.messages.Report).\n"
        "• Logging stays inside the configured log group.\n"
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
    for key, cls in mapping.items():
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

    panel_text = (
        "🛰️ **Review Panel Initialized**\n"
        f"Target: {target_link}\n"
        f"Chat: {chat_identifier}\n"
        f"Message ID: {msg_id}\n"
        f"Requested sessions: {sessions_count}\n"
        f"Requested count: {requested_count}\n"
        f"Available sessions: {available_sessions}\n"
        "Processing..."
    )
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
        results.append(f"• {session_name}: {status} ({detail})")

        panel_text = (
            "🛰️ **Review Panel**\n"
            f"Target: {target_link}\n"
            f"Chat: {chat_identifier}\n"
            f"Message ID: {msg_id}\n"
            f"Requested sessions: {sessions_count}\n"
            f"Requested count: {requested_count}\n"
            f"Available sessions: {available_sessions}\n"
            f"Sessions validated: {processed}/{min(sessions_count, available_sessions)}\n"
            f"Reachable sessions: {reachable}/{processed}\n\n"
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

    await app.start()
    print("Moderator tool is running...")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
