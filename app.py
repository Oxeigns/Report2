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

# File paths for persistence
CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
SESSIONS_DIR = "sessions"

# --- Configuration & Environment Setup ---
def parse_int(value: Optional[Union[str, int]]) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0

# Load Deployment Variables (Priority: Environment > Config File)
def load_config() -> Dict:
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            [span_5](start_span)config = json.load(f)[span_5](end_span)
    
    return {
        [span_6](start_span)[span_7](start_span)"API_ID": parse_int(os.getenv("API_ID") or config.get("API_ID")),[span_6](end_span)[span_7](end_span)
        [span_8](start_span)[span_9](start_span)"API_HASH": os.getenv("API_HASH") or config.get("API_HASH", ""),[span_8](end_span)[span_9](end_span)
        [span_10](start_span)[span_11](start_span)"PRIMARY_SESSION": os.getenv("PRIMARY_SESSION") or config.get("PRIMARY_SESSION", ""),[span_10](end_span)[span_11](end_span)
        [span_12](start_span)[span_13](start_span)"OWNER_ID": parse_int(os.getenv("OWNER_ID") or config.get("OWNER_ID")),[span_12](end_span)[span_13](end_span)
        [span_14](start_span)[span_15](start_span)"LOG_GROUP_LINK": os.getenv("LOG_GROUP_LINK") or config.get("LOG_GROUP_LINK", "")[span_14](end_span)[span_15](end_span)
    }

[span_16](start_span)GLOBAL_CONFIG = load_config()[span_16](end_span)
[span_17](start_span)API_ID = GLOBAL_CONFIG["API_ID"][span_17](end_span)
[span_18](start_span)API_HASH = GLOBAL_CONFIG["API_HASH"][span_18](end_span)
[span_19](start_span)PRIMARY_SESSION = GLOBAL_CONFIG["PRIMARY_SESSION"][span_19](end_span)
[span_20](start_span)OWNER_ID = GLOBAL_CONFIG["OWNER_ID"][span_20](end_span)
[span_21](start_span)LOG_GROUP_LINK = GLOBAL_CONFIG["LOG_GROUP_LINK"][span_21](end_span)

# --- State Management ---
@dataclass
class TargetContext:
    [span_22](start_span)group_link: Optional[str] = None[span_22](end_span)
    [span_23](start_span)message_link: Optional[str] = None[span_23](end_span)
    [span_24](start_span)chat_identifier: Optional[Union[str, int]] = None[span_24](end_span)
    [span_25](start_span)message_id: Optional[int] = None[span_25](end_span)
    [span_26](start_span)chat_title: Optional[str] = None[span_26](end_span)
    [span_27](start_span)message_preview: Optional[str] = None[span_27](end_span)
    [span_28](start_span)active_sessions: int = 0[span_28](end_span)
    [span_29](start_span)validation_notes: List[str] = field(default_factory=list)[span_29](end_span)

@dataclass
class ConversationState:
    [span_30](start_span)mode: str = "idle"[span_30](end_span)
    [span_31](start_span)target: TargetContext = field(default_factory=TargetContext)[span_31](end_span)
    [span_32](start_span)report_reason_key: str = "other"[span_32](end_span)
    [span_33](start_span)report_text: str = ""[span_33](end_span)
    [span_34](start_span)paused: bool = False[span_34](end_span)
    [span_35](start_span)live_panel: Optional[int] = None[span_35](end_span)
    [span_36](start_span)live_panel_chat: Optional[int] = None[span_36](end_span)

[span_37](start_span)USER_STATES: Dict[int, ConversationState] = {}[span_37](end_span)

# --- Helper Functions ---
def load_state() -> Dict:
    if not os.path.exists(STATE_PATH):
        [span_38](start_span)return {"sudo_user_ids": [], "report": {"reason": "other", "text": "Violating content"}}[span_38](end_span)
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        [span_39](start_span)return json.load(f)[span_39](end_span)

def save_state(state: Dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        [span_40](start_span)json.dump(state, f, indent=2)[span_40](end_span)

[span_41](start_span)STATE_DATA = load_state()[span_41](end_span)

def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    [span_42](start_span)link = link.strip()[span_42](end_span)
    [span_43](start_span)m_username = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)$", link)[span_43](end_span)
    [span_44](start_span)if m_username: return m_username.group(1), int(m_username.group(2))[span_44](end_span)
    [span_45](start_span)m_c = re.match(r"^https?://t\.me/c/([0-9]+)/([0-9]+)$", link)[span_45](end_span)
    [span_46](start_span)if m_c: return int(f"-100{m_c.group(1)}"), int(m_c.group(2))[span_46](end_span)
    [span_47](start_span)return None, None[span_47](end_span)

# --- Reporting Logic ---
REASON_MAP = {
    [span_48](start_span)"child_abuse": types.InputReportReasonChildAbuse,[span_48](end_span)
    [span_49](start_span)"violence": types.InputReportReasonViolence,[span_49](end_span)
    [span_50](start_span)"scam": types.InputReportReasonSpam,[span_50](end_span)
    [span_51](start_span)"spam": types.InputReportReasonSpam,[span_51](end_span)
    [span_52](start_span)"other": types.InputReportReasonOther,[span_52](end_span)
}

