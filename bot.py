"""Post Maker Bot (single file): build a Rich Message post block by block and send it to your channel.

Blocks: Heading, Text, Quote, Table (text on the left + a link button on the right), Buttons rows, Divider.
Everything is rich-button driven. Admin only. Drafts live in memory (a redeploy clears them).
"""
import asyncio
import html
import os
import re

import aiohttp
from aiohttp import web
from pyrogram import Client, filters, idle

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMINS = [int(x) for x in os.getenv("ADMINS", "").replace(",", " ").split()]
DEFAULT_CHANNEL = os.getenv("CHANNEL_ID", "").strip()  # optional: channel every new draft starts with
LINEBREAK = os.getenv("LINEBREAK", "br")  # br = <br/> inside table cells, nl = a real newline
PORT = int(os.getenv("PORT", 10000))  # Render sets PORT automatically
SELF_URL = os.getenv("RENDER_EXTERNAL_URL")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_BLOCKS = 40

app = Client("post_maker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
S = {}  # admin id -> {"blocks", "channel", "title", "await", "panel", "preview"}

COLORS = {"blue": "primary", "primary": "primary", "green": "success", "success": "success",
          "red": "danger", "danger": "danger", "": None, "none": None, "default": None, "-": None}
ICON = {"h": "🔠", "p": "📝", "q": "💬", "t": "▦", "b": "🔘", "hr": "➖"}
NAME = {"h": "Heading", "p": "Text", "q": "Quote", "t": "Table", "b": "Buttons", "hr": "Divider"}


# ---------------------------------------------------------------- text -> rich html
esc = html.escape
_TAGS = re.compile(r"&lt;(/?)(b|i|u|s|code|mark|tg-spoiler)&gt;")
_A_OPEN = re.compile(r"&lt;a href=&quot;([^&\"]*?)&quot;&gt;")
_EMOJI = re.compile(r"&lt;tg-emoji emoji-id=&quot;(\d+)&quot;&gt;(.*?)&lt;/tg-emoji&gt;")
_BR = "<br/>" if LINEBREAK == "br" else "\n"


def fmt(text: str) -> str:
    """Escape everything, then allow only: b i u s code mark tg-spoiler, <a href>, tg-emoji, and ' // ' = new line."""
    s = esc(text)
    s = _TAGS.sub(r"<\1\2>", s)
    s = _A_OPEN.sub(r'<a href="\1">', s).replace("&lt;/a&gt;", "</a>")
    s = _EMOJI.sub(r'<tg-emoji emoji-id="\1">\2</tg-emoji>', s)
    return re.sub(r"\s+//\s+", _BR, s.replace("\\n", " // "))


def btn_html(b: dict) -> str:
    st = f' style="{b["style"]}"' if b.get("style") else ""
    return f'<tg-button type="url"{st} url="{esc(b["url"], quote=True)}">{esc(b["name"])}</tg-button>'


def cb(text: str, data: str, style: str = "primary") -> str:
    st = f' style="{style}"' if style else ""
    return f'<tg-button type="callback_data"{st} data="{data}">{esc(text)}</tg-button>'


def row(*buttons: str) -> str:
    return "<tg-button-row>" + "".join(buttons) + "</tg-button-row>"


def render(blocks: list) -> str:
    """The post, exactly as it is sent to the channel."""
    out = []
    for b in blocks:
        t = b["t"]
        if t == "h":
            out.append(f"<h{b['size']}>{fmt(b['text'])}</h{b['size']}>")
        elif t == "p":
            out += [f"<p>{fmt(line)}</p>" for line in b["text"].split("\n") if line.strip()]
        elif t == "q":
            out.append(f"<blockquote>{fmt(b['text'])}</blockquote>")
        elif t == "hr":
            out.append("<hr/>")
        elif t == "t":
            rows = ""
            for r in b["rows"]:
                right = btn_html(r["btn"]) if r["btn"] else ""
                rows += f'<tr><td valign="middle">{fmt(r["text"])}</td><td align="center" valign="middle">{right}</td></tr>'
            out.append(f"<table bordered>{rows}</table>")
        elif t == "b":
            out += [row(*[btn_html(x) for x in line]) for line in b["lines"]]
    return "".join(out)


# ---------------------------------------------------------------- parsing what the admin types
def parse_url(u: str):
    u = u.strip()
    if re.match(r"^(https?://|tg://)\S+$", u):
        return u
    if re.match(r"^(t|telegram)\.me/\S+$", u):
        return "https://" + u
    if re.fullmatch(r"@\w{4,}", u):
        return "https://t.me/" + u[1:]
    return None


def parse_button(name: str, url: str, color: str = ""):
    if not name.strip():
        return None, "A button needs a name."
    link = parse_url(url)
    if not link:
        return None, f"“{esc(url[:40])}” is not a valid link. Use https://… or t.me/… or @username."
    if color.strip().lower() not in COLORS:
        return None, "Color must be blue, green, red or none."
    return {"name": name.strip()[:60], "url": link, "style": COLORS[color.strip().lower()]}, None


def parse_block(kind: str, raw: str):
    """-> (block, None) or (None, error text)"""
    raw = raw.strip()
    if not raw:
        return None, "Send some text."
    if kind == "h":
        m = re.match(r"^([1-6]):\s*(.+)$", raw, re.S)
        size, text = (int(m.group(1)), m.group(2).strip()) if m else (1, raw)
        return {"t": "h", "size": size, "text": text, "raw": raw}, None
    if kind in ("p", "q"):
        return {"t": kind, "text": raw, "raw": raw}, None
    if kind == "t":
        rows = []
        for n, line in enumerate([x for x in raw.split("\n") if x.strip()], 1):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) == 1:
                rows.append({"text": parts[0], "btn": None})
                continue
            if len(parts) < 3:
                return None, f"Row {n}: write <code>Text | Button name | Link | color</code> (or only the text)."
            b, err = parse_button(parts[1], parts[2], parts[3] if len(parts) > 3 else "")
            if err:
                return None, f"Row {n}: {err}"
            rows.append({"text": parts[0], "btn": b})
        if len(rows) > 30:
            return None, "A table can have at most 30 rows."
        return {"t": "t", "rows": rows, "raw": raw}, None
    if kind == "b":
        lines = []
        for n, group in enumerate([g for g in re.split(r"\n\s*\n", raw) if g.strip()], 1):
            line = []
            for x in [y for y in group.split("\n") if y.strip()]:
                parts = [p.strip() for p in x.split("|")]
                if len(parts) < 2:
                    return None, f"Row {n}: write <code>Name | Link | color</code>."
                b, err = parse_button(parts[0], parts[1], parts[2] if len(parts) > 2 else "")
                if err:
                    return None, f"Row {n}: {err}"
                line.append(b)
            if len(line) > 8:
                return None, f"Row {n}: at most 8 buttons in one row."
            lines.append(line)
        return {"t": "b", "lines": lines, "raw": raw}, None
    return None, "Unknown block."


