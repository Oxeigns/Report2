import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from pyrogram import Client, filters
from pyrogram.errors import (
    FloodWait,
    InviteHashExpired,
    InviteHashInvalid,
    RPCError,
    UserAlreadyParticipant,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.raw import functions, types
from pyrogram.raw.types import (
    InputReportReasonSpam,
    InputReportReasonOther,
    InputReportReasonChildAbuse,
    InputReportReasonViolence,
    InputReportReasonIllegalDrugs,
    InputReportReasonPornography,
    InputReportReasonPersonalDetails,
    InputReportReasonCopyright,
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
SESSIONS_DIR = "sessions"

@dataclass
class TargetContext:
    group_link: Optional[str] = None
    message_link: Optional[str] = None
    chat_identifier: Optional[Union[str, int]] = None
    message_id: Optional[int] = None
    chat_title: Optional[str] = None
    message_preview: Optional[str] = None
    active_sessions: int = 0
    validation_notes: List[str] = field(default_factory=list)

@dataclass
class ReportSettings:
    report_type: str = "standard"
    report_reason_key: str = "other"
    report_text: str = ""
    report_total: Optional[int] = None
    session_limit: int = 0

@dataclass
class ConversationState:
    mode: str = "idle"
    target: TargetContext = field(default_factory=TargetContext)
    report: ReportSettings = field(default_factory=ReportSettings)
    paused: bool = False
    live_panel: Optional[int] = None
    live_panel_chat: Optional[int] = None
    pending_session_name: Optional[str] = None
    pending_sudo_action: Optional[str] = None
    last_panel_text: str = ""

USER_STATES: Dict[int, ConversationState] = {}

REASON_MAP = {
    "child_abuse": InputReportReasonChildAbuse,
    "violence": InputReportReasonViolence,
    "illegal_goods": InputReportReasonIllegalDrugs,
    "illegal_adult": InputReportReasonPornography,
    "personal_data": InputReportReasonPersonalDetails,
    "scam": InputReportReasonSpam,
    "copyright": InputReportReasonCopyright,
    "spam": InputReportReasonSpam,
    "other": InputReportReasonOther,
}

def resolve_reason_class(key: str):
    return REASON_MAP.get(key.lower(), InputReportReasonOther)()

def load_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError("Missing config.json")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    config.setdefault("API_ID", None)
    config.setdefault("API_HASH", "")
    config.setdefault("PRIMARY_SESSION", "")
    config.setdefault("LOG_GROUP_LINK", "")
    config.setdefault("OWNER_ID", None)
    return config

def parse_int(value: Optional[Union[str, int]]) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0

def load_state() -> Dict:
    if not os.path.exists(STATE_PATH):
        return {
            "report": {
                "type": "standard",
                "reason": "other",
                "text": "",
                "total": None,
                "session_limit": 0,
            },
            "log_group_id": None,
            "sudo_user_ids": [],
        }
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
STATE_DATA = load_state()
API_ID = parse_int(os.getenv("API_ID") or CONFIG.get("API_ID"))
API_HASH = os.getenv("API_HASH") or CONFIG.get("API_HASH", "")
OWNER_ID = parse_int(os.getenv("OWNER_ID") or CONFIG.get("OWNER_ID"))
LOG_GROUP_LINK = CONFIG.get("LOG_GROUP_LINK", "")
PRIMARY_SESSION = CONFIG.get("PRIMARY_SESSION", "")

REPORT_REASON = resolve_reason_class(STATE_DATA["report"].get("reason", "other"))
REPORT_TEXT = STATE_DATA["report"].get("text", "")

def is_owner(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id == OWNER_ID

def has_power(user_id: Optional[int]) -> bool:
    return is_owner(user_id) or user_id in STATE_DATA.get("sudo_user_ids", [])

def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    m1 = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)$", link)
    m2 = re.match(r"^https?://t\.me/c/(\d+)/(\d+)$", link)
    if m1:
        return m1.group(1), int(m1.group(2))
    if m2:
        return int(f"-100{m2.group(1)}"), int(m2.group(2))
    return None, None

def load_session_strings(max_count: int = 0, include_primary: bool = True) -> List[Tuple[str, str]]:
    sessions = []
    if include_primary and PRIMARY_SESSION:
        sessions.append(("primary", PRIMARY_SESSION))
    if os.path.isdir(SESSIONS_DIR):
        for filename in sorted(os.listdir(SESSIONS_DIR)):
            with open(os.path.join(SESSIONS_DIR, filename), "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    sessions.append((filename, content))
    return sessions[:max_count] if max_count else sessions

async def join_target_chat(client: Client, join_link: str, chat_identifier: Union[str, int]):
    try:
        chat = await client.join_chat(join_link)
        return await client.resolve_peer(chat.id), "✅ Joined"
    except UserAlreadyParticipant:
        return await client.resolve_peer(chat_identifier), "Already a member"
    except (InviteHashExpired, InviteHashInvalid, UsernameInvalid, UsernameNotOccupied):
        return None, "❌ Invalid link"
    except RPCError as e:
        return None, f"RPC error: {str(e)}"

async def evaluate_session(session_name, session_str, join_link, target, message_id, *, reason=None, report_text=None):
    try:
        async with Client(
            name=f"session_{session_name}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_str,
            no_updates=True
        ) as user_client:
            me = await user_client.get_me()
            peer, status = await join_target_chat(user_client, join_link, target)
            if not peer:
                return "invalid", f"Join failed: {status}"
            msg = await user_client.get_messages(target, message_id)
            await user_client.invoke(
                functions.messages.Report(
                    peer=peer,
                    id=[msg.id],
                    reason=reason or REPORT_REASON,
                    message=report_text or REPORT_TEXT
                )
            )
            return "reachable", f"{me.id} OK"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return "floodwait", f"Wait {e.value}s"
    except RPCError as e:
        return "error", str(e)
    except Exception as e:
        return "fail", f"Unexpected: {e}"

async def run_reporting_flow(state: ConversationState, client: Client, chat_id: int):
    sessions = load_session_strings(state.report.session_limit or 0)
    success, fail = 0, 0
    for name, sess in sessions:
        while state.paused:
            await asyncio.sleep(1)
        status, detail = await evaluate_session(
            name, sess,
            state.target.group_link or "",
            state.target.chat_identifier or "",
            state.target.message_id or 0,
            reason=resolve_reason_class(state.report.report_reason_key),
            report_text=state.report.report_text
        )
        if status == "reachable":
            success += 1
        else:
            fail += 1
        print(f"{name}: {status} - {detail}")
        if state.live_panel and state.live_panel_chat:
            try:
                await client.edit_message_text(
                    state.live_panel_chat,
                    state.live_panel,
                    f"✅ Success: {success} | ❌ Failed: {fail}"
                )
            except RPCError:
                pass

async def main():
    app = Client("mod_tool", api_id=API_ID, api_hash=API_HASH, session_string=PRIMARY_SESSION)

    @app.on_message(filters.command("start"))
    async def start(_, msg):
        if not has_power(msg.from_user.id):
            return await msg.reply("❌ Unauthorized.")
        await msg.reply("✅ Bot is running. Use /run to launch report.")

    @app.on_message(filters.command("run"))
    async def run(client, msg):
        if not has_power(msg.from_user.id):
            return await msg.reply("Unauthorized")
        parts = msg.text.split()
        if len(parts) != 5:
            return await msg.reply("Usage: /run <group_link> <msg_link> <sessions> <count>")
        _, group_link, message_link, sess_raw, count_raw = parts
        try:
            sess_count = int(sess_raw)
            rep_count = int(count_raw)
        except ValueError:
            return await msg.reply("Invalid numbers")

        chat_id, msg_id = parse_link(message_link)
        if chat_id is None or msg_id is None:
            return await msg.reply("❌ Invalid message link")

        state = ConversationState()
        state.target = TargetContext(
            group_link=group_link,
            message_link=message_link,
            chat_identifier=chat_id,
            message_id=msg_id
        )
        state.report.session_limit = sess_count
        state.report.report_total = rep_count

        sent = await msg.reply("Launching report...")
        state.live_panel = sent.id
        state.live_panel_chat = msg.chat.id
        await run_reporting_flow(state, client, msg.chat.id)

    await app.start()
    print("Bot started.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
