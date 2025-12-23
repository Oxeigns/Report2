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
#   Telegram Auto Reporter v8.5 (Oxeigns)
# ======================================================
BANNER = r"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 🚨 Telegram Auto Reporter v8.5 (Oxeigns)                                  ║
║ Universal Link Fix | FloodWait Safe | Smart Target Resolver | Live Panel  ║
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

LOG_GROUP_LINK = "https://t.me/+bZAKT6wMT_gwZTFl"
LOG_GROUP_ID = -1003368489757

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
    """Initializes live status panel in log group."""
    global LIVE_PANEL_MSG_ID
    try:
        async with Client("logger", api_id=API_ID, api_hash=API_HASH, session_string=session_str) as app:
            try:
                chat = await app.get_chat(LOG_GROUP_LINK)
            except Exception:
                await app.join_chat(LOG_GROUP_LINK)
                chat = await app.get_chat(LOG_GROUP_LINK)

            chat_id = getattr(chat, "id", LOG_GROUP_ID)
            panel_text = (
                f"🎯 **Target Information (Loading...)**\n\n"
                f"📛 Name: {TARGET_INFO['name']}\n"
                f"👥 Members: {TARGET_INFO['members']}\n"
                f"🔗 Type: {TARGET_INFO['type']}\n"
                f"🧾 Link: {TARGET_INFO['link']}\n\n"
                f"📊 **Live Reporting Panel**\n\n"
                f"✅ Success: 0\n❌ Failed: 0\n🎯 Target: {NUMBER_OF_REPORTS}\n⚙️ Progress: 0%\n"
                f"🧾 Reason: {REPORT_TEXT}\n⏰ Updated: `{time.strftime('%H:%M:%S')}`"
            )
            msg = await app.send_message(chat_id, panel_text)
            LIVE_PANEL_MSG_ID = msg.id
            try:
                await app.pin_chat_message(chat_id, msg.id, disable_notification=True)
            except Exception:
                pass
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
# TARGET RESOLVER (fixes USERNAME_INVALID)
# ======================================================
async def resolve_target_chat(app: Client, link: str):
    """Resolve Telegram link to valid chat object."""
    link = link.strip()
    if link.startswith("https://t.me/"):
        link = link.replace("https://t.me/", "").replace("@", "").strip()

    chat = None
    try:
        if "+" in link:
            chat = await app.join_chat(f"https://t.me/{link}" if not link.startswith("https://t.me/") else link)
        else:
            chat = await app.get_chat(link)
    except errors.UsernameInvalid:
        log_console("⚠️ Retrying join_chat() for invalid username.", "WARN")
        try:
            chat = await app.join_chat(f"https://t.me/{link}")
        except Exception as e:
            log_console(f"⚠️ Second retry failed: {e}", "WARN")
    except errors.UserAlreadyParticipant:
        chat = await app.get_chat(link)
    except errors.FloodWait as e:
        log_console(f"⏳ FloodWait {e.value}s — waiting...", "WARN")
        await asyncio.sleep(e.value)
        chat = await app.get_chat(link)
    except Exception as e:
        log_console(f"⚠️ Fallback resolve_peer() for {link}: {e}", "WARN")
        try:
            peer = await app.resolve_peer(link)
            chat = await app.get_chat(peer)
        except Exception as ex:
            log_console(f"❌ Could not resolve {link}: {ex}", "ERR")
    return chat

# ======================================================
# FETCH TARGET INFO
# ======================================================
async def fetch_target_info(session_str: str):
    global TARGET_INFO
    try:
        async with Client("target_info", api_id=API_ID, api_hash=API_HASH, session_string=session_str) as app:
            chat = await resolve_target_chat(app, CHANNEL_LINK)
            if chat:
                TARGET_INFO["name"] = getattr(chat, "title", "Unknown")
                TARGET_INFO["type"] = getattr(chat, "type", "Unknown")
                try:
                    TARGET_INFO["members"] = await app.get_chat_members_count(chat.id)
                except Exception:
                    TARGET_INFO["members"] = getattr(chat, "members_count", 0)
                log_console(f"📊 Target Info Loaded: {TARGET_INFO}", "INFO")
            else:
                log_console("⚠️ Target chat could not be resolved.", "WARN")
    except Exception as e:
        log_console(f"⚠️ Failed to fetch target info: {e}", "WARN")

# ======================================================
# REPORT FUNCTION
# ======================================================
async def send_report(session_str: str, index: int, stats: dict):
    try:
        async with Client(f"reporter_{index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str) as app:
            chat = await resolve_target_chat(app, CHANNEL_LINK)
            if not chat:
                stats["failed"] += 1
                return
            msg_id = int(MESSAGE_LINK.split("/")[-1])
            msg = await app.get_messages(chat.id, msg_id)
            peer = await app.resolve_peer(chat.id)
            await asyncio.sleep(random.uniform(0.8, 1.5))
            await app.invoke(functions.messages.Report(peer=peer, id=[msg.id], reason=REASON, message=REPORT_TEXT))
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
    stats = {"success": 0, "failed": 0}

    valid_logger = next((s for s in SESSIONS if await validate_session(s)), None)
    if not valid_logger:
        print("❌ No valid sessions for logger.")
        return

    await fetch_target_info(valid_logger)
    asyncio.create_task(telegram_logger(valid_logger))
    await LOG_SENDER_READY.wait()
    log_console("🛰️ Log mirror started successfully.", "OK")

    valid_sessions = [s for s in SESSIONS if await validate_session(s)]
    if not valid_sessions:
        log_console("⚠️ No valid sessions remain.", "WARN")
        return

    async def live_panel_updater():
        async with Client("panel", api_id=API_ID, api_hash=API_HASH, session_string=valid_logger) as app:
            chat = await app.get_chat(LOG_GROUP_LINK)
            chat_id = getattr(chat, "id", LOG_GROUP_ID)
            while True:
                try:
                    progress = round((stats["success"] / max(1, NUMBER_OF_REPORTS)) * 100, 1)
                    text = (
                        f"🎯 **Target Info**\n"
                        f"📛 {TARGET_INFO['name']}\n"
                        f"👥 {TARGET_INFO['members']} members\n"
                        f"🔗 {TARGET_INFO['type']}\n\n"
                        f"📊 **Live Panel**\n"
                        f"✅ {stats['success']} success\n"
                        f"❌ {stats['failed']} failed\n"
                        f"🎯 Target: {NUMBER_OF_REPORTS}\n"
                        f"⚙️ {progress}% done\n"
                        f"🧾 {REPORT_TEXT}\n"
                        f"⏰ {time.strftime('%H:%M:%S')}"
                    )
                    await app.edit_message_text(chat_id, LIVE_PANEL_MSG_ID, text)
                except errors.FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass
                await asyncio.sleep(10)

    asyncio.create_task(live_panel_updater())

    i = 0
    while stats["success"] < NUMBER_OF_REPORTS:
        session = valid_sessions[i % len(valid_sessions)]
        await send_report(session, i + 1, stats)
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