# ---------------------------------------------------------------- Telegram calls
_session = None


async def api(method: str, payload: dict) -> dict:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
    try:
        async with _session.post(f"{API}/{method}", json=payload) as r:
            return await r.json()
    except Exception as e:
        return {"ok": False, "description": str(e)}


def state(uid: int) -> dict:
    if uid not in S:
        ch = int(DEFAULT_CHANNEL) if re.fullmatch(r"-?\d+", DEFAULT_CHANNEL) else (DEFAULT_CHANNEL or None)
        S[uid] = {"blocks": [], "channel": ch, "title": "", "await": None, "panel": None, "preview": None}
    return S[uid]


async def show(client, st: dict, body: str, rows: list = None, chat_id: int = None):
    """Draw a panel: edit the panel message in place, or send a new one."""
    html_ = body + "".join(rows or [])
    if st["panel"] and chat_id is None:
        c, m = st["panel"]
        res = await api("editMessageText", {"chat_id": c, "message_id": m, "rich_message": {"html": html_}})
        if res.get("ok") or "not modified" in str(res.get("description", "")).lower():
            return
        chat_id = c
    res = await api("sendRichMessage", {"chat_id": chat_id, "rich_message": {"html": html_}})
    if res.get("ok"):
        st["panel"] = (chat_id, res["result"]["message_id"])
    else:
        await client.send_message(chat_id, f"❌ Telegram refused the rich message:\n<code>{esc(str(res.get('description')))}</code>")


