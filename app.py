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
from typing import Any
InputReportReason = Any

from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
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


# ----------------------
# Configuration helpers
# ----------------------
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


def save_config(config: Dict) -> None:
    tmp_path = f"{CONFIG_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def load_state() -> Dict:
    default_state = {
        "target": {
            "group_link": "",
            "message_link": "",
            "chat_identifier": None,
            "message_id": None,
            "chat_title": None,
            "message_preview": None,
            "active_sessions": 0,
        },
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

    if not os.path.exists(STATE_PATH):
        return default_state

    with open(STATE_PATH, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    # Merge defaults to stay backward compatible
    for key, value in default_state.items():
        if key not in loaded:
            loaded[key] = value
    for key, value in default_state["target"].items():
        loaded["target"].setdefault(key, value)
    for key, value in default_state["report"].items():
        loaded["report"].setdefault(key, value)
    if not isinstance(loaded.get("sudo_user_ids"), list):
        loaded["sudo_user_ids"] = []

    return loaded


def save_state(state: Dict) -> None:
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_PATH)


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
    STATE_DATA["target"].update(
        {
            "group_link": state.target.group_link or "",
            "message_link": state.target.message_link or "",
            "chat_identifier": state.target.chat_identifier,
            "message_id": state.target.message_id,
            "chat_title": state.target.chat_title,
            "message_preview": state.target.message_preview,
            "active_sessions": state.target.active_sessions,
        }
    )
    save_state(STATE_DATA)


def persist_sudo_users(users: List[int]) -> None:
    STATE_DATA["sudo_user_ids"] = sorted(set(users))
    save_state(STATE_DATA)


def load_session_strings(max_count: int, include_primary: bool = True) -> List[Tuple[str, str]]:
    sessions: List[Tuple[str, str]] = []

    if include_primary and PRIMARY_SESSION:
        sessions.append(("primary", PRIMARY_SESSION))

    # Environment sessions (kept for backward compatibility)
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


def get_state(user_id: int) -> ConversationState:
    if user_id not in USER_STATES:
        USER_STATES[user_id] = ConversationState()
        USER_STATES[user_id].report.report_text = STATE_DATA["report"].get("text", "")
        USER_STATES[user_id].report.report_reason_key = STATE_DATA["report"].get(
            "reason", "other"
        )
        USER_STATES[user_id].report.report_total = STATE_DATA["report"].get("total")
        USER_STATES[user_id].report.report_type = STATE_DATA["report"].get(
            "type", "standard"
        )
        USER_STATES[user_id].report.session_limit = int(
            STATE_DATA["report"].get("session_limit") or 0
        )
        if STATE_DATA["target"].get("group_link") and STATE_DATA["target"].get(
            "message_link"
        ):
            chat_identifier, message_id = parse_link(
                STATE_DATA["target"].get("message_link", "")
            )
            USER_STATES[user_id].target.group_link = STATE_DATA["target"].get(
                "group_link"
            )
            USER_STATES[user_id].target.message_link = STATE_DATA["target"].get(
                "message_link"
            )
            USER_STATES[user_id].target.chat_identifier = chat_identifier
            USER_STATES[user_id].target.message_id = message_id
    return USER_STATES[user_id]


CONFIG = load_config()
STATE_DATA = load_state()
API_ID = parse_int(os.getenv("API_ID") or CONFIG.get("API_ID"))
API_HASH = os.getenv("API_HASH") or CONFIG.get("API_HASH", "")
owner_id_value = os.getenv("OWNER_ID") if os.getenv("OWNER_ID") is not None else CONFIG.get("OWNER_ID")
OWNER_ID = parse_int(owner_id_value) or None
LOG_GROUP_LINK = CONFIG.get("LOG_GROUP_LINK", "")
PRIMARY_SESSION = CONFIG.get("PRIMARY_SESSION") or os.getenv("PRIMARY_SESSION", "")

if not API_ID or not API_HASH:
    raise RuntimeError(
        "API_ID and API_HASH must be configured (set env vars or populate config.json)"
    )

if not PRIMARY_SESSION:
    raise RuntimeError("PRIMARY_SESSION must be configured for the bootstrap account")

if OWNER_ID is None:
    raise RuntimeError(
        "OWNER_ID must be configured via environment variable or config.json and cannot be changed after deployment"
    )

# ----------------------
# Utilities
# ----------------------

def format_help() -> str:
    return (
        "**Button-driven Telegram Reporting System**\n"
        "Follow the guided cards to add sessions, pick a target, and launch live reporting without redeploying."
        "\n\n**How it works**\n"
        "• /start opens the control panel for the owner and sudo team.\n"
        "• First choose whether to add new sessions.\n"
        "• Provide the group/channel link, then the exact message link. We validate everything across all sessions.\n"
        "• Configure report reason, text, and counts with the buttons.\n"
        "• Launch reporting to view a live panel with pause/resume, change target, and new-report actions.\n\n"
        "**Roles**\n"
        "• Owner (permanent): full control and sudo management.\n"
        "• Sudo users: same operational powers as owner, managed post-deployment.\n\n"
        "**Accepted links**\n"
        "• Groups/Channels: https://t.me/<username>, https://t.me/+<invite>, or https://t.me/joinchat/<invite>.\n"
        "• Messages: https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>.\n"
        "Validation ensures invalid peer IDs or expired invites are caught early."
    )


def start_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ Add New Sessions", callback_data="add_sessions_prompt")],
        [InlineKeyboardButton("🎯 Set / Change Target", callback_data="setup_target")],
        [InlineKeyboardButton("⚙️ Configure Report Settings", callback_data="configure")],
        [InlineKeyboardButton("🚀 Start Reporting", callback_data="begin_report")],
    ]
    if is_owner:
        buttons.append([InlineKeyboardButton("🛡 Manage Sudo Users", callback_data="manage_sudo")])
    buttons.append([InlineKeyboardButton("ℹ️ Help", callback_data="show_help")])
    return InlineKeyboardMarkup(buttons)


