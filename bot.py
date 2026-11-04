"""Rich Message format test board (single file).
/start -> heading + bordered boxes with a button inside each box + a button row (line breaks with <br/>)
/v2    -> same board, but line breaks written as real newlines (to see which one Telegram accepts)
REJECT / VERIFY flip a chat's status, REFRESH re-draws, CLOSE deletes the message.
If Telegram refuses the rich message, the bot sends you Telegram's exact error text.
"""
import asyncio
import html
import os
import time

import aiohttp
from aiohttp import web
from pyrogram import Client, filters, idle

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.getenv("PORT", 10000))  # Render sets PORT automatically
SELF_URL = os.getenv("RENDER_EXTERNAL_URL")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Client("board_test", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# demo data (in memory)
CHATS = [
    {"id": -1002337773145, "name": "FlickSafe | Movie Search Group", "verified": True},
    {"id": -1002127651368, "name": "New SS HD MOVIES", "verified": False},
]
BR = {"b": "<br/>", "n": "\n"}  # b = <br/> variant, n = newline variant


def btn(text, data, style=None):
    st = f' style="{style}"' if style else ""
    return f'<tg-button type="callback_data"{st} data="{data}">{html.escape(text)}</tg-button>'


def board(mode: str) -> str:
    out = ["<h1>📁 CHATS SAVED IN DB</h1>"]
    for i, c in enumerate(CHATS):
        status = "✅ VERIFIED" if c["verified"] else "❌ NOT VERIFIED"
        left = BR[mode].join([
            f"{i + 1}. 🎥 <b>{html.escape(c['name'])}</b>",
            f"🆔 {c['id']}",
            "⚙️ 🟢 ACTIVE",
            f"🔐 {status}",
        ])
        action = btn("REJECT", f"rej:{i}:{mode}", "danger") if c["verified"] else btn("VERIFY", f"ver:{i}:{mode}", "success")
        out.append(
            '<table bordered><tr>'
            f'<td valign="middle">{left}</td>'
            f'<td align="center" valign="middle">{action}</td>'
            '</tr></table>'
        )
    out.append(f"<p><i>updated {time.strftime('%H:%M:%S', time.gmtime())} UTC</i></p>")
    out.append(
    try:
        async with _session.post(f"{API}/{method}", json=payload) as r:
            return await r.json()
    except Exception as e:
        return {"ok": False, "description": str(e)}


async def send_board(client, chat_id: int, mode: str):
    res = await api("sendRichMessage", {"chat_id": chat_id, "rich_message": {"html": board(mode)}})
    if not res.get("ok"):
        await client.send_message(
            chat_id,
            "❌ <b>Telegram refused the rich message</b>\n"
            f"<code>{html.escape(str(res.get('description')))}</code>\n\nSend me this text.",
        )


@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await send_board(client, message.chat.id, "b")


@app.on_message(filters.command("v2") & filters.private)
async def v2(client, message):
    await send_board(client, message.chat.id, "n")


@app.on_callback_query()
async def on_click(client, query):
    parts = (query.data or "").split(":")
    action = parts[0]
    chat_id, msg_id = query.message.chat.id, query.message.id
    if action == "close":
        await query.message.delete()
        return
    mode = parts[-1] if parts[-1] in BR else "b"
    if action in ("rej", "ver"):
        CHATS[int(parts[1])]["verified"] = action == "ver"
    res = await api("editMessageText", {"chat_id": chat_id, "message_id": msg_id, "rich_message": {"html": board(mode)}})
    if res.get("ok"):
        await query.answer("Done ✅")
    else:
        await query.answer(str(res.get("description"))[:190], show_alert=True)


# ---------- web server for Render ----------
async def health(request):
    return web.Response(text="Board test bot is running ✅")


async def start_web():
    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"Web server on port {PORT}")


async def self_ping():
    if not SELF_URL:
        return
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        while True:
            await asyncio.sleep(600)
            try:
                await s.get(SELF_URL)
            except Exception:
                pass


async def main():
    await start_web()  # port first, so Render sees it open quickly
    await app.start()
    me = await app.get_me()
    print(f"Bot started as @{me.username}")
    asyncio.create_task(self_ping())
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
