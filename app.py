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
from pyrogram.raw.base import InputReportReason
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

# ----------------------
# Constants & Paths
# ----------------------
CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
SESSIONS_DIR = "sessions"

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# ----------------------
# Data Models
# ----------------------
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

# ----------------------
# Configuration Helpers
# ----------------------
def load_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        # Create a template if it doesn't exist
        template = {"API_ID": 0, "API_HASH": "", "PRIMARY_SESSION": "", "LOG_GROUP_LINK": "", "OWNER_ID": 0}
        with open(CONFIG_PATH, "w") as f: json.dump(template, f, indent=2)
        raise FileNotFoundError("Missing config.json. A template has been created.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: Dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

def load_state() -> Dict:
    default_state = {
        "target": {"group_link": "", "message_link": "", "chat_identifier": None, "message_id": None},
        "report": {"type": "standard", "reason": "other", "text": "", "total": None, "session_limit": 0},
        "log_group_id": None,
        "sudo_user_ids": [],
    }
    if not os.path.exists(STATE_PATH): return default_state
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state: Dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# Global Data Initialization
CONFIG = load_config()
STATE_DATA = load_state()
API_ID = int(os.getenv("API_ID") or CONFIG.get("API_ID"))
API_HASH = os.getenv("API_HASH") or CONFIG.get("API_HASH", "")
OWNER_ID = int(os.getenv("OWNER_ID") or CONFIG.get("OWNER_ID"))
PRIMARY_SESSION = CONFIG.get("PRIMARY_SESSION") or os.getenv("PRIMARY_SESSION", "")
LOG_GROUP_LINK = CONFIG.get("LOG_GROUP_LINK", "")

# ----------------------
# Core Logic Helpers
# ----------------------
REASON_MAP = {
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

def resolve_reason_class(key: str) -> InputReportReason:
    return REASON_MAP.get(key.strip().lower(), types.InputReportReasonOther)()

REPORT_REASON = resolve_reason_class(STATE_DATA["report"].get("reason", "other"))
REPORT_TEXT = STATE_DATA["report"].get("text", "")

def persist_report_settings(state: ConversationState) -> None:
    STATE_DATA["report"] = {
        "type": state.report.report_type,
        "reason": state.report.report_reason_key,
        "text": state.report.report_text,
        "total": state.report.report_total,
        "session_limit": state.report.session_limit,
    }
    save_state(STATE_DATA)

def persist_target(state: ConversationState) -> None:
    STATE_DATA["target"].update({
        "group_link": state.target.group_link or "",
        "message_link": state.target.message_link or "",
        "chat_identifier": state.target.chat_identifier,
        "message_id": state.target.message_id,
    })
    save_state(STATE_DATA)

def persist_sudo_users(users: List[int]) -> None:
    STATE_DATA["sudo_user_ids"] = sorted(set(users))
    save_state(STATE_DATA)

def get_state(user_id: int) -> ConversationState:
    if user_id not in USER_STATES:
        USER_STATES[user_id] = ConversationState()
        # Pre-load settings from persistent state
        USER_STATES[user_id].report.report_text = STATE_DATA["report"].get("text", "")
        USER_STATES[user_id].report.report_reason_key = STATE_DATA["report"].get("reason", "other")
        USER_STATES[user_id].report.report_total = STATE_DATA["report"].get("total")
    return USER_STATES[user_id]

# ----------------------
# URL & Pyrogram Utils
# ----------------------
def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    link = link.strip()
    m_username = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)$", link)
    if m_username: return m_username.group(1), int(m_username.group(2))
    m_c = re.match(r"^https?://t\.me/c/([0-9]+)/([0-9]+)$", link)
    if m_c: return int(f"-100{m_c.group(1)}"), int(m_c.group(2))
    return None, None

def is_valid_group_link(link: str) -> bool:
    return bool(re.match(r"^https?://t\.me/(\+|joinchat/)?[A-Za-z0-9_-]+$", link.strip()))

def load_session_strings(max_count: int) -> List[Tuple[str, str]]:
    sessions = []
    if os.path.isdir(SESSIONS_DIR):
        for filename in sorted(os.listdir(SESSIONS_DIR)):
            path = os.path.join(SESSIONS_DIR, filename)
            with open(path, "r") as f:
                content = f.read().strip()
                if content: sessions.append((filename, content))
    return sessions[:max_count] if max_count > 0 else sessions

async def join_target_chat(client: Client, join_link: str, chat_identifier: Union[str, int]):
    try:
        chat = await client.join_chat(join_link)
        return await client.resolve_peer(chat.id), "✅ Joined"
    except UserAlreadyParticipant:
        return await client.resolve_peer(chat_identifier), "ℹ️ Already in"
    except Exception as e:
        return None, f"❌ {str(e)}"

async def evaluate_session(session_name, session_str, join_link, target, msg_id, reason=None, report_text=None):
    try:
        async with Client(name=f"run_{session_name}", api_id=API_ID, api_hash=API_HASH, session_string=session_str, no_updates=True) as c:
            peer, status = await join_target_chat(c, join_link, target)
            if not peer: return "failed", status
            await c.invoke(functions.messages.Report(peer=peer, id=[msg_id], reason=reason or REPORT_REASON, message=report_text or REPORT_TEXT))
            return "reachable", "Report Sent"
    except Exception as e:
        return "error", str(e)

async def validate_target_with_sessions(group_link, message_link, limit):
    chat_id, msg_id = parse_link(message_link)
    sessions = load_session_strings(limit)
    if not sessions: return None, ["No sessions found."]
    # Logic to check one session to see if message is visible
    name, string = sessions[0]
    async with Client(name="validator", api_id=API_ID, api_hash=API_HASH, session_string=string, no_updates=True) as c:
        peer, status = await join_target_chat(c, group_link, chat_id)
        if not peer: return None, [status]
        msg = await c.get_messages(chat_id, msg_id)
        if msg.empty: return None, ["Message not found."]
        target = TargetContext(group_link=group_link, message_link=message_link, chat_identifier=chat_id, message_id=msg_id, chat_title=msg.chat.title, active_sessions=len(sessions))
        return target, ["Target Validated"]

# ----------------------
# Keyboards
# ----------------------
def start_keyboard(is_owner: bool) -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton("➕ Add Sessions", callback_data="add_sessions")],
        [InlineKeyboardButton("🎯 Set Target", callback_data="setup_target")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="configure")],
        [InlineKeyboardButton("🚀 Start", callback_data="begin_report")]
    ]
    if is_owner: btns.append([InlineKeyboardButton("🛡 Sudo", callback_data="manage_sudo")])
    return InlineKeyboardMarkup(btns)

