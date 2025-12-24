import os
import json
import asyncio
import random
import sys
import time
import traceback
from typing import List
from pyrogram import Client, errors
from pyrogram.raw import functions, types

# ======================================================
#   Telegram Auto Reporter v8.7 (Fixed Version)
# ======================================================
BANNER = r"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 🚨 Telegram Auto Reporter v8.7 (Fixed)                                     ║
║ Live Counter | Smart Resolver | FloodWait Safe | Clean Log Panel          ║
╚════════════════════════════════════════════════════════════════════════════╝
"""
print(BANNER)

# ================= CONFIG ===================
CONFIG_PATH = "config.json"
if not os.path.exists(CONFIG_PATH):
    print("❌ Missing config.json file.")
    sys.exit(1)

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

API_ID = int(os.getenv("API_ID", CONFIG["API_ID"]))
API_HASH = os.getenv("API_HASH", CONFIG["API_HASH"])
CHANNEL_LINK = os.getenv("CHANNEL_LINK", CONFIG["CHANNEL_LINK"])
MESSAGE_LINK = os.getenv("MESSAGE_LINK", CONFIG["MESSAGE_LINK"])
REPORT_TEXT = os.getenv("REPORT_TEXT", CONFIG["REPORT_TEXT"])
NUMBER_OF_REPORTS = int(os.getenv("NUMBER_OF_REPORTS", CONFIG["NUMBER_OF_REPORTS"]))

LOG_GROUP_LINK = "https://t.me/+yRPqAUQFvQ1lMzQ1"  # Replace with valid invite if expired
LOG_GROUP_ID = -1003371632666

SESSIONS = [v.strip() for k, v in os.environ.items() if k.startswith("SESSION_") and v.strip()]
if not SESSIONS:
    print("❌ No sessions found! Add SESSION_1, SESSION_2, etc. in Heroku Config Vars.")
    sys.exit(1)

LOG_SENDER_READY = asyncio.Event()
LIVE_PANEL_MSG_ID = None
TARGET_INFO = {"name": "Unknown", "members": 0, "type": "Unknown", "link": CHANNEL_LINK}

# ======================================================
# LOGGER SYSTEM
# ======================================================
async def telegram_logger(session_str: str):
    global LIVE_PANEL_MSG_ID
    try:
        async with Client("logger", api_id=API_ID, api_hash=API_HASH, session_string=session_str) as app:
            try:
                chat = await app.get_chat(LOG_GROUP_LINK)
            except Exception:
                await app.join_chat(LOG_GROUP_LINK)
                chat = await app.get_chat(LOG_GROUP_LINK)

            chat_id = getattr(chat, "id", LOG_GROUP_ID)
            msg = await app.send_message(
                chat_id,
                f"🛰️ **Initializing Live Report Panel...**\n\n🎯 Target: {CHANNEL_LINK}\n💬 Message: {MESSAGE_LINK}"
            )
            LIVE_PANEL_MSG_ID = msg.id
            LOG_SENDER_READY.set()
            while True:
                await asyncio.sleep(30)
    except Exception as e:
        print(f"[LOGGER_FATAL] {e}")

def log_console(msg: str, level="INFO"):
    colors = {"INFO": "\033[94m", "WARN": "\033[93m", "ERR": "\033[91m", "OK": "\033[92m"}
    print(f"{colors.get(level, '')}[{time.strftime('%H:%M:%S')}] {level}: {msg}\033[0m", flush=True)

# ======================================================
# REASON
# ======================================================
def get_reason():
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
        if str(CONFIG.get(key, False)).lower() == "true" or os.getenv(key, "false").lower() == "true":
            return cls()
    return types.InputReportReasonOther()
REASON = get_reason()

# ======================================================
# VALIDATION
# ======================================================
async def validate_session(session_str: str) -> bool:
    try:
        async with Client("check", api_id=API_ID, api_hash=API_HASH, session_string=session_str) as app:
            me = await app.get_me()
            log_console(f"✅ Valid session: {me.first_name} ({me.id})", "OK")
            return True
    except errors.AuthKeyUnregistered:
        log_console("❌ Invalid session — skipping permanently.", "WARN")
        return False
    except Exception as e:
        log_console(f"⚠️ Validation error: {e}", "WARN")
        return False

# ======================================================
# TARGET RESOLVER
# ======================================================
async def resolve_target_chat(app: Client, link: str):
    link = link.strip()
    if link.startswith("https://t.me/"):
        link = link.replace("https://t.me/", "").replace("@", "").strip()

    try:
        if "+" in link:
            return await app.join_chat(f"https://t.me/{link}")
        return await app.get_chat(link)
    except errors.UsernameInvalid:
        try:
            return await app.join_chat(f"https://t.me/{link}")
        except Exception:
            return None
    except errors.UserAlreadyParticipant:
        return await app.get_chat(link)
    except errors.FloodWait as e:
        log_console(f"⏳ FloodWait {e.value}s — waiting...", "WARN")
        await asyncio.sleep(e.value)
        return await app.get_chat(link)
    except Exception as e:
        log_console(f"❌ Could not resolve target: {e}", "ERR")
        return None

# ======================================================
# REPORT FUNCTION (FIXED)
# ======================================================
async def send_report(session_str: str, index: int, stats: dict):
    try:
        async with Client(f"reporter_{index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str) as app:
            chat = await resolve_target_chat(app, CHANNEL_LINK)
            if not chat:
                stats["failed"] += 1
                log_console(f"❌ Could not resolve target chat for session {index}.", "ERR")
                return

            msg_id = int(MESSAGE_LINK.split("/")[-1])
            msg = await app.get_messages(chat.id, msg_id)

            if not hasattr(chat, "access_hash"):
                chat = await app.get_chat(chat.id)

            try:
                peer = types.InputPeerChannel(
                    channel_id=chat.id if isinstance(chat.id, int) else chat.chat.id,
                    access_hash=chat.access_hash
                )
            except Exception as e:
                log_console(f"❌ Failed to construct InputPeerChannel: {e}", "ERR")
                stats["failed"] += 1
                return

            await asyncio.sleep(random.uniform(0.8, 1.5))
            await app.invoke(functions.messages.Report(
                peer=peer,
                id=[msg.id],
                reason=REASON,
                message=REPORT_TEXT
            ))
            stats["success"] += 1
            log_console(f"✅ Report #{stats['success']} sent (Session {index})", "OK")

    except errors.FloodWait as e:
        log_console(f"⚠️ FloodWait {e.value}s in Session {index}", "WARN")
        await asyncio.sleep(e.value)
    except Exception as e:
        stats["failed"] += 1
        log_console(f"❌ Error in Session {index}: {e}", "ERR")

# ======================================================
# MAIN
# ======================================================
async def main():
    global LIVE_PANEL_MSG_ID
    stats = {"success": 0, "failed": 0, "sent": 0}

    valid_logger = None
    for s in SESSIONS:
        if await validate_session(s):
            valid_logger = s
            break
    if not valid_logger:
        print("❌ No valid sessions for logger.")
        return

    asyncio.create_task(telegram_logger(valid_logger))
    await LOG_SENDER_READY.wait()
    log_console("🛰️ Log mirror started successfully.", "OK")

    valid_sessions = [s for s in SESSIONS if await validate_session(s)]
    if not valid_sessions:
        log_console("⚠️ No valid sessions remain.", "WARN")
        return

    async def live_panel():
        async with Client("panel", api_id=API_ID, api_hash=API_HASH, session_string=valid_logger) as app:
            chat = await app.get_chat(LOG_GROUP_LINK)
            chat_id = getattr(chat, "id", LOG_GROUP_ID)
            while True:
                try:
                    progress = round((stats["sent"] / max(1, NUMBER_OF_REPORTS)) * 100, 1)
                    text = (
                        f"📊 **Live Reporting Panel**\n\n"
                        f"🎯 **Target:** {CHANNEL_LINK}\n"
                        f"💬 **Message:** {MESSAGE_LINK}\n\n"
                        f"✅ Success: {stats['success']}\n"
                        f"❌ Failed: {stats['failed']}\n"
                        f"📨 Sent: {stats['sent']} / {NUMBER_OF_REPORTS}\n"
                        f"⚙️ Progress: {progress}%\n\n"
                        f"🧾 Reason: {REPORT_TEXT}\n"
                        f"⏰ Updated: `{time.strftime('%H:%M:%S')}`"
                    )
                    await app.edit_message_text(chat_id, LIVE_PANEL_MSG_ID, text)
                except errors.FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass
                await asyncio.sleep(10)

    asyncio.create_task(live_panel())

    i = 0
    while stats["sent"] < NUMBER_OF_REPORTS:
        session = valid_sessions[i % len(valid_sessions)]
        await send_report(session, i + 1, stats)
        stats["sent"] += 1
        i += 1
        await asyncio.sleep(random.uniform(1.0, 2.0))

    log_console("✅ All reports completed successfully.", "OK")
    while True:
        await asyncio.sleep(60)

# ======================================================
# CRASH HANDLER
# ======================================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        crash_trace = traceback.format_exc()
        crash_msg = f"💥 Crash Detected:\n`{type(e).__name__}` — {e}\n\n```{crash_trace}```"
        print(crash_msg)