async def run_reporting_flow(state: ConversationState, chat_id: int, client: Client):
    # Load worker sessions
    [span_53](start_span)sessions = [("primary", PRIMARY_SESSION)][span_53](end_span)
    if os.path.isdir(SESSIONS_DIR):
        [span_54](start_span)for f in os.listdir(SESSIONS_DIR):[span_54](end_span)
            if f.endswith(".session"):
                with open(os.path.join(SESSIONS_DIR, f), "r") as s:
                    [span_55](start_span)sessions.append((f, s.read().strip()))[span_55](end_span)

    [span_56](start_span)reason = REASON_MAP.get(state.report_reason_key, types.InputReportReasonOther)()[span_56](end_span)
    [span_57](start_span)header = f"🚀 **Reporting Target**\n`{state.target.message_link}`"[span_57](end_span)
    panel = await client.send_message(chat_id, header + "\nStatus: Initializing...", 
                                      [span_58](start_span)reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏸ Pause", callback_data="toggle_pause")]]))[span_58](end_span)
    
    [span_59](start_span)success, fail = 0, 0[span_59](end_span)
    for name, s_string in sessions:
        [span_60](start_span)while state.paused: await asyncio.sleep(1)[span_60](end_span)
        try:
            [span_61](start_span)async with Client(name, API_ID, API_HASH, session_string=s_string, no_updates=True) as worker:[span_61](end_span)
                [span_62](start_span)chat = await worker.join_chat(state.target.group_link)[span_62](end_span)
                [span_63](start_span)peer = await worker.resolve_peer(chat.id)[span_63](end_span)
                [span_64](start_span)await worker.invoke(functions.messages.Report(peer=peer, id=[state.target.message_id], reason=reason, message=state.report_text))[span_64](end_span)
                [span_65](start_span)success += 1[span_65](end_span)
        [span_66](start_span)except Exception: fail += 1[span_66](end_span)
        
        await client.edit_message_text(chat_id, panel.id, f"{header}\n\n✅ Success: {success}\n❌ Fail: {fail}", 
                                      [span_67](start_span)reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Resume" if state.paused else "⏸ Pause", callback_data="toggle_pause")]]))[span_67](end_span)
    
    [span_68](start_span)await client.send_message(chat_id, "✅ **Reporting Task Completed.**")[span_68](end_span)

# --- Main Application ---
async def main():
    if not API_ID or not API_HASH or not PRIMARY_SESSION:
        [span_69](start_span)print("CRITICAL: Missing core environment variables (API_ID, API_HASH, or PRIMARY_SESSION).")[span_69](end_span)
        return

    [span_70](start_span)app = Client("moderator_tool", api_id=API_ID, api_hash=API_HASH, session_string=PRIMARY_SESSION)[span_70](end_span)

    [span_71](start_span)@app.on_message(filters.command("start") & filters.private)[span_71](end_span)
    async def _start(_, msg):
        [span_72](start_span)uid = msg.from_user.id[span_72](end_span)
        [span_73](start_span)if uid != OWNER_ID and uid not in STATE_DATA.get("sudo_user_ids", []): return[span_73](end_span)
        
        kb = [[InlineKeyboardButton("➕ Add Session", callback_data="add_sess"), InlineKeyboardButton("🎯 Set Target", callback_data="set_tar")],
              [span_74](start_span)[InlineKeyboardButton("⚙️ Settings", callback_data="config"), InlineKeyboardButton("🚀 Launch", callback_data="launch")]][span_74](end_span)
        [span_75](start_span)await msg.reply_text("🛰 **Moderator Tool Controller**\nChoose an action:", reply_markup=InlineKeyboardMarkup(kb))[span_75](end_span)

    [span_76](start_span)@app.on_callback_query()[span_76](end_span)
    async def _callbacks(client, cq: CallbackQuery):
        [span_77](start_span)state = USER_STATES.setdefault(cq.from_user.id, ConversationState())[span_77](end_span)
        
        if cq.data == "set_tar":
            [span_78](start_span)state.mode = "await_group"[span_78](end_span)
            [span_79](start_span)await cq.message.reply_text("Send the Group/Channel Link:")[span_79](end_span)
        elif cq.data == "launch":
            [span_80](start_span)if not state.target.message_id: return await cq.answer("❌ Set target links first!", show_alert=True)[span_80](end_span)
            [span_81](start_span)asyncio.create_task(run_reporting_flow(state, cq.message.chat.id, client))[span_81](end_span)
        elif cq.data == "toggle_pause":
            [span_82](start_span)state.paused = not state.paused[span_82](end_span)
            [span_83](start_span)await cq.answer("Paused" if state.paused else "Resumed")[span_83](end_span)

    [span_84](start_span)@app.on_message(filters.private & ~filters.command("start"))[span_84](end_span)
    async def _input(_, msg):
        [span_85](start_span)state = USER_STATES.get(msg.from_user.id)[span_85](end_span)
        [span_86](start_span)if not state: return[span_86](end_span)

        if state.mode == "await_group":
            [span_87](start_span)state.target.group_link = msg.text.strip()[span_87](end_span)
            [span_88](start_span)state.mode = "await_msg"[span_88](end_span)
            [span_89](start_span)await msg.reply("Invite link saved. Now send the Message Link:")[span_89](end_span)
        elif state.mode == "await_msg":
            [span_90](start_span)cid, mid = parse_link(msg.text.strip())[span_90](end_span)
            if cid:
                [span_91](start_span)state.target.message_link, state.target.chat_identifier, state.target.message_id = msg.text.strip(), cid, mid[span_91](end_span)
                [span_92](start_span)state.mode = "idle"[span_92](end_span)
                [span_93](start_span)await msg.reply(f"✅ Target Locked: {cid} / {mid}")[span_93](end_span)
            [span_94](start_span)else: await msg.reply("❌ Invalid format.")[span_94](end_span)

    [span_95](start_span)print("--- Moderator Bot is Live ---")[span_95](end_span)
    [span_96](start_span)await app.start()[span_96](end_span)
    [span_97](start_span)await asyncio.Event().wait()[span_97](end_span)

if __name__ == "__main__":
    try:
        [span_98](start_span)asyncio.run(main())[span_98](end_span)
    [span_99](start_span)except KeyboardInterrupt: pass[span_99](end_span)

