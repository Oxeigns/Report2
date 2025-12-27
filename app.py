import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from pyrogram import Client, filters, utils
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
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

# ----------------------
# Configuration & State Management
# ----------------------
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
    last_panel_text: str = ""

USER_STATES: Dict[int, ConversationState] = {}

def load_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        return {"API_ID": None, "API_HASH": "", "PRIMARY_SESSION": "", "OWNER_ID": None}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def parse_int(value: Optional[Union[str, int]]) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0

CONFIG = load_config()
API_ID = parse_int(os.getenv("API_ID") or CONFIG.get("API_ID"))
API_HASH = os.getenv("API_HASH") or CONFIG.get("API_HASH", "")
OWNER_ID = parse_int(os.getenv("OWNER_ID") or CONFIG.get("OWNER_ID"))
PRIMARY_SESSION = os.getenv("PRIMARY_SESSION") or CONFIG.get("PRIMARY_SESSION", "")

def load_state() -> Dict:
    if not os.path.exists(STATE_PATH):
        return {"target": {}, "report": {"reason": "other", "text": "Violating content"}, "sudo_user_ids": []}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

STATE_DATA = load_state()

def save_state(state: Dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# ----------------------
# Logic & Fixed Peer Resolution
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

def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    link = link.strip()
    m_username = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)$", link)
    if m_username: return m_username.group(1), int(m_username.group(2))
    m_c = re.match(r"^https?://t\.me/c/([0-9]+)/([0-9]+)$", link)
    if m_c: return int(f"-100{m_c.group(1)}"), int(m_c.group(2))
    return None, None

async def force_resolve_peer(client: Client, chat_id: Union[str, int]):
    """Attempts to resolve peer, joins if necessary to fix Peer ID Invalid error."""
    try:
        return await client.resolve_peer(chat_id)
    except (KeyError, ValueError):
        try:
            # If it's a username or link, get_chat will cache it
            chat = await client.get_chat(chat_id)
            return await client.resolve_peer(chat.id)
        except Exception:
            return None

def load_session_strings(limit: int = 0) -> List[Tuple[str, str]]:
    sessions = [("primary", PRIMARY_SESSION)]
    if os.path.isdir(SESSIONS_DIR):
        for filename in sorted(os.listdir(SESSIONS_DIR)):
            if filename.endswith(".session"):
                with open(os.path.join(SESSIONS_DIR, filename), "r") as f:
                    content = f.read().strip()
                    if content: sessions.append((filename, content))
    return sessions[:limit] if limit > 0 else sessions