def add_sessions_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Yes, Add Sessions", callback_data="add_sessions")],
            [InlineKeyboardButton("No, Continue", callback_data="back_home")],
        ]
    )


def sudo_management_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Sudo", callback_data="sudo_add")],
            [InlineKeyboardButton("➖ Remove Sudo", callback_data="sudo_remove")],
            [InlineKeyboardButton("📜 List Sudo Users", callback_data="sudo_list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_home")],
        ]
    )


def configuration_keyboard(state: ConversationState) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Change report type", callback_data="choose_type")],
            [InlineKeyboardButton("📝 Change reason text", callback_data="change_text")],
            [InlineKeyboardButton("#️⃣ Change number of reports", callback_data="change_total")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_home")],
        ]
    )


def reason_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row: List[InlineKeyboardButton] = []
    for idx, key in enumerate(REASON_MAP.keys()):
        row.append(InlineKeyboardButton(key.replace("_", " ").title(), callback_data=f"reason:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="configure")])
    return InlineKeyboardMarkup(rows)


def target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 Restart target setup", callback_data="setup_target")],
            [InlineKeyboardButton("🚀 Start reporting", callback_data="begin_report")],
            [InlineKeyboardButton("⬅️ Home", callback_data="back_home")],
        ]
    )


def live_panel_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    toggle = InlineKeyboardButton("▶️ Resume" if paused else "⏸ Pause", callback_data="toggle_pause")
    return InlineKeyboardMarkup(
        [
            [toggle, InlineKeyboardButton("🆕 New report", callback_data="begin_report")],
            [
                InlineKeyboardButton("➕ Add sessions", callback_data="add_sessions"),
                InlineKeyboardButton("🎯 Change target", callback_data="setup_target"),
            ],
            [InlineKeyboardButton("⬅️ Home", callback_data="back_home")],
        ]
    )


def parse_link(link: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    link = link.strip()
    pattern_username = r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)$"
    pattern_c = r"^https?://t\.me/c/([0-9]+)/([0-9]+)$"

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


def is_valid_group_link(link: str) -> bool:
    normalized = link.strip()
    if not normalized.startswith(("http://", "https://")):
        return False
    patterns = [
        r"^https?://t\.me/[A-Za-z0-9_]{3,}$",  # public username links
        r"^https?://t\.me/\+[A-Za-z0-9_-]+$",  # join links with +
        r"^https?://t\.me/joinchat/[A-Za-z0-9_-]+$",  # joinchat links
    ]
    return any(re.match(p, normalized) for p in patterns)


async def resolve_user_identifier(app: Client, message) -> Tuple[Optional[int], str]:
    if message.forward_from:
        return message.forward_from.id, "Forwarded user detected."

    if message.text:
        value = message.text.strip()
        if value.isdigit():
            return int(value), "User ID provided."
        username = value.lstrip("@")
        try:
            user = await app.get_users(username)
            return user.id, f"Resolved @{username}."
        except RPCError:
            return None, "Unable to resolve the provided username."
    return None, "Send a Telegram user ID, @username, or forward a message from the user."


def format_target_summary(state: ConversationState) -> str:
    target = state.target
    report = state.report
    return (
        "🎯 **Target confirmed**\n"
        f"• Group/channel: {target.chat_title or 'Unknown'}\n"
        f"• Link: {target.group_link}\n"
        f"• Message: {target.message_link} (ID {target.message_id})\n"
        f"• Preview: {(target.message_preview or 'Not available')}\n"
        f"• Active sessions: {target.active_sessions}\n\n"
        "**Report configuration**\n"
        f"• Type: {report.report_type}\n"
        f"• Reason key: {report.report_reason_key}\n"
        f"• Text: {report.report_text or 'Not set'}\n"
        f"• Requested reports: {report.report_total or 'Not set'}\n"
        f"• Session limit: {report.session_limit or target.active_sessions}\n"
        "\nUse the buttons to start reporting or change the target."
    )