# ---------------------------------------------------------------- the panels
def P(text: str) -> str:
    return f"<p>{text}</p>"


def main_view(st: dict, note: str = ""):
    ch = st["channel"]
    chan = f"<b>{esc(st['title'])}</b> <code>{ch}</code>" if ch and st["title"] else (f"<code>{ch}</code>" if ch else "<i>not set</i>")
    body = "<h2>🛠 POST MAKER</h2>" + (P(note) if note else "") + P(f"📡 <b>Channel:</b> {chan}") + P(f"🧱 <b>Blocks:</b> {len(st['blocks'])}")
    rows = [
        row(cb("🔠 Heading", "add:h"), cb("📝 Text", "add:p"), cb("💬 Quote", "add:q")),
        row(cb("▦ Table", "add:t"), cb("🔘 Buttons", "add:b"), cb("➖ Divider", "add:hr")),
        row(cb("🧱 Edit blocks", "blocks"), cb("📡 Channel", "chan")),
        row(cb("👁 Preview", "prev"), cb("🚀 Send to channel", "send", "success")),
        row(cb("🗑 Clear all", "clear", "danger")),
    ]
    return body, rows


def summary(b: dict) -> str:
    t = b["t"]
    if t in ("h", "p", "q"):
        extra = re.sub(r"\s+", " ", b["text"])[:30]
    elif t == "t":
        extra = f"{len(b['rows'])} row(s)"
    elif t == "b":
        extra = f"{sum(len(x) for x in b['lines'])} button(s)"
    else:
        extra = ""
    return f"{ICON[t]} {NAME[t]}" + (f": {esc(extra)}" if extra else "")


def blocks_view(st: dict):
    body = "<h2>🧱 BLOCKS</h2>"
    if not st["blocks"]:
        body += P("No blocks yet. Add one from the main panel.")
    rows = []
    for i, b in enumerate(st["blocks"]):
        body += P(f"<b>{i + 1}.</b> {summary(b)}")
        rows.append(row(cb(f"✏️ {i + 1}", f"ed:{i}"), cb("⬆️", f"mv:{i}:-1"), cb("⬇️", f"mv:{i}:1"), cb("🗑", f"rm:{i}", "danger")))
    rows.append(row(cb("⬅️ Back", "back")))
    return body, rows


HELP = {
    "h": "Send the heading text.\nSize 1 (biggest) to 6: start with the number and a colon, e.g. <code>2: My title</code>. Default is 1.",
    "p": "Send the text. Every new line becomes a new paragraph.\nYou can use <code>&lt;b&gt;bold&lt;/b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>, <code>&lt;a href=\"https://…\"&gt;link&lt;/a&gt;</code>.",
    "q": "Send the quote text.",
    "t": "One row per line, left = text, right = button:\n<code>Text | Button name | Link | color</code>\n◉ <code> // </code> (with spaces) = new line inside the text\n◉ color: blue, green, red (optional)\n◉ Only text, no button? just write the text\nExample:\n<code>1. My Channel // ID -100123 | OPEN | https://t.me/mychannel | green</code>",
    "b": "One button per line: <code>Name | Link | color</code>\n◉ Up to 8 buttons in a row\n◉ A blank line starts a new row\nExample:\n<code>Join | https://t.me/mychannel | green\nHelp | https://t.me/helpbot</code>",
    "chan": "Send the channel ID like <code>-1001234567890</code> or <code>@channelname</code>.\nThe bot must be an <b>admin</b> of that channel with the right to post.",
}


