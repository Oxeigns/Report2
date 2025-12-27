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
    [span_2](start_span)validation_notes: List[str] = field(default_factory=list)[span_2](end_span)

@dataclass
class ReportSettings:
    report_type: str = "standard"
    report_reason_key: str = "other"
    report_text: str = ""
    report_total: Optional[int] = None
    [span_3](start_span)session_limit: int = 0[span_3](end_span)

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
    [span_4](start_span)last_panel_text: str = ""[span_4](end_span)

USER_STATES: Dict[int, ConversationState] = {}

def load_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        return {"API_ID": None, "API_HASH": "", "PRIMARY_SESSION": "", "LOG_GROUP_LINK": "", "OWNER_ID": None}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        [span_5](start_span)return json.load(f)[span_5](end_span)

def parse_int(value: Optional[Union[str, int]]) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        [span_6](start_span)return 0[span_6](end_span)

CONFIG = load_config()
API_ID = parse_int(os.getenv("API_ID") or CONFIG.get("API_ID"))
API_HASH = os.getenv("API_HASH") or CONFIG.get("API_HASH", "")
OWNER_ID = parse_int(os.getenv("OWNER_ID") or CONFIG.get("OWNER_ID"))
PRIMARY_SESSION = os.getenv("PRIMARY_SESSION") or CONFIG.get("PRIMARY_SESSION", "")
[span_7](start_span)LOG_GROUP_LINK = os.getenv("LOG_GROUP_LINK") or CONFIG.get("LOG_GROUP_LINK", "")[span_7](end_span)

def load_state() -> Dict:
    if not os.path.exists(STATE_PATH):
        return {"target": {}, "report": {"reason": "other", "text": ""}, "log_group_id": None, "sudo_user_ids": []}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        [span_8](start_span)return json.load(f)[span_8](end_span)

STATE_DATA = load_state()

def save_state(state: Dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        [span_9](start_span)json.dump(state, f, indent=2)[span_9](end_span)

# ----------------------
# Logic & Helper Functions
# ----------------------

# FIXED: Removed specific import that caused error and used types directly
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
[span_10](start_span)}

def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    link = link.strip()
    m_username = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)$", link)
    if m_username: return m_username.group(1), int(m_username.group(2))
    m_c = re.match(r"^https?://t\.me/c/([0-9]+)/([0-9]+)$", link)
    if m_c: return int(f"-100{m_c.group(1)}"), int(m_c.group(2))
    return None, None[span_10](end_span)

def load_session_strings(max_count: int) -> List[Tuple[str, str]]:
    sessions = [("primary", PRIMARY_SESSION)]
    if os.path.isdir(SESSIONS_DIR):
        for filename in sorted(os.listdir(SESSIONS_DIR)):
            path = os.path.join(SESSIONS_DIR, filename)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content: sessions.append((filename, content))
    [span_11](start_span)return sessions[:max_count] if max_count > 0 else sessions[span_11](end_span)