async def validate_target_with_sessions(
    group_link: str, message_link: str, session_limit: int
) -> Tuple[Optional[TargetContext], List[str]]:
    chat_identifier, message_id = parse_link(message_link)
    if chat_identifier is None or message_id is None:
        return None, [
            "❌ Invalid message link. Use https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>",
        ]

    sessions = load_session_strings(session_limit or 0)
    if not sessions:
        return None, ["❌ No session strings are configured."]

    notes: List[str] = []
    title: Optional[str] = None
    preview: Optional[str] = None
    successes = 0

    for session_name, session_str in sessions:
        status, detail, maybe_title, maybe_preview = await validate_session_access(
            session_name, session_str, group_link, chat_identifier, message_id
        )
        if maybe_title:
            title = maybe_title
        if maybe_preview:
            preview = maybe_preview
        notes.append(f"• {session_name}: {status} ({detail})")
        if status == "reachable":
            successes += 1

    target = TargetContext(
        group_link=group_link,
        message_link=message_link,
        chat_identifier=chat_identifier,
        message_id=message_id,
        chat_title=title,
        message_preview=preview,
        active_sessions=len(sessions),
        validation_notes=notes,
    )

    if successes == 0:
        notes.insert(0, "❌ Validation failed. No sessions could access the target message.")
        return None, notes
    return target, notes


async def run_reporting_flow(
    state: ConversationState, panel_chat: Optional[int], client: Client
) -> None:
    state.mode = "reporting"
    state.paused = False
    report_reason = resolve_reason_class(state.report.report_reason_key)
    report_text = state.report.report_text or REPORT_TEXT
    sessions = load_session_strings(state.report.session_limit or 0)

    header = (
        "🛰️ **Live Reporting Panel**\n"
        f"Target: {state.target.group_link}\n"
        f"Message: {state.target.message_link}\n"
        f"Report reason: {state.report.report_reason_key}\n"
        f"Report text: {report_text or 'Not set'}\n"
        f"Requested total: {state.report.report_total or 'Not set'}\n"
        f"Sessions available: {len(sessions)}"
    )

    sent_id = await send_log_message(
        client, panel_chat, header, reply_markup=live_panel_keyboard(state.paused)
    )
    state.last_panel_text = header
    state.live_panel = sent_id
    state.live_panel_chat = panel_chat
    success = 0
    failed = 0
    details: List[str] = []

    for session_name, session_str in sessions:
        while state.paused:
            await asyncio.sleep(1)
        status, detail = await evaluate_session(
            session_name,
            session_str,
            state.target.group_link or "",
            state.target.chat_identifier or "",
            state.target.message_id or 0,
            reason=report_reason,
            report_text=report_text,
        )
        if status == "reachable":
            success += 1
        else:
            failed += 1
        details.append(f"• {session_name}: {status} ({detail})")
        panel_text = (
            header
            + "\n"
            + f"\nSuccessful reports: {success}\nFailed reports: {failed}\nStatus: {'Paused' if state.paused else 'Running'}\n\n"
            + "\n".join(details)
        )
        if state.live_panel and state.live_panel_chat:
            await edit_log_message(
                client,
                state.live_panel_chat,
                state.live_panel,
                panel_text,
                reply_markup=live_panel_keyboard(state.paused),
            )
            state.last_panel_text = panel_text

    completion = header + "\n\n✅ Reporting finished."
    if state.live_panel and state.live_panel_chat:
        await edit_log_message(
            client,
            state.live_panel_chat,
            state.live_panel,
            completion,
            reply_markup=live_panel_keyboard(state.paused),
        )
        state.last_panel_text = completion


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
    normalized = key.strip().lower()
    cls = REASON_MAP.get(normalized, types.InputReportReasonOther)
    return cls()


def reason_from_config() -> InputReportReason:
    configured_reason = STATE_DATA["report"].get("reason", "other")
    normalized = str(configured_reason).strip().lower()
    if normalized in REASON_MAP:
        return REASON_MAP[normalized]()
    return types.InputReportReasonOther()


REPORT_REASON = reason_from_config()
REPORT_TEXT = STATE_DATA["report"].get("text", "")


async def resolve_log_group_id(client: Client) -> Optional[int]:
    if STATE_DATA.get("log_group_id"):
        return STATE_DATA["log_group_id"]

    if not LOG_GROUP_LINK:
        return None

    try:
        chat = await client.join_chat(LOG_GROUP_LINK)
    except UserAlreadyParticipant:
        chat = await client.get_chat(LOG_GROUP_LINK)
    except RPCError:
        return None

    STATE_DATA["log_group_id"] = chat.id
    save_state(STATE_DATA)
    return chat.id