async def run_reporting_flow(state: ConversationState, chat_id: int, client: Client):
    sessions = load_session_strings(state.report.session_limit)
    reason_class = REASON_MAP.get(state.report.report_reason_key, types.InputReportReasonOther)()
    
    header = f"🚀 **Live Reporting Dashboard**\nTarget: {state.target.message_link}\nReason: {state.report.report_reason_key}"
    panel = await client.send_message(chat_id, header, 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏸ Pause", callback_data="toggle_pause")]]))
    
    state.live_panel = panel.id
    success, fail = 0, 0
    
    for name, s_str in sessions:
        while state.paused: await asyncio.sleep(1)
        try:
            async with Client(name, API_ID, API_HASH, session_string=s_str, no_updates=True) as worker:
                # First ensure we are in the chat
                try:
                    await worker.join_chat(state.target.group_link)
                except UserAlreadyParticipant:
                    pass
                
                peer = await force_resolve_peer(worker, state.target.chat_identifier)
                if peer:
                    await worker.invoke(functions.messages.Report(
                        peer=peer, 
                        id=[state.target.message_id], 
                        reason=reason_class, 
                        message=state.report.report_text
                    ))
                    success += 1
                else: fail += 1
        except Exception: fail += 1
        
        # Dashboard Update
        try:
            status = "⏸ Paused" if state.paused else "▶️ Running"
            await client.edit_message_text(chat_id, panel.id, f"{header}\n\n✅ Success: {success}\n❌ Fail: {fail}\nStatus: {status}", 
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Resume" if state.paused else "⏸ Pause", callback_data="toggle_pause")]]))
        except: pass

    await client.send_message(chat_id, f"🏁 **Task Finished**\nSuccessful: {success} | Failed: {fail}")

# ----------------------
# Bot Event Handlers
# ----------------------
async def main():
    if not API_ID or not PRIMARY_SESSION:
        print("CRITICAL: API_ID and PRIMARY_SESSION are required.")
        return

    app = Client("moderator_tool", api_id=API_ID, api_hash=API_HASH, session_string=PRIMARY_SESSION)

    def get_state(uid) -> ConversationState:
        if uid not in USER_STATES:
            USER_STATES[uid] = ConversationState()
            USER_STATES[uid].report.report_reason_key = STATE_DATA["report"].get("reason", "other")
            USER_STATES[uid].report.report_text = STATE_DATA["report"].get("text", "Spam content")
        return USER_STATES[uid]

    def is_auth(uid):
        return uid == OWNER_ID or uid in STATE_DATA.get("sudo_user_ids", [])

    @app.on_message(filters.command("start") & filters.private)
    async def _start(_, msg):
        if not is_auth(msg.from_user.id): return
        kb = [
            [InlineKeyboardButton("🎯 Set Target", callback_data="set_tar"), InlineKeyboardButton("🚀 Launch", callback_data="launch")],
            [InlineKeyboardButton("➕ Add Session", callback_data="add_sess"), InlineKeyboardButton("🛡️ Manage Sudo", callback_data="manage_sudo")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="config")]
        ]
        await msg.reply_text("🛰 **Moderator Tool Controller**\nChoose an action:", reply_markup=InlineKeyboardMarkup(kb))

    @app.on_callback_query()
    async def _callbacks(client: Client, cq: CallbackQuery):
        uid = cq.from_user.id
        if not is_auth(uid): return
        state = get_state(uid)

        if cq.data == "set_tar":
            state.mode = "await_group"
            await cq.edit_message_text("1/2: Send the Group/Channel Invite Link:")
        elif cq.data == "launch":
            if not state.target.message_id: return await cq.answer("❌ Set target links first!", show_alert=True)
            asyncio.create_task(run_reporting_flow(state, cq.message.chat.id, client))
        elif cq.data == "toggle_pause":
            state.paused = not state.paused
            await cq.answer("Paused" if state.paused else "Resumed")
        elif cq.data == "add_sess":
            state.mode = "await_sess_name"
            await cq.edit_message_text("Enter name for this session file (e.g. acc1):")
        elif cq.data == "manage_sudo":
            if uid != OWNER_ID: return await cq.answer("Owner Only", show_alert=True)
            state.mode = "add_sudo"
            await cq.edit_message_text(f"Current Sudos: `{STATE_DATA['sudo_user_ids']}`\n\nSend a User ID to add to Sudo.")
        elif cq.data == "config":
            btns = [[InlineKeyboardButton(k, callback_data=f"rsn:{k}")] for k in REASON_MAP.keys()]
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="home")])
            await cq.edit_message_text("Select Reporting Reason:", reply_markup=InlineKeyboardMarkup(btns))
        elif cq.data.startswith("rsn:"):
            state.report.report_reason_key = cq.data.split(":")[1]
            STATE_DATA["report"]["reason"] = state.report.report_reason_key
            save_state(STATE_DATA)
            await cq.answer(f"Reason: {state.report.report_reason_key}")
        elif cq.data == "home":
            await _start(client, cq.message)

    @app.on_message(filters.private & ~filters.command("start"))
    async def _input(client: Client, msg):
        uid = msg.from_user.id
        if not is_auth(uid): return
        state = get_state(uid)

        if state.mode == "await_group":
            state.target.group_link = msg.text.strip()
            state.mode = "await_msg"
            await msg.reply("Group Link saved. Now send the Message Link:")
        elif state.mode == "await_msg":
            cid, mid = parse_link(msg.text.strip())
            if cid:
                state.target.message_link, state.target.chat_identifier, state.target.message_id = msg.text.strip(), cid, mid
                state.mode = "idle"
                await msg.reply(f"✅ Target Locked: {cid} / {mid}")
            else: await msg.reply("❌ Invalid format. Use https://t.me/c/xxx/yyy")
        elif state.mode == "await_sess_name":
            state.pending_session_name = msg.text.strip()
            state.mode = "await_sess_val"
            await msg.reply("Paste the session string:")
        elif state.mode == "await_sess_val":
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            with open(os.path.join(SESSIONS_DIR, f"{state.pending_session_name}.session"), "w") as f:
                f.write(msg.text.strip())
            state.mode = "idle"
            await msg.reply(f"✅ Session `{state.pending_session_name}` saved.")
        elif state.mode == "add_sudo":
            try:
                sid = int(msg.text.strip())
                if sid not in STATE_DATA["sudo_user_ids"]:
                    STATE_DATA["sudo_user_ids"].append(sid)
                    save_state(STATE_DATA)
                await msg.reply(f"✅ Added {sid} to Sudo.")
                state.mode = "idle"
            except: await msg.reply("Send a numeric ID.")

    print("--- Moderator Bot Starting ---")
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