def prompt_view(kind: str, current: str = ""):
    title = "📡 Channel" if kind == "chan" else f"{ICON[kind]} {NAME[kind]}"
    body = f"<h2>{title}</h2>" + "".join(P(line) for line in HELP[kind].split("\n"))
    if current:
        body += P("<b>Current:</b>") + f"<pre>{esc(current)}</pre>"
    body += P("👇 <i>Send it now</i>")
    return body, [row(cb("❌ Cancel", "cancel", "danger"))]


# ---------------------------------------------------------------- admin guard + commands
def is_admin(uid: int) -> bool:
    return uid in ADMINS


async def open_panel(client, uid: int, chat_id: int, note: str = ""):
    st = state(uid)
    st["await"] = None
    if st["panel"]:
        try:
            await client.delete_messages(*st["panel"])
        except Exception:
            pass
        st["panel"] = None
    body, rows = main_view(st, note)
    await show(client, st, body, rows, chat_id=chat_id)


@app.on_message(filters.command(["start", "new"]) & filters.private)
async def start(client, message):
    uid = message.from_user.id
    if not is_admin(uid):
        return await message.reply_text("🔒 This bot is private.")
    if message.command[0] == "new":
        state(uid)["blocks"] = []
    await open_panel(client, uid, message.chat.id, "🆕 New post" if message.command[0] == "new" else "")


async def set_channel(client, st: dict, raw: str) -> str:
    raw = raw.strip()
    if re.fullmatch(r"-?\d+", raw):
        ref = int(raw)
    elif re.fullmatch(r"@?\w{4,}", raw):
        ref = "@" + raw.lstrip("@")
    else:
        return "❌ Send a channel ID like -1001234567890 or @channelname."
    try:
        chat = await client.get_chat(ref)
    except Exception as e:
        return f"❌ Can't open that chat: <code>{esc(str(e)[:90])}</code>"
    st["channel"], st["title"] = ref, chat.title or ""
    return f"✅ Channel set: <b>{esc(chat.title or str(ref))}</b>"


