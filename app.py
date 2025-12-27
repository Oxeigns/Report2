import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

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

InputReportReason = Any

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
SESSIONS_DIR = "sessions"

# ---- Pyrogram crash-guard for large channel peer IDs
try:
    from pyrogram import utils as _pyro_utils  # type: ignore

    _ORIG_GET_PEER_TYPE = _pyro_utils.get_peer_type
    _MIN_CHANNEL_PEER = -1002147483648

    def _patched_get_peer_type(peer_id: Any) -> Any:
        if isinstance(peer_id, int) and peer_id <= -1000000000000 and peer_id < _MIN_CHANNEL_PEER:
            return "channel"
        return _ORIG_GET_PEER_TYPE(peer_id)

    _pyro_utils.get_peer_type = _patched_get_peer_type  # type: ignore[attr-defined]
except Exception:
    pass


async def safe_reply_text(message, text: str, **kwargs) -> Optional[object]:
    try:
        return await message.reply_text(text, **kwargs)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            return await message.reply_text(text, **kwargs)
        except RPCError:
            return None
    except RPCError:
        return None


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
    quick_start: bool = False


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
        "session_limit": 0,
        "log_group_id": None,
        "sudo_user_ids": [],
        "last_status": "",
    }

    if not os.path.exists(STATE_PATH):
        return default_state

    with open(STATE_PATH, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    for key, value in default_state.items():
        if key not in loaded:
            loaded[key] = value
    for key, value in default_state["target"].items():
        loaded["target"].setdefault(key, value)
    if not isinstance(loaded.get("sudo_user_ids"), list):
        loaded["sudo_user_ids"] = []

    return loaded


def save_state(state: Dict) -> None:
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_PATH)


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


def persist_session_limit(limit_value: int) -> None:
    STATE_DATA["session_limit"] = limit_value
    save_state(STATE_DATA)


def persist_last_status(text: str) -> None:
    STATE_DATA["last_status"] = text
    save_state(STATE_DATA)


def persist_sudo_users(users: List[int]) -> None:
    STATE_DATA["sudo_user_ids"] = sorted(set(users))
    save_state(STATE_DATA)


def load_session_strings(max_count: int, include_primary: bool = True) -> List[Tuple[str, str]]:
    sessions: List[Tuple[str, str]] = []

    if include_primary and PRIMARY_SESSION:
        sessions.append(("primary", PRIMARY_SESSION))

    for key, value in sorted(os.environ.items()):
        if key.startswith("SESSION_") and value.strip():
            sessions.append((key, value.strip()))

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
        USER_STATES[user_id].report.session_limit = int(STATE_DATA.get("session_limit") or 0)

        if STATE_DATA["target"].get("group_link") and STATE_DATA["target"].get("message_link"):
            chat_identifier, message_id = parse_link(STATE_DATA["target"].get("message_link", ""))
            USER_STATES[user_id].target.group_link = STATE_DATA["target"].get("group_link")
            USER_STATES[user_id].target.message_link = STATE_DATA["target"].get("message_link")
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
    raise RuntimeError("API_ID and API_HASH must be configured (set env vars or populate config.json)")

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
        "**Session-mode Reporting Bot**\n"
        "Use commands to add sessions, set targets, and run reports. Inline buttons are disabled for safety.\n\n"
        "**Commands**\n"
        "/start - show help and current status\n"
        "/help - show command list\n"
        "/set_target <group_link> - set target group/channel link, then send the message link when prompted\n"
        "/send_link <group_link> - quick flow asking for message link and number of reports\n"
        "/session_limit <n> - limit number of sessions used (0 = all)\n"
        "/add_session <name> <session_string> - store a user session string\n"
        "/set_reason <key> - choose report type (keys: "
        + ", ".join(REASON_MAP.keys())
        + ")\n"
        "/set_total_reports <n> - set number of reports to send\n"
        "/pause /resume - control reporting loop\n"
        "/status - show current state summary\n"
        "/start_report - begin reporting with the current configuration\n"
        "/cancel - cancel any pending prompts"
    )