async def send_log_message(
    client: Client,
    chat_id: Optional[int],
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[int]:
    try:
        target_chat = chat_id or await resolve_log_group_id(client)
        if target_chat is None:
            return None
        msg = await client.send_message(target_chat, text, reply_markup=reply_markup)
        return msg.id
    except RPCError:
        return None
    return None


async def edit_log_message(
    client: Client,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    try:
        target_chat = chat_id or await resolve_log_group_id(client)
        if target_chat is None:
            return
        await client.edit_message_text(target_chat, message_id, text, reply_markup=reply_markup)
    except RPCError:
        pass


async def join_target_chat(
    client: Client,
    join_link: str,
    chat_identifier: Union[str, int],
) -> Tuple[Optional[object], str]:

    normalized = join_link.strip()
    if not normalized.startswith(("http://", "https://")):
        return None, "❌ Group/channel link must start with http:// or https://"

    try:
        chat = await client.join_chat(normalized)
        return await client.resolve_peer(chat.id), "✅ Joined group/channel"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return None, f"⏳ FloodWait {e.value}s while joining"
    except UserAlreadyParticipant:
        try:
            peer = await client.resolve_peer(chat_identifier)
            return peer, "ℹ️ Already a participant"
        except RPCError as e:  # pragma: no cover - defensive
            return None, f"⚠️ Could not confirm membership: {e.MESSAGE or e}"  # type: ignore
    except (InviteHashExpired, InviteHashInvalid):
        return None, "❌ Invite link expired or invalid"
    except (UsernameInvalid, UsernameNotOccupied):
        return None, "❌ Invalid or unknown public group/channel link"
    except RPCError as e:
        return None, f"❌ Failed to join: {e.MESSAGE or e}"  # type: ignore


async def evaluate_session(
    session_name: str,
    session_str: str,
    join_link: str,
    target: Union[str, int],
    message_id: int,
    *,
    reason: Optional[InputReportReason] = None,
    report_text: Optional[str] = None,
) -> Tuple[str, str]:
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
                peer, join_detail = await join_target_chat(
                    user_client, join_link, target
                )
                if not peer:
                    return "invalid", f"Join failed: {join_detail}"

                try:
                    msg = await user_client.get_messages(target, message_id)
                except RPCError as e:
                    return "inaccessible", f"Message error: {e.MESSAGE or e}"  # type: ignore

                await user_client.invoke(
                    functions.messages.Report(
                        peer=peer,
                        id=[msg.id],
                        reason=reason or REPORT_REASON,
                        message=report_text if report_text is not None else REPORT_TEXT,
                    )
                )
                return "reachable", f"Session {me.id} ok ({join_detail})"
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


async def validate_session_access(
    session_name: str,
    session_str: str,
    join_link: str,
    target: Union[str, int],
    message_id: int,
) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Join and fetch the message without reporting to confirm accessibility."""

    try:
        async with Client(
            name=f"validate_{session_name}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_str,
            no_updates=True,
        ) as user_client:
            peer, join_detail = await join_target_chat(user_client, join_link, target)
            if not peer:
                return "invalid", f"Join failed: {join_detail}", None, None
            try:
                msg = await user_client.get_messages(target, message_id)
                preview = (msg.text or msg.caption or "").strip()
                preview = preview[:120] + ("…" if len(preview) > 120 else "") if preview else None
                return "reachable", f"{join_detail}", msg.chat.title or msg.chat.first_name, preview
            except RPCError as e:
                return "inaccessible", f"Message error: {e.MESSAGE or e}", None, None  # type: ignore
    except UserAlreadyParticipant:
        return "reachable", "Already joined", None, None
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return "floodwait", f"FloodWait {e.value}s", None, None
    except RPCError as e:
        return "invalid", f"RPC error: {e.MESSAGE or e}", None, None  # type: ignore
    except Exception as e:  # noqa: BLE001
        return "invalid", f"Unexpected: {e}", None, None


async def handle_run_command(client: Client, message) -> None:
    global OWNER_ID
    if OWNER_ID is None or not has_power(message.from_user.id if message.from_user else None):
        await message.reply_text("❌ Authorization failed. Only owner or sudo users can run this command.")
        return

    parts = message.text.split()
    if len(parts) != 5:
        await message.reply_text(
            "Usage: /run <group_link> <message_link> <sessions_count> <requested_count>"
        )
        return

    _, group_link, target_link, sessions_count_raw, requested_count_raw = parts

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

    if not group_link.startswith(("http://", "https://")):
        await message.reply_text("❌ group_link must start with http:// or https://")
        return

    chat_identifier, msg_id = parse_link(target_link)
    if chat_identifier is None or msg_id is None:
        await message.reply_text(
            "❌ Invalid message link. Use https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>"
        )
        return

    sessions = load_session_strings(sessions_count)
    if not sessions:
        await message.reply_text("No session strings found to run validation")
        return

    state = get_state(message.from_user.id)
    state.target.group_link = group_link
    state.target.message_link = target_link
    state.target.chat_identifier = chat_identifier
    state.target.message_id = msg_id
    persist_target(state)

    available_sessions = len(sessions)

    panel_lines = [
        "🛰️ **Review Panel Initialized**",
        f"Target group/channel: {group_link}",
        f"Target message: {target_link}",
        f"Chat reference: {chat_identifier}",
        f"Message ID: {msg_id}",
        f"Requested sessions: {sessions_count}",
        f"Requested count: {requested_count}",
        f"Available sessions: {available_sessions}",
        f"Configured total reports: {state.report.report_total or '—'}",
        f"Report reason: {state.report.report_reason_key or 'other'}",
        f"Report text: {state.report.report_text or 'Not set'}",
    ]
    panel_lines.append("Status: processing…")
    panel_text = "\n".join(panel_lines)
    panel_chat = message.chat.id if message.chat else STATE_DATA.get("log_group_id")
    panel_id = await send_log_message(client, panel_chat or message.chat.id, panel_text)

    results: List[str] = []
    reachable = 0
    processed = 0

    for session_name, session_str in sessions:
        status, detail = await evaluate_session(
            session_name, session_str, group_link, chat_identifier, msg_id
        )
        processed += 1
        if status == "reachable":
            reachable += 1
        results.append(f"• **{session_name}** — {status} ({detail})")

        panel_text = (
            "🛰️ **Review Panel**\n"
            "**Target details**\n"
            f"• Group/channel link: {group_link}\n"
            f"• Link: {target_link}\n"
            f"• Chat: {chat_identifier} | Message: {msg_id}\n"
            f"• Requested sessions: {sessions_count} | Requested count: {requested_count}\n"
            f"• Configured total reports: {state.report.report_total or '—'}\n"
            f"• Report reason: {state.report.report_reason_key or 'other'} | Text: {REPORT_TEXT or 'Not set'}\n"
            + (f"• Log group link: {LOG_GROUP_LINK}\n" if LOG_GROUP_LINK else "")
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
    await message.reply_text(
        f"🔒 Owner is locked to `{OWNER_ID}` and cannot be changed after deployment."
    )


def is_owner(user_id: Optional[int]) -> bool:
    return user_id is not None and OWNER_ID is not None and user_id == OWNER_ID


def is_sudo(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id in STATE_DATA.get("sudo_user_ids", [])


def has_power(user_id: Optional[int]) -> bool:
    return is_owner(user_id) or is_sudo(user_id)


def owner_required(message) -> bool:
    return bool(message.from_user and is_owner(message.from_user.id))


async def handle_set_reason(message) -> None:
    global REPORT_REASON
    if not has_power(message.from_user.id if message.from_user else None):
        await message.reply_text("❌ Only the owner or sudo users can update the report reason.")
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

    REPORT_REASON = reason_map[value]()
    state = get_state(message.from_user.id)
    state.report.report_reason_key = value
    state.report.report_type = value.replace("_", " ").title()
    persist_report_settings(state)
    await message.reply_text(f"✅ Report reason updated to `{value}`.")


async def handle_set_report_text(message) -> None:
    global REPORT_TEXT
    if not has_power(message.from_user.id if message.from_user else None):
        await message.reply_text("❌ Only the owner or sudo users can update the report text.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.reply_text("Usage: /set_report_text <text>")
        return

    REPORT_TEXT = parts[1].strip()
    state = get_state(message.from_user.id)
    state.report.report_text = REPORT_TEXT
    persist_report_settings(state)
    await message.reply_text("✅ Report text updated.")


async def start_target_prompt(message, state: ConversationState) -> None:
    state.mode = "awaiting_group_link"
    await message.reply_text(
        "Send the **group or channel link** to target (accepts https://t.me/username, https://t.me/+invite, or https://t.me/joinchat/invite).",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
        ),
    )


async def confirm_target_and_configure(
    message, state: ConversationState, validation_notes: List[str]
) -> None:
    summary = format_target_summary(state)
    summary += "\n\n" + "\n".join(validation_notes)
    await message.reply_text(summary, reply_markup=target_keyboard())
    if state.report.report_total is None:
        state.mode = "awaiting_report_total"
        await message.reply_text(
            "How many reports should be sent? Reply with a number, then fine-tune the reason via buttons.",
            reply_markup=configuration_keyboard(state),
        )
    else:
        await message.reply_text(
            "Choose a report reason, provide the number of reports, or adjust text via the settings.",
            reply_markup=configuration_keyboard(state),
        )
    state.report.session_limit = state.report.session_limit or state.target.active_sessions
    persist_report_settings(state)


async def handle_set_total_reports(message) -> None:
    if not has_power(message.from_user.id if message.from_user else None):
        await message.reply_text("❌ Only the owner or sudo users can update the total reports.")
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
    state = get_state(message.from_user.id)
    state.report.report_total = total_reports
    persist_report_settings(state)
    await message.reply_text(f"✅ Total reports set to {total_reports}.")


async def handle_set_links(message) -> None:
    global LOG_GROUP_LINK
    if not has_power(message.from_user.id if message.from_user else None):
        await message.reply_text("❌ Only the owner or sudo users can update links.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply_text("Usage: /set_links <log_group_link>")
        return

    log_group_link = parts[1].strip()

    if not (log_group_link.startswith("http://") or log_group_link.startswith("https://")):
        await message.reply_text("❌ log_group_link must start with http:// or https://")
        return

    LOG_GROUP_LINK = log_group_link
    CONFIG["LOG_GROUP_LINK"] = log_group_link
    save_config(CONFIG)
    STATE_DATA["log_group_id"] = None
    save_state(STATE_DATA)
    await message.reply_text("✅ Log group link updated. Future panels will use the new group.")


async def handle_add_session(message) -> None:
    if not has_power(message.from_user.id if message.from_user else None):
        await message.reply_text("❌ Only the owner or sudo users can add sessions.")
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

    @app.on_message(filters.command("start"))
    async def _start(_, msg):
        if not msg.from_user:
            await msg.reply_text("⚠️ Start is available only from owner/sudo private chats or the log group.")
            return

        state = get_state(msg.from_user.id)
        if not has_power(msg.from_user.id):
            await msg.reply_text("❌ Only the owner or configured sudo users can control this bot.")
            return

        state.mode = "idle"
        state.pending_sudo_action = None
        if not state.target.group_link:
            state.target = TargetContext()
        state.report.report_text = STATE_DATA["report"].get("text", "")
        state.report.report_total = STATE_DATA["report"].get("total")
        state.report.report_type = STATE_DATA["report"].get("type", "standard")
        await msg.reply_text(
            "Do you want to add new sessions? Use the buttons to continue the guided setup.",
            reply_markup=add_sessions_prompt_keyboard(),
        )
        await msg.reply_text(
            "Main control panel ready. Follow the buttons to set targets, configure reports, or launch the live panel.",
            reply_markup=start_keyboard(is_owner=is_owner(msg.from_user.id)),
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

    @app.on_callback_query()
    async def _callbacks(client: Client, cq: CallbackQuery):
        if OWNER_ID is None:
            await cq.answer("Set OWNER_ID first via config.json.", show_alert=True)
            return
        if not cq.from_user or not has_power(cq.from_user.id):
            await cq.answer("Only the owner or sudo users can use these controls.", show_alert=True)
            return

        state = get_state(cq.from_user.id)
        data = cq.data or ""

        if data == "manage_sudo":
            if not is_owner(cq.from_user.id):
                await cq.answer("Only the owner can manage sudo users.", show_alert=True)
                return
            await cq.message.reply_text(
                "Owner panel: manage sudo users post-deployment.",
                reply_markup=sudo_management_keyboard(),
            )
            await cq.answer()
            return

        if data == "sudo_add":
            if not is_owner(cq.from_user.id):
                await cq.answer("Only the owner can add sudo users.", show_alert=True)
                return
            state.mode = "awaiting_sudo_add"
            state.pending_sudo_action = "add"
            await cq.message.reply_text(
                "Send the sudo user as an ID, @username, or forward a message from them.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                ),
            )
            await cq.answer()
            return

        if data == "sudo_remove":
            if not is_owner(cq.from_user.id):
                await cq.answer("Only the owner can remove sudo users.", show_alert=True)
                return
            sudo_ids = STATE_DATA.get("sudo_user_ids", [])
            if not sudo_ids:
                await cq.message.reply_text("No sudo users configured.")
                await cq.answer()
                return
            rows = [
                [InlineKeyboardButton(str(uid), callback_data=f"sudo_remove:{uid}")]
                for uid in sudo_ids
            ]
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manage_sudo")])
            await cq.message.reply_text(
                "Select a sudo user to remove.", reply_markup=InlineKeyboardMarkup(rows)
            )
            await cq.answer()
            return

        if data == "sudo_list":
            if not is_owner(cq.from_user.id):
                await cq.answer("Only the owner can view sudo roster.", show_alert=True)
                return
            sudo_ids = STATE_DATA.get("sudo_user_ids", [])
            if not sudo_ids:
                await cq.message.reply_text("No sudo users configured.")
            else:
                await cq.message.reply_text(
                    "Current sudo users:\n" + "\n".join(f"• {uid}" for uid in sudo_ids)
                )
            await cq.answer()
            return

        if data.startswith("sudo_remove:"):
            if not is_owner(cq.from_user.id):
                await cq.answer("Only the owner can remove sudo users.", show_alert=True)
                return
            _, raw_id = data.split(":", 1)
            try:
                remove_id = int(raw_id)
            except ValueError:
                await cq.answer("Invalid user id", show_alert=True)
                return
            sudo_ids = STATE_DATA.get("sudo_user_ids", [])
            if remove_id in sudo_ids:
                sudo_ids.remove(remove_id)
                persist_sudo_users(sudo_ids)
                await cq.message.reply_text(
                    f"Removed sudo access for `{remove_id}`.",
                    reply_markup=sudo_management_keyboard(),
                )
            else:
                await cq.message.reply_text("User not in sudo list.")
            await cq.answer()
            return

        if data == "add_sessions_prompt":
            await cq.message.reply_text(
                "Do you want to add new sessions now?",
                reply_markup=add_sessions_prompt_keyboard(),
            )
            await cq.answer()
            return

        if data == "add_sessions":
            state.mode = "awaiting_session_name"
            await cq.message.reply_text(
                "Send a session name (letters/numbers/underscore). After that, send the session string.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                ),
            )
            await cq.answer()
            return

        if data == "setup_target":
            await start_target_prompt(cq.message, state)
            await cq.answer()
            return

        if data == "configure":
            text = (
                "⚙️ **Configuration**\n"
                f"Report type: {state.report.report_type}\n"
                f"Reason key: {state.report.report_reason_key}\n"
                f"Report text: {state.report.report_text or 'Not set'}\n"
                f"Total reports: {state.report.report_total or 'Not set'}"
            )
            await cq.message.reply_text(text, reply_markup=configuration_keyboard(state))
            await cq.answer()
            return

        if data == "show_help":
            await cq.message.reply_text(format_help())
            await cq.answer()
            return

        if data == "back_home":
            state.mode = "idle"
            state.pending_sudo_action = None
            await cq.message.reply_text(
                "Back to home. Choose what to do next.",
                reply_markup=start_keyboard(is_owner=is_owner(cq.from_user.id)),
            )
            await cq.answer()
            return

        if data == "choose_type":
            await cq.message.reply_text(
                "Select a report reason (applies to new reports immediately).",
                reply_markup=reason_keyboard(),
            )
            await cq.answer()
            return

        if data.startswith("reason:"):
            _, key = data.split(":", 1)
            global REPORT_REASON
            state.report.report_reason_key = key
            state.report.report_type = key.replace("_", " ").title()
            REPORT_REASON = resolve_reason_class(key)
            persist_report_settings(state)
            await cq.message.reply_text(
                f"✅ Reason updated to {key}.", reply_markup=configuration_keyboard(state)
            )
            await cq.answer("Reason updated")
            return

        if data == "change_text":
            state.mode = "awaiting_report_text"
            await cq.message.reply_text(
                "Send the new report text/message body.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                ),
            )
            await cq.answer()
            return

        if data == "change_total":
            state.mode = "awaiting_report_total"
            await cq.message.reply_text(
                "Send the new total number of reports to log (integer).",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                ),
            )
            await cq.answer()
            return

        if data == "begin_report":
            if not state.target.message_id:
                await cq.answer("Set a target first.", show_alert=True)
                return
            state.report.session_limit = state.report.session_limit or state.target.active_sessions
            await cq.message.reply_text("Re-validating target across sessions…")
            target, notes = await validate_target_with_sessions(
                state.target.group_link or "",
                state.target.message_link or "",
                state.report.session_limit,
            )
            if not target:
                await cq.message.reply_text("\n".join(notes))
                await cq.answer("Validation failed", show_alert=True)
                return
            state.target = target
            persist_target(state)
            await cq.message.reply_text(
                "Starting live reporting…", reply_markup=live_panel_keyboard()
            )
            asyncio.create_task(
                run_reporting_flow(
                    state,
                    cq.message.chat.id if cq.message.chat else STATE_DATA.get("log_group_id"),
                    client,
                )
            )
            await cq.answer()
            return

        if data == "toggle_pause":
            state.paused = not state.paused
            await cq.answer("Paused" if state.paused else "Resumed")
            if state.live_panel and state.live_panel_chat:
                text = state.last_panel_text or "🛰️ Live Reporting Panel"
                status_line = f"\nStatus: {'Paused' if state.paused else 'Running'}"
                await edit_log_message(
                    client,
                    state.live_panel_chat,
                    state.live_panel,
                    text + status_line,
                    reply_markup=live_panel_keyboard(state.paused),
                )

    @app.on_message(~filters.command(["start", "help", "set_owner", "run", "set_reason", "set_report_text", "set_total_reports", "set_links", "add_session"]))
    async def _stateful(_, msg):
        if not msg.from_user:
            return
        if OWNER_ID is None:
            await msg.reply_text("Set OWNER_ID first in config.json.")
            return
        if not has_power(msg.from_user.id):
            await msg.reply_text("❌ Only the owner or sudo users can control this bot.")
            return

        state = get_state(msg.from_user.id)

        if state.mode == "awaiting_sudo_add":
            if not is_owner(msg.from_user.id):
                state.mode = "idle"
                state.pending_sudo_action = None
                await msg.reply_text("Only the owner can manage sudo users.")
                return
            user_id, detail = await resolve_user_identifier(app, msg)
            if not user_id:
                await msg.reply_text(f"❌ {detail}")
                return
            if user_id == OWNER_ID:
                await msg.reply_text("Owner is already fully privileged and cannot be demoted.")
                state.mode = "idle"
                state.pending_sudo_action = None
                return
            sudo_ids = STATE_DATA.get("sudo_user_ids", [])
            if user_id in sudo_ids:
                await msg.reply_text(
                    f"ℹ️ `{user_id}` is already a sudo user.",
                    reply_markup=sudo_management_keyboard(),
                )
            else:
                sudo_ids.append(user_id)
                persist_sudo_users(sudo_ids)
                await msg.reply_text(
                    f"✅ Added `{user_id}` as sudo. They now have full operational control.",
                    reply_markup=sudo_management_keyboard(),
                )
            state.mode = "idle"
            state.pending_sudo_action = None
            return

        if state.mode == "awaiting_session_name":
            name = msg.text.strip()
            if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", name):
                await msg.reply_text(
                    "❌ Session name must be 1-64 characters (letters, numbers, underscores, hyphens)."
                )
                return
            state.pending_session_name = name
            state.mode = "awaiting_session_value"
            await msg.reply_text(
                f"Send the session string for `{name}`.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                ),
            )
            return

        if state.mode == "awaiting_session_value":
            name = state.pending_session_name
            if not name:
                state.mode = "idle"
                await msg.reply_text("Session flow reset. Start again from /start.")
                return
            session_str = msg.text.strip()
            if len(session_str) < 10:
                await msg.reply_text("❌ Session string looks too short. Please provide a valid session string.")
                return
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            dest = os.path.join(SESSIONS_DIR, f"{name}.session")
            with open(dest, "w", encoding="utf-8") as f:
                f.write(session_str)
            state.mode = "idle"
            state.pending_session_name = None
            await msg.reply_text(
                f"✅ Session `{name}` added. Add more or go back home.",
                reply_markup=start_keyboard(is_owner=is_owner(msg.from_user.id)),
            )
            return

        if state.mode == "awaiting_group_link":
            link = msg.text.strip()
            if not is_valid_group_link(link):
                await msg.reply_text(
                    "❌ Invalid group/channel link. Provide a valid https://t.me invite or @username link.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                    ),
                )
                return
            state.target.group_link = link
            state.mode = "awaiting_message_link"
            await msg.reply_text(
                "Great. Now send the target **message link** (https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>).",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data="back_home")]]
                ),
            )
            return

        if state.mode == "awaiting_message_link":
            message_link = msg.text.strip()
            chat_identifier, msg_id = parse_link(message_link)
            if chat_identifier is None or msg_id is None:
                await msg.reply_text(
                    "❌ Invalid message link. Use https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>."
                )
                return
            state.target.message_link = message_link
            state.target.chat_identifier = chat_identifier
            state.target.message_id = msg_id
            state.report.session_limit = 0
            await msg.reply_text("Validating target across sessions…")
            target, notes = await validate_target_with_sessions(
                state.target.group_link or "", message_link, state.report.session_limit
            )
            if not target:
                await msg.reply_text("\n".join(notes))
                state.mode = "idle"
                return
            state.target = target
            persist_target(state)
            state.mode = "confirmed"
            await confirm_target_and_configure(msg, state, notes)
            return

    if state.mode == "awaiting_report_text":
        global REPORT_TEXT
        text = msg.text.strip()
        state.report.report_text = text
        REPORT_TEXT = text
        persist_report_settings(state)
        state.mode = "idle"
        await msg.reply_text(
            "✅ Report text updated.", reply_markup=configuration_keyboard(state)
        )
        return

    if state.mode == "awaiting_report_total":
        try:
            total = int(msg.text.strip())
            if total < 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Please send a non-negative integer.")
            return
        state.report.report_total = total
        persist_report_settings(state)
        state.mode = "idle"
        await msg.reply_text(
            f"✅ Total reports updated to {total}.",
            reply_markup=configuration_keyboard(state),
        )
        return

    await msg.reply_text(
        "Use the buttons from /start to navigate the guided flow.",
        reply_markup=start_keyboard(is_owner=is_owner(msg.from_user.id)),
    )

    await app.start()
    print("Moderator tool is running...")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