@app.on_message(filters.command("channel") & filters.private)
async def channel_cmd(client, message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    st = state(uid)
    note = await set_channel(client, st, message.command[1]) if len(message.command) > 1 else ""
    await open_panel(client, uid, message.chat.id, note)


@app.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
async def on_text(client, message):
    uid = message.from_user.id
    st = S.get(uid)
    if not is_admin(uid) or not st or not st["await"]:
        return
    aw, raw = st["await"], message.text
    try:
        await message.delete()
    except Exception:
        pass
    if aw["kind"] == "chan":
        note = await set_channel(client, st, raw)
        if note.startswith("✅"):
            st["await"] = None
            return await show(client, st, *main_view(st, note))
        body, rows = prompt_view("chan")
        return await show(client, st, P(note) + body, rows)
    block, err = parse_block(aw["kind"], raw)
    if err:
        body, rows = prompt_view(aw["kind"], aw.get("current", ""))
        return await show(client, st, P(f"❌ {err}") + body, rows)
    if aw.get("replace") is not None and aw["replace"] < len(st["blocks"]):
        st["blocks"][aw["replace"]] = block
        note = "✅ Block updated"
    else:
        st["blocks"].append(block)
        note = f"✅ {NAME[block['t']]} added"
    st["await"] = None
    await show(client, st, *main_view(st, note))


# ---------------------------------------------------------------- buttons
def chan_link(ref, mid: int) -> str:
    if isinstance(ref, str):
        return f"https://t.me/{ref.lstrip('@')}/{mid}"
    return f"https://t.me/c/{str(ref).replace('-100', '', 1)}/{mid}"


@app.on_callback_query()
async def on_click(client, query):
    uid = query.from_user.id
    if not is_admin(uid):
        return await query.answer("Admins only", show_alert=True)
    st = state(uid)
    st["panel"] = (query.message.chat.id, query.message.id)
    d = query.data or ""
    toast = None

    if d.startswith("add:"):
        kind = d[4:]
        if kind == "hr":
            if len(st["blocks"]) >= MAX_BLOCKS:
                toast = "Too many blocks"
            else:
                st["blocks"].append({"t": "hr", "raw": ""})
                await show(client, st, *main_view(st, "✅ Divider added"))
        else:
            st["await"] = {"kind": kind, "replace": None}
            await show(client, st, *prompt_view(kind))
    elif d == "chan":
        st["await"] = {"kind": "chan"}
        await show(client, st, *prompt_view("chan"))
    elif d in ("back", "cancel"):
        st["await"] = None
        await show(client, st, *main_view(st))
    elif d == "blocks":
        await show(client, st, *blocks_view(st))
    elif d.startswith("ed:"):
        i = int(d[3:])
        b = st["blocks"][i] if i < len(st["blocks"]) else None
        if not b:
            toast = "Block not found"
        elif b["t"] == "hr":
            toast = "A divider has nothing to edit"
        else:
            st["await"] = {"kind": b["t"], "replace": i, "current": b["raw"]}
            await show(client, st, *prompt_view(b["t"], b["raw"]))
    elif d.startswith("mv:"):
        _, i, step = d.split(":")
        i, j = int(i), int(i) + int(step)
        if 0 <= i < len(st["blocks"]) and 0 <= j < len(st["blocks"]):
            st["blocks"][i], st["blocks"][j] = st["blocks"][j], st["blocks"][i]
        await show(client, st, *blocks_view(st))
    elif d.startswith("rm:"):
        i = int(d[3:])
        if i < len(st["blocks"]):
            st["blocks"].pop(i)
        await show(client, st, *blocks_view(st))
    elif d == "clear":
        await show(client, st, "<h2>🗑 Clear all?</h2>" + P("This removes every block of the draft."),
                   [row(cb("✅ Yes, clear", "clear_yes", "danger"), cb("❌ No", "back"))])
    elif d == "clear_yes":
        st["blocks"] = []
        await show(client, st, *main_view(st, "🗑 Cleared"))
    elif d == "prev":
        if not st["blocks"]:
            toast = "Add a block first"
        else:
            if st["preview"]:
                try:
                    await client.delete_messages(*st["preview"])
                except Exception:
                    pass
            res = await api("sendRichMessage", {"chat_id": query.message.chat.id, "rich_message": {"html": render(st["blocks"])}})
            if res.get("ok"):
                st["preview"] = (query.message.chat.id, res["result"]["message_id"])
                toast = "Preview sent ⬆️"
            else:
                await show(client, st, *main_view(st, f"❌ Preview failed: <code>{esc(str(res.get('description')))}</code>"))
    elif d == "send":
        if not st["channel"]:
            await show(client, st, *main_view(st, "⚠️ Set the channel first"))
        elif not st["blocks"]:
            await show(client, st, *main_view(st, "⚠️ Add a block first"))
        else:
            await show(client, st, "<h2>🚀 Post now?</h2>" + P(f"Channel: <code>{st['channel']}</code>") + P(f"Blocks: {len(st['blocks'])}"),
                       [row(cb("✅ Yes, post", "send_yes", "success"), cb("❌ Cancel", "back", "danger"))])
    elif d == "send_yes":
        res = await api("sendRichMessage", {"chat_id": st["channel"], "rich_message": {"html": render(st["blocks"])}})
        if res.get("ok"):
            link = chan_link(st["channel"], res["result"]["message_id"])
            await show(client, st, *main_view(st, f'✅ <b>Posted!</b> <a href="{esc(link, quote=True)}">open the post</a>'))
        else:
            await show(client, st, *main_view(st, f"❌ Telegram: <code>{esc(str(res.get('description')))}</code>"))
    await query.answer(toast) if toast else await query.answer()


# ---------------------------------------------------------------- web server for Render
async def health(request):
    return web.Response(text="Post maker bot is running ✅")


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
    print(f"Bot started as @{me.username}; admins: {ADMINS or 'NONE - set ADMINS'}")
    asyncio.create_task(self_ping())
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