def format_status(state: ConversationState) -> str:
    target = state.target
    report = state.report
    return (
        "**Current status**\n"
        f"Target group: {target.group_link or 'not set'}\n"
        f"Message link: {target.message_link or 'not set'}\n"
        f"Message ID: {target.message_id or 'n/a'}\n"
        f"Preview: {(target.message_preview or 'Not available')}\n"
        f"Sessions active (last validation): {target.active_sessions}\n"
        f"Session limit: {report.session_limit or '0 (all)'}\n"
        f"Report reason: {report.report_reason_key}\n"
        f"Report text: {report.report_text or 'default'}\n"
        f"Reports requested: {report.report_total or 'not set'}\n"
        f"Paused: {state.paused}\n"
        f"Last status: {STATE_DATA.get('last_status') or 'n/a'}"
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
        r"^https?://t\.me/[A-Za-z0-9_]{3,}$",
        r"^https?://t\.me/\+[A-Za-z0-9_-]+$",
        r"^https?://t\.me/joinchat/[A-Za-z0-9_-]+$",
    ]
    return any(re.match(p, normalized) for p in patterns)


async def resolve_user_identifier(app: Client, message) -> Tuple[Optional[int], str]:
    if getattr(message, "forward_from", None):
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
        f"• Reason key: {report.report_reason_key}\n"
        f"• Text: {report.report_text or 'Not set'}\n"
        f"• Requested reports: {report.report_total or 'Not set'}\n"
        f"• Session limit: {report.session_limit or target.active_sessions}\n"
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


REPORT_REASON = resolve_reason_class(STATE_DATA.get("report_reason", "other"))
REPORT_TEXT = STATE_DATA.get("report_text", "")


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
) -> Optional[int]:
    try:
        target_chat = chat_id or await resolve_log_group_id(client)
        if target_chat is None:
            return None
        msg = await client.send_message(target_chat, text)
        return msg.id
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            target_chat = chat_id or await resolve_log_group_id(client)
            if target_chat is None:
                return None
            msg = await client.send_message(target_chat, text)
            return msg.id
        except RPCError:
            return None
    except RPCError:
        return None