def configuration_keyboard(state: ConversationState) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reason", callback_data="choose_type"), InlineKeyboardButton("📝 Text", callback_data="change_text")],
        [InlineKeyboardButton("#️⃣ Total", callback_data="change_total")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_home")]
    ])

def live_panel_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Resume" if paused else "⏸ Pause", callback_data="toggle_pause")],
        [InlineKeyboardButton("⬅️ Stop & Home", callback_data="back_home")]
    ])

# ----------------------
# Permission Checks
# ----------------------
def has_power(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in STATE_DATA.get("sudo_user_ids", [])

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

# ----------------------
# Main Application
# ----------------------
async def main():
    app = Client("moderator_tool", api_id=API_ID, api_hash=API_HASH, session_string=PRIMARY_SESSION)

    @app.on_message(filters.command("start") & filters.private)
    async def _start(_, msg):
        if not has_power(msg.from_user.id): return
        get_state(msg.from_user.id).mode = "idle"
        await msg.reply_text("Control Panel:", reply_markup=start_keyboard(is_owner(msg.from_user.id)))

    @app.on_callback_query()
    async def _callbacks(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        if not has_power(user_id): return
        state = get_state(user_id)
        data = cq.data

        if data == "back_home":
            state.mode = "idle"
            await cq.edit_message_text("Home:", reply_markup=start_keyboard(is_owner(user_id)))
        elif data == "add_sessions":
            state.mode = "awaiting_session_name"
            await cq.message.reply_text("Enter a name for this session:")
        elif data == "setup_target":
            state.mode = "awaiting_group_link"
            await cq.message.reply_text("Send the Group/Channel Link:")
        elif data == "configure":
            await cq.edit_message_text("Settings:", reply_markup=configuration_keyboard(state))
        elif data == "change_text":
            state.mode = "awaiting_report_text"
            await cq.message.reply_text("Send the report message text:")
        elif data == "change_total":
            state.mode = "awaiting_report_total"
            await cq.message.reply_text("How many reports total?")
        elif data == "begin_report":
            if not state.target.message_id: 
                await cq.answer("Setup target first!", show_alert=True)
                return
            await cq.message.reply_text("Starting reporting sequence...")
            # Async task for reporting flow
            asyncio.create_task(run_reporting_flow(state, cq.message.chat.id, client))
        elif data == "toggle_pause":
            state.paused = not state.paused
            await cq.answer("Paused" if state.paused else "Resumed")
        await cq.answer()

    async def run_reporting_flow(state, chat_id, client):
        sessions = load_session_strings(state.report.session_limit)
        success, fail = 0, 0
        panel = await client.send_message(chat_id, "Reporting Started...", reply_markup=live_panel_keyboard(state.paused))
        
        for name, string in sessions:
            while state.paused: await asyncio.sleep(1)
            status, detail = await evaluate_session(name, string, state.target.group_link, state.target.chat_identifier, state.target.message_id)
            if status == "reachable": success += 1
            else: fail += 1
            await panel.edit_text(f"Progress:\n✅ Success: {success}\n❌ Failed: {fail}\nLast: {name} ({detail})", reply_markup=live_panel_keyboard(state.paused))
        await panel.edit_text(f"Done!\nTotal Success: {success}")

    @app.on_message(filters.private & ~filters.command(["start"]))
    async def _stateful(client, msg):
        user_id = msg.from_user.id
        if not has_power(user_id): return
        state = get_state(user_id)

        if state.mode == "awaiting_session_name":
            state.pending_session_name = msg.text.strip()
            state.mode = "awaiting_session_value"
            await msg.reply_text(f"Now send the session string for {state.pending_session_name}:")
        
        elif state.mode == "awaiting_session_value":
            with open(os.path.join(SESSIONS_DIR, f"{state.pending_session_name}.session"), "w") as f:
                f.write(msg.text.strip())
            state.mode = "idle"
            await msg.reply_text("✅ Session saved.", reply_markup=start_keyboard(is_owner(user_id)))

        elif state.mode == "awaiting_group_link":
            if not is_valid_group_link(msg.text): return await msg.reply_text("Invalid Link.")
            state.target.group_link = msg.text.strip()
            state.mode = "awaiting_message_link"
            await msg.reply_text("Now send the target Message Link:")

        elif state.mode == "awaiting_message_link":
            target, notes = await validate_target_with_sessions(state.target.group_link, msg.text.strip(), 0)
            if target:
                state.target = target
                persist_target(state)
                state.mode = "idle"
                await msg.reply_text(f"🎯 Target Set: {target.chat_title}\n{target.message_link}", reply_markup=start_keyboard(is_owner(user_id)))
            else:
                await msg.reply_text(f"Validation Failed: {notes[0]}")

        elif state.mode == "awaiting_report_text":
            global REPORT_TEXT
            REPORT_TEXT = msg.text.strip()
            state.report.report_text = REPORT_TEXT
            persist_report_settings(state)
            state.mode = "idle"
            await msg.reply_text("✅ Text Updated.", reply_markup=configuration_keyboard(state))

        elif state.mode == "awaiting_report_total":
            try:
                state.report.report_total = int(msg.text)
                persist_report_settings(state)
                state.mode = "idle"
                await msg.reply_text(f"✅ Total set to {state.report.report_total}", reply_markup=configuration_keyboard(state))
            except:
                await msg.reply_text("Please enter a valid number.")

    await app.start()
    print("Bot is live.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