async def run_reporting_flow(state: ConversationState, chat_id: int, client: Client):
    sessions = load_session_strings(state.report.session_limit)
    [span_12](start_span)reason_class = REASON_MAP.get(state.report.report_reason_key, types.InputReportReasonOther)()[span_12](end_span)
    
    header = f"🚀 **Live Reporting Panel**\nTarget: {state.target.message_link}\nReason: {state.report.report_reason_key}"
    panel = await client.send_message(chat_id, header, 
                                      [span_13](start_span)reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏸ Pause", callback_data="toggle_pause")]]))[span_13](end_span)
    
    state.live_panel = panel.id
    success, fail = 0, 0
    
    for name, s_str in sessions:
        while state.paused: await asyncio.sleep(1)
        try:
            async with Client(name, API_ID, API_HASH, session_string=s_str, no_updates=True) as worker:
                chat = await worker.join_chat(state.target.group_link)
                peer = await worker.resolve_peer(chat.id)
                await worker.invoke(functions.messages.Report(peer=peer, id=[state.target.message_id], 
                                                              reason=reason_class, message=state.report.report_text))
                success += 1
        [span_14](start_span)except Exception: fail += 1[span_14](end_span)
        
        # Real-time dashboard update
        try:
            await client.edit_message_text(chat_id, panel.id, f"{header}\n\n✅ Success: {success}\n❌ Fail: {fail}\nStatus: {'Paused' if state.paused else 'Running'}", 
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Resume" if state.paused else "⏸ Pause", callback_data="toggle_pause")]]))
        [span_15](start_span)except: pass[span_15](end_span)

    [span_16](start_span)await client.send_message(chat_id, f"🏁 **Reporting Finished!**\nTotal Successful: {success}\nTotal Failed: {fail}")[span_16](end_span)

# ----------------------
# Bot Event Handlers
# ----------------------
async def main():
    if not API_ID or not PRIMARY_SESSION:
        print("CRITICAL: API_ID and PRIMARY_SESSION must be set via app.json variables or config.json")
        [span_17](start_span)return[span_17](end_span)

    app = Client("moderator_tool", api_id=API_ID, api_hash=API_HASH, session_string=PRIMARY_SESSION)

    def get_user_state(uid) -> ConversationState:
        if uid not in USER_STATES:
            USER_STATES[uid] = ConversationState()
            USER_STATES[uid].report.report_reason_key = STATE_DATA["report"].get("reason", "other")
            USER_STATES[uid].report.report_text = STATE_DATA["report"].get("text", "")
        [span_18](start_span)return USER_STATES[uid][span_18](end_span)

    def is_auth(uid):
        [span_19](start_span)return uid == OWNER_ID or uid in STATE_DATA.get("sudo_user_ids", [])[span_19](end_span)

    @app.on_message(filters.command("start") & filters.private)
    async def _start(_, msg):
        if not is_auth(msg.from_user.id): return
        kb = [[InlineKeyboardButton("➕ Add Session", callback_data="add_sess"), InlineKeyboardButton("🎯 Set Target", callback_data="set_tar")],
              [InlineKeyboardButton("⚙️ Settings", callback_data="config"), InlineKeyboardButton("🚀 Launch", callback_data="launch")],
              [InlineKeyboardButton("🛡️ Sudo Management", callback_data="manage_sudo")]]
        [span_20](start_span)await msg.reply_text("🛰 **Moderator Tool Controller**\nGuided system active:", reply_markup=InlineKeyboardMarkup(kb))[span_20](end_span)

    @app.on_callback_query()
    async def _callbacks(client: Client, cq: CallbackQuery):
        uid = cq.from_user.id
        if not is_auth(uid): return
        state = get_user_state(uid)

        if cq.data == "set_tar":
            state.mode = "await_group"
            [span_21](start_span)await cq.edit_message_text("Step 1: Send the Group/Channel Invite Link:")[span_21](end_span)
        elif cq.data == "toggle_pause":
            state.paused = not state.paused
            [span_22](start_span)await cq.answer("Process " + ("Paused" if state.paused else "Resumed"))[span_22](end_span)
        elif cq.data == "launch":
            if not state.target.message_id: return await cq.answer("❌ Set target links first!", show_alert=True)
            [span_23](start_span)asyncio.create_task(run_reporting_flow(state, cq.message.chat.id, client))[span_23](end_span)
        elif cq.data == "add_sess":
            state.mode = "await_sess_name"
            [span_24](start_span)await cq.edit_message_text("Enter name for this session file (no spaces):")[span_24](end_span)
        elif cq.data == "manage_sudo":
            if uid != OWNER_ID: return await cq.answer("Only Owner can manage sudo list.", show_alert=True)
            state.mode = "add_sudo"
            [span_25](start_span)await cq.edit_message_text(f"Current Sudo IDs: `{STATE_DATA['sudo_user_ids']}`\n\nSend a User ID to add to Sudo.")[span_25](end_span)

    @app.on_message(filters.private & ~filters.command("start"))
    async def _text_input(client: Client, msg):
        uid = msg.from_user.id
        if not is_auth(uid): return
        state = get_user_state(uid)

        if state.mode == "await_group":
            state.target.group_link = msg.text.strip()
            state.mode = "await_msg"
            [span_26](start_span)await msg.reply("Group link saved. Now send the Message Link (e.g. https://t.me/c/123/45):")[span_26](end_span)
        elif state.mode == "await_msg":
            cid, mid = parse_link(msg.text.strip())
            if cid:
                state.target.message_link, state.target.chat_identifier, state.target.message_id = msg.text.strip(), cid, mid
                state.mode = "idle"
                [span_27](start_span)await msg.reply(f"✅ Target Locked.\nID: `{cid}`\nMsg: `{mid}`")[span_27](end_span)
            [span_28](start_span)else: await msg.reply("❌ Invalid format. Use a proper message link.")[span_28](end_span)
        elif state.mode == "await_sess_name":
            state.pending_session_name = msg.text.strip()
            state.mode = "await_sess_val"
            [span_29](start_span)await msg.reply(f"Paste the session string for `{state.pending_session_name}`:")[span_29](end_span)
        elif state.mode == "await_sess_val":
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            with open(os.path.join(SESSIONS_DIR, f"{state.pending_session_name}.session"), "w") as f:
                f.write(msg.text.strip())
            state.mode = "idle"
            [span_30](start_span)await msg.reply(f"✅ Session `{state.pending_session_name}` saved.")[span_30](end_span)
        elif state.mode == "add_sudo":
            try:
                sid = int(msg.text.strip())
                if sid not in STATE_DATA["sudo_user_ids"]:
                    STATE_DATA["sudo_user_ids"].append(sid)
                    save_state(STATE_DATA)
                await msg.reply(f"✅ User `{sid}` added to Sudo roster.")
                state.mode = "idle"
            [span_31](start_span)except: await msg.reply("❌ Invalid ID format.")[span_31](end_span)

    print("--- Moderator Bot is Starting ---")
    await app.start()
    [span_32](start_span)await asyncio.Event().wait()[span_32](end_span)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        [span_33](start_span)pass[span_33](end_span)