async def edit_log_message(
    client: Client,
    chat_id: Optional[int],
    message_id: int,
    text: str,
) -> None:
    try:
        target_chat = chat_id or await resolve_log_group_id(client)
        if target_chat is None:
            return
        await client.edit_message_text(target_chat, message_id, text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            target_chat = chat_id or await resolve_log_group_id(client)
            if target_chat is None:
                return
            await client.edit_message_text(target_chat, message_id, text)
        except RPCError:
            return
    except RPCError:
        return


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
        except RPCError as e:
            return None, f"⚠️ Could not confirm membership: {getattr(e, 'MESSAGE', None) or e}"
    except (InviteHashExpired, InviteHashInvalid):
        return None, "❌ Invite link expired or invalid"
    except (UsernameInvalid, UsernameNotOccupied):
        return None, "❌ Invalid or unknown public group/channel link"
    except RPCError as e:
        return None, f"❌ Failed to join: {getattr(e, 'MESSAGE', None) or e}"


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
                peer, join_detail = await join_target_chat(user_client, join_link, target)
                if not peer:
                    return "invalid", f"Join failed: {join_detail}"

                try:
                    msg = await user_client.get_messages(target, message_id)
                except RPCError as e:
                    return "inaccessible", f"Message error: {getattr(e, 'MESSAGE', None) or e}"

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
                return "inaccessible", f"RPC error: {getattr(e, 'MESSAGE', None) or e}"
    except RPCError as e:
        if isinstance(e, FloodWait):
            await asyncio.sleep(e.value)
            return "floodwait", f"FloodWait {e.value}s"
        return "invalid", f"Session error: {getattr(e, 'MESSAGE', None) or e}"
    except Exception as e:  # noqa: BLE001
        return "invalid", f"Unexpected: {e}"


async def validate_session_access(
    session_name: str,
    session_str: str,
    join_link: str,
    target: Union[str, int],
    message_id: int,
) -> Tuple[str, str, Optional[str], Optional[str]]:
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
                title = None
                try:
                    title = msg.chat.title or msg.chat.first_name
                except Exception:
                    title = None
                return "reachable", f"{join_detail}", title, preview
            except RPCError as e:
                return "inaccessible", f"Message error: {getattr(e, 'MESSAGE', None) or e}", None, None
    except UserAlreadyParticipant:
        return "reachable", "Already joined", None, None
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return "floodwait", f"FloodWait {e.value}s", None, None
    except RPCError as e:
        return "invalid", f"RPC error: {getattr(e, 'MESSAGE', None) or e}", None, None
    except Exception as e:  # noqa: BLE001
        return "invalid", f"Unexpected: {e}", None, None


def is_owner(user_id: Optional[int]) -> bool:
    return user_id == OWNER_ID


def is_sudo(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id in STATE_DATA.get("sudo_user_ids", [])


def resolve_effective_user_id(message) -> Optional[int]:
    if getattr(message, "from_user", None):
        return message.from_user.id
    if getattr(message, "sender_chat", None) and message.chat and STATE_DATA.get("log_group_id"):
        if message.sender_chat.id == STATE_DATA.get("log_group_id"):
            return OWNER_ID
    return None


def has_power(user_id: Optional[int]) -> bool:
    return is_owner(user_id) or is_sudo(user_id)


async def run_reporting_flow(state: ConversationState, panel_chat: Optional[int], client: Client) -> None:
    if not state.target.message_link or not state.target.group_link or state.target.chat_identifier is None:
        return

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

    sent_id = await send_log_message(client, panel_chat, header)
    state.last_panel_text = header
    state.live_panel = sent_id
    state.live_panel_chat = panel_chat

    processed = 0
    successes = 0
    failures = 0

    for session_name, session_str in sessions:
        if state.paused:
            break
        status, detail = await evaluate_session(
            session_name,
            session_str,
            state.target.group_link or "",
            state.target.chat_identifier,
            state.target.message_id or 0,
            reason=report_reason,
            report_text=report_text,
        )
        processed += 1
        if status == "reachable":
            successes += 1
        else:
            failures += 1

        body = (
            header
            + "\n\n"
            + "**Progress**\n"
            + f"Sessions processed: {processed}/{len(sessions)}\n"
            + f"Success: {successes}\n"
            + f"Failed: {failures}\n"
            + f"Latest: {session_name} -> {status} ({detail})"
        )
        state.last_panel_text = body
        if sent_id and panel_chat is not None:
            await edit_log_message(client, panel_chat, sent_id, body)

    completion = state.last_panel_text + "\n\n✅ Reporting finished."
    if state.live_panel and state.live_panel_chat:
        await edit_log_message(client, state.live_panel_chat, state.live_panel, completion)
    persist_last_status(completion)
    state.mode = "idle"


async def handle_set_reason(message, state: ConversationState) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await safe_reply_text(message, "Usage: /set_reason <" + "|".join(REASON_MAP.keys()) + ">")
        return
    value = parts[1].strip().lower()
    if value not in REASON_MAP:
        await safe_reply_text(message, "Invalid reason key. Choose from: " + ", ".join(REASON_MAP.keys()))
        return
    state.report.report_reason_key = value
    await safe_reply_text(message, f"Report reason set to {value}. Now set number of reports with /set_total_reports.")


async def handle_set_report_text(message, state: ConversationState) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await safe_reply_text(message, "Usage: /set_report_text <text>")
        return
    state.report.report_text = parts[1].strip()
    await safe_reply_text(message, "Report text updated.")


async def handle_set_total_reports(message, state: ConversationState) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await safe_reply_text(message, "Usage: /set_total_reports <number>")
        return
    try:
        total = int(parts[1].strip())
        if total <= 0:
            raise ValueError
    except ValueError:
        await safe_reply_text(message, "Please provide a positive integer for total reports.")
        return
    state.report.report_total = total
    await safe_reply_text(message, f"Total reports set to {total}. Use /start_report to begin.")


async def handle_add_session(message) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await safe_reply_text(message, "Usage: /add_session <name> <session_string>")
        return
    name = parts[1].strip()
    session_str = parts[2].strip()
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", name):
        await safe_reply_text(message, "Session name must be 1-64 characters (letters, numbers, underscores, hyphens).")
        return
    if len(session_str) < 10:
        await safe_reply_text(message, "Session string looks too short.")
        return
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    dest = os.path.join(SESSIONS_DIR, f"{name}.session")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(session_str)
    await safe_reply_text(message, f"Session `{name}` added. Run /status to review.")


async def handle_set_links(message, state: ConversationState, group_link: str, message_link: str) -> None:
    state.target.group_link = group_link
    state.target.message_link = message_link
    state.target.chat_identifier, state.target.message_id = parse_link(message_link)
    state.report.session_limit = state.report.session_limit or int(STATE_DATA.get("session_limit") or 0)
    await safe_reply_text(message, "Validating target across sessions…")
    target, notes = await validate_target_with_sessions(
        state.target.group_link or "", message_link, state.report.session_limit
    )
    if not target:
        await safe_reply_text(message, "\n".join(notes))
        state.mode = "idle"
        return
    state.target = target
    persist_target(state)
    summary = format_target_summary(state) + "\n\nValidation notes:\n" + "\n".join(notes)
    await safe_reply_text(
        message,
        summary
        + "\n\nChoose report type with /set_reason <key>. Available: "
        + ", ".join(REASON_MAP.keys())
        + "\nThen set total reports with /set_total_reports <n> and start with /start_report.",
    )
    state.mode = "awaiting_report_type"


async def main():
    app = Client(
        "moderator_tool",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=PRIMARY_SESSION,
    )

    def unauthorized(msg) -> bool:
        user_id = resolve_effective_user_id(msg)
        if not has_power(user_id):
            asyncio.create_task(safe_reply_text(msg, "Unauthorized."))
            return True
        return False

    @app.on_message(filters.command("start"))
    async def _start(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        await safe_reply_text(msg, format_help())
        await safe_reply_text(msg, format_status(state))

    @app.on_message(filters.command("help"))
    async def _help(_, msg):
        if unauthorized(msg):
            return
        await safe_reply_text(msg, format_help())
        await safe_reply_text(msg, "If you want to report, add sessions with /add_session or send /send_link <group_link>.")

    @app.on_message(filters.command("set_target"))
    async def _set_target(_, msg):
        if unauthorized(msg):
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await safe_reply_text(msg, "Usage: /set_target <group_link>")
            return
        link = parts[1].strip()
        if not is_valid_group_link(link):
            await safe_reply_text(msg, "Invalid group/channel link. Provide a valid https://t.me link.")
            return
        state = get_state(msg.from_user.id)
        state.target.group_link = link
        state.mode = "awaiting_message_link"
        state.quick_start = False
        await safe_reply_text(
            msg,
            "Group link saved. Send the target message link (https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>).",
        )

    @app.on_message(filters.command("send_link"))
    async def _send_link(_, msg):
        if unauthorized(msg):
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await safe_reply_text(msg, "Usage: /send_link <group_link>")
            return
        link = parts[1].strip()
        if not is_valid_group_link(link):
            await safe_reply_text(msg, "Invalid group/channel link. Provide a valid https://t.me link.")
            return
        state = get_state(msg.from_user.id)
        state.target.group_link = link
        state.mode = "awaiting_message_link"
        state.quick_start = True
        await safe_reply_text(msg, "Send the target message link for quick reporting.")

    @app.on_message(filters.command("session_limit"))
    async def _session_limit(_, msg):
        if unauthorized(msg):
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await safe_reply_text(msg, "Usage: /session_limit <number>")
            return
        try:
            value = int(parts[1].strip())
            if value < 0:
                raise ValueError
        except ValueError:
            await safe_reply_text(msg, "Provide a non-negative integer (0 means all sessions).")
            return
        state = get_state(msg.from_user.id)
        state.report.session_limit = value
        persist_session_limit(value)
        await safe_reply_text(msg, f"Session limit set to {value or 'all'}.")

    @app.on_message(filters.command("add_session"))
    async def _add_session_handler(_, msg):
        if unauthorized(msg):
            return
        await handle_add_session(msg)

    @app.on_message(filters.command("set_reason"))
    async def _set_reason(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        await handle_set_reason(msg, state)

    @app.on_message(filters.command("set_report_text"))
    async def _set_report_text(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        await handle_set_report_text(msg, state)

    @app.on_message(filters.command("set_total_reports"))
    async def _set_total_reports(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        await handle_set_total_reports(msg, state)

    @app.on_message(filters.command("pause"))
    async def _pause(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        state.paused = True
        await safe_reply_text(msg, "Reporting paused. Use /resume to continue or /start_report to restart.")

    @app.on_message(filters.command("resume"))
    async def _resume(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        state.paused = False
        await safe_reply_text(msg, "Reporting resumed. Use /start_report to relaunch if needed.")

    @app.on_message(filters.command("status"))
    async def _status(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        await safe_reply_text(msg, format_status(state))

    @app.on_message(filters.command("cancel"))
    async def _cancel(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        state.mode = "idle"
        state.pending_session_name = None
        state.quick_start = False
        await safe_reply_text(msg, "All pending actions cancelled.")

    @app.on_message(filters.command("start_report"))
    async def _start_report(client, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)
        if not state.target.group_link or not state.target.message_link:
            await safe_reply_text(msg, "Set a target first with /set_target or /send_link.")
            return
        if state.report.report_total is None:
            await safe_reply_text(msg, "Set report count with /set_total_reports before starting.")
            return
        await safe_reply_text(msg, "Reporting started. Progress will appear here or in the log group.")
        await run_reporting_flow(state, msg.chat.id if msg.chat else None, client)

    @app.on_message(filters.text & ~filters.command([]))
    async def _message_handler(_, msg):
        if unauthorized(msg):
            return
        state = get_state(msg.from_user.id)

        if state.mode == "awaiting_message_link":
            message_link = (msg.text or "").strip()
            chat_identifier, msg_id = parse_link(message_link)
            if chat_identifier is None or msg_id is None:
                await safe_reply_text(msg, "Invalid message link. Use https://t.me/<username>/<id> or https://t.me/c/<internal_id>/<id>.")
                return
            await handle_set_links(msg, state, state.target.group_link or "", message_link)
            if state.quick_start:
                state.mode = "awaiting_report_total"
                await safe_reply_text(
                    msg,
                    "Send the number of reports to file (positive integer).",
                )
            return

        if state.mode == "awaiting_report_type":
            value = (msg.text or "").strip().lower()
            if value in REASON_MAP:
                state.report.report_reason_key = value
                await safe_reply_text(
                    msg,
                    "Reason set. Provide number of reports with /set_total_reports or send a number now.",
                )
                state.mode = "awaiting_report_total"
            else:
                await safe_reply_text(msg, "Unknown reason. Choose from: " + ", ".join(REASON_MAP.keys()))
            return

        if state.mode == "awaiting_report_total":
            try:
                total = int((msg.text or "").strip())
                if total <= 0:
                    raise ValueError
            except ValueError:
                await safe_reply_text(msg, "Send a positive integer for the number of reports.")
                return
            state.report.report_total = total
            state.mode = "idle"
            await safe_reply_text(msg, f"Total reports set to {total}. Use /start_report to begin.")
            if state.quick_start:
                await run_reporting_flow(state, msg.chat.id if msg.chat else None, _)
            return

    await app.start()
    print("Moderator tool is running...")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
