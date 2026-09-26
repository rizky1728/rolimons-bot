import os
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict

import aiohttp
import aiosqlite
import discord
from discord import app_commands, Webhook
from discord.ext import commands
from dotenv import load_dotenv
from rapidfuzz import fuzz, process

from panel import register_panel

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
ROLIMONS_BASE = "https://api.rolimons.com"
DB_PATH = "bot_config.db"


# ============================================================
# CACHE
# ============================================================
class Cache:
    def __init__(self):
        self._store: Dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    async def get(self, key):
        async with self._lock:
            e = self._store.get(key)
            if not e:
                return None
            v, exp = e
            if time.time() > exp:
                del self._store[key]
                return None
            return v

    async def set(self, key, value, ttl=300):
        async with self._lock:
            self._store[key] = (value, time.time() + ttl)


cache = Cache()


# ============================================================
# DB
# ============================================================
class DB:
    def __init__(self):
        self.conn: Optional[aiosqlite.Connection] = None

    async def init(self):
        self.conn = await aiosqlite.connect(DB_PATH)
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS newitem_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                filter_type TEXT,
                filter_value TEXT,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER,
                UNIQUE(guild_id, target_type, target_id)
            );
            CREATE TABLE IF NOT EXISTS watch_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                interval_min INTEGER DEFAULT 15,
                last_value INTEGER,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER,
                UNIQUE(guild_id, target_type, target_id, item_id)
            );
            CREATE TABLE IF NOT EXISTS seen_items (
                item_id INTEGER PRIMARY KEY,
                first_seen INTEGER
            );
        """)
        await self.conn.commit()

    async def add_newitem_sub(self, g, tt, ti, ft="any", fv=None):
        await self.conn.execute(
            "INSERT OR REPLACE INTO newitem_subs (guild_id,target_type,target_id,filter_type,filter_value,created_at) VALUES (?,?,?,?,?,?)",
            (g, tt, ti, ft, fv, int(time.time())),
        )
        await self.conn.commit()

    async def remove_newitem_sub(self, g, tt, ti):
        await self.conn.execute(
            "DELETE FROM newitem_subs WHERE guild_id=? AND target_type=? AND target_id=?",
            (g, tt, ti),
        )
        await self.conn.commit()

    async def list_newitem_subs(self, g=None):
        if g:
            cur = await self.conn.execute(
                "SELECT * FROM newitem_subs WHERE guild_id=? AND enabled=1", (g,)
            )
        else:
            cur = await self.conn.execute("SELECT * FROM newitem_subs WHERE enabled=1")
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    async def add_watch(self, g, tt, ti, iid, interval=15):
        await self.conn.execute(
            "INSERT OR REPLACE INTO watch_subs (guild_id,target_type,target_id,item_id,interval_min,created_at) VALUES (?,?,?,?,?,?)",
            (g, tt, ti, iid, interval, int(time.time())),
        )
        await self.conn.commit()

    async def remove_watch(self, g, tt, ti, iid=None):
        if iid:
            await self.conn.execute(
                "DELETE FROM watch_subs WHERE guild_id=? AND target_type=? AND target_id=? AND item_id=?",
                (g, tt, ti, iid),
            )
        else:
            await self.conn.execute(
                "DELETE FROM watch_subs WHERE guild_id=? AND target_type=? AND target_id=?",
                (g, tt, ti),
            )
        await self.conn.commit()

    async def list_watches(self, g=None):
        if g:
            cur = await self.conn.execute(
                "SELECT * FROM watch_subs WHERE guild_id=? AND enabled=1", (g,)
            )
        else:
            cur = await self.conn.execute("SELECT * FROM watch_subs WHERE enabled=1")
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    async def update_last_value(self, wid, v):
        await self.conn.execute("UPDATE watch_subs SET last_value=? WHERE id=?", (v, wid))
        await self.conn.commit()

    async def is_seen(self, iid):
        cur = await self.conn.execute("SELECT 1 FROM seen_items WHERE item_id=?", (iid,))
        return await cur.fetchone() is not None

    async def mark_seen(self, iid):
        await self.conn.execute(
            "INSERT OR IGNORE INTO seen_items (item_id, first_seen) VALUES (?,?)",
            (iid, int(time.time())),
        )
        await self.conn.commit()

    async def count_seen(self):
        cur = await self.conn.execute("SELECT COUNT(*) FROM seen_items")
        return (await cur.fetchone())[0]


db = DB()


# ============================================================
# ROLIMONS API
# ============================================================
class RolimonsAPI:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._items_index: Dict[int, str] = {}
        self._items_meta: Dict[int, dict] = {}
        self._name_index: List[str] = []

    async def start(self):
        if not self.session:
            self.session = aiohttp.ClientSession(headers={"User-Agent": "RolimonsBot/1.0"})

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _get(self, path, ttl=300):
        key = f"rl:{path}"
        c = await cache.get(key)
        if c is not None:
            return c
        await self.start()
        try:
            async with self.session.get(f"{ROLIMONS_BASE}{path}", timeout=15) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                await cache.set(key, data, ttl)
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"[API ERR] {path}: {e}")
            return None

    async def load_items_index(self):
        data = await self._get("/items/v1/allitems", 1800)
        if not data:
            return False
        items = data.get("items", [])
        self._items_index.clear()
        self._items_meta.clear()
        self._name_index.clear()
        for it in items:
            try:
                iid = it[0]
                name = it[1] or ""
                self._items_index[iid] = name
                self._items_meta[iid] = {
                    "id": iid, "name": name,
                    "value": it[3] if len(it) > 3 else None,
                    "rap": it[4] if len(it) > 4 else None,
                    "demand": it[5] if len(it) > 5 else None,
                    "trend": it[6] if len(it) > 6 else None,
                    "projected": it[7] if len(it) > 7 else None,
                    "rare": bool(it[8]) if len(it) > 8 else False,
                }
                if name:
                    self._name_index.append(name)
            except (IndexError, TypeError):
                continue
        return True

    async def search_items(self, q, limit=25):
        if not self._name_index:
            await self.load_items_index()
        if not self._name_index:
            return []
        ql = q.strip().lower()
        exact = [n for n in self._name_index if n.lower() == ql]
        if exact:
            iid = self._name_to_id(exact[0])
            return [self._items_meta[iid]] if iid else []
        results = process.extract(q, self._name_index, scorer=fuzz.WRatio, limit=limit, score_cutoff=55)
        out = []
        for name, score, _ in results:
            iid = self._name_to_id(name)
            if iid and iid in self._items_meta:
                m = dict(self._items_meta[iid]); m["_score"] = score
                out.append(m)
        return out

    def _name_to_id(self, name):
        for iid, n in self._items_index.items():
            if n == name:
                return iid
        return None

    def get_meta(self, iid):
        return self._items_meta.get(iid)

    async def get_item_value(self, iid):
        return await self._get(f"/items/v1/value/{iid}")

    async def get_item_details(self, iid):
        return await self._get(f"/items/v1/details/{iid}")

    async def get_all_raw(self):
        return await self._get("/items/v1/allitems", 1800)

    async def get_player(self, uid):
        return await self._get(f"/players/v1/player/{uid}")

    async def get_player_owned(self, uid):
        return await self._get(f"/players/v1/owneditems/{uid}")

    async def get_deals(self):
        return await self._get("/deals/v1/deals", 120)


api = RolimonsAPI()


# ============================================================
# FORMATTERS
# ============================================================
def fmt(n):
    if n is None:
        return "N/A"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def trend_e(t):
    return {"up": "📈", "down": "📉", "stable": "➡️"}.get(t, "➡️")


def demand_bar(d):
    if d is None:
        return "N/A"
    d = max(0, min(5, int(d)))
    return "🟩" * d + "⬜" * (5 - d)


def item_embed(meta, value=None, details=None):
    name = meta.get("name", "Unknown")
    iid = meta.get("id")
    thumb = (details or {}).get("thumbnail") or meta.get("thumbnail")
    e = discord.Embed(
        title=name,
        url=f"https://www.rolimons.com/item/{iid}",
        color=0x00A8FF,
        timestamp=datetime.now(timezone.utc),
    )
    if thumb:
        e.set_thumbnail(url=thumb)
    val = (value or {}).get("value", meta.get("value"))
    rap = (value or {}).get("rap", meta.get("rap"))
    dem = (value or {}).get("demand", meta.get("demand"))
    tr = (value or {}).get("trend", meta.get("trend"))
    proj = (value or {}).get("projected", meta.get("projected"))
    rare = (value or {}).get("rare", meta.get("rare"))
    e.add_field(name="💰 Value", value=fmt(val), inline=True)
    e.add_field(name="📊 RAP", value=fmt(rap), inline=True)
    e.add_field(name="🔥 Demand", value=demand_bar(dem), inline=True)
    e.add_field(name="📈 Trend", value=f"{trend_e(tr)} {tr or 'N/A'}", inline=True)
    e.add_field(name="🔮 Projected", value=str(proj or "N/A"), inline=True)
    e.add_field(name="💎 Rare", value="✅" if rare else "❌", inline=True)
    e.set_footer(text=f"Item ID: {iid} · Rolimons")
    return e


def new_item_embed(meta):
    e = discord.Embed(
        title=f"🆕 {meta.get('name', 'Unknown')}",
        url=f"https://www.rolimons.com/item/{meta.get('id')}",
        color=0x00FF88,
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="💰 Value", value=fmt(meta.get("value")), inline=True)
    e.add_field(name="📊 RAP", value=fmt(meta.get("rap")), inline=True)
    e.add_field(name="🔥 Demand", value=demand_bar(meta.get("demand")), inline=True)
    e.add_field(name="💎 Rare", value="✅" if meta.get("rare") else "❌", inline=True)
    e.set_footer(text=f"Item ID: {meta.get('id')} · Item baru rilis")
    return e


# ============================================================
# SEND
# ============================================================
async def send_to(bot, tt, ti, embed):
    try:
        if tt == "channel":
            ch = bot.get_channel(int(ti))
            if ch:
                await ch.send(embed=embed)
                return True
        elif tt == "webhook":
            async with aiohttp.ClientSession() as s:
                wh = Webhook.from_url(ti, session=s)
                await wh.send(embed=embed)
                return True
    except Exception as e:
        print(f"[SEND ERR] {tt}:{ti} → {e}")
    return False


# ============================================================
# BOT
# ============================================================
class RolimonsBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.poll_task: Optional[asyncio.Task] = None

    async def setup_hook(self):
        await api.start()
        await db.init()
        register_panel(self, db, api)
        asyncio.create_task(self._preload())
        self.poll_task = asyncio.create_task(self._scheduler())
        await self.tree.sync()
        print("[OK] Ready")

    async def _preload(self):
        ok = await api.load_items_index()
        print(f"[OK] Items: {len(api._items_index)} (loaded={ok})")
        count = await db.count_seen()
        if count == 0 and api._items_meta:
            print("[INIT] Seeding seen items...")
            for iid in api._items_meta.keys():
                await db.mark_seen(iid)

    async def _scheduler(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._check_new()
                await self._check_watches()
            except Exception as e:
                print(f"[SCHED ERR] {e}")
            await asyncio.sleep(60)

    async def _check_new(self):
        data = await api.get_all_raw()
        if not data:
            return
        items = data.get("items", [])
        subs = await db.list_newitem_subs()
        if not subs:
            for it in items:
                try:
                    await db.mark_seen(it[0])
                except Exception:
                    continue
            return
        new = []
        for it in items:
            try:
                iid = it[0]
                if not await db.is_seen(iid):
                    new.append({
                        "id": iid,
                        "name": it[1] or "",
                        "value": it[3] if len(it) > 3 else None,
                        "rap": it[4] if len(it) > 4 else None,
                        "demand": it[5] if len(it) > 5 else None,
                        "rare": bool(it[8]) if len(it) > 8 else False,
                    })
                    await db.mark_seen(iid)
            except (IndexError, TypeError):
                continue
        if not new:
            return
        print(f"[NEW] {len(new)} item baru")
        for meta in new:
            for sub in subs:
                if not self._pass_filter(meta, sub):
                    continue
                await send_to(self, sub["target_type"], sub["target_id"], new_item_embed(meta))
                await asyncio.sleep(0.5)

    def _pass_filter(self, meta, sub):
        ft = sub.get("filter_type") or "any"
        fv = sub.get("filter_value")
        if ft == "any":
            return True
        if ft == "value":
            try:
                return (meta.get("value") or 0) >= int(fv)
            except (ValueError, TypeError):
                return False
        if ft == "rare":
            return bool(meta.get("rare")) == (str(fv).lower() in ("true", "1", "yes"))
        if ft == "keyword":
            return fv and fv.lower() in (meta.get("name") or "").lower()
        return True

    async def _check_watches(self):
        for w in await db.list_watches():
            try:
                v = await api.get_item_value(w["item_id"])
                if not v:
                    continue
                cur = v.get("value")
                last = w.get("last_value")
                if last is not None and cur != last:
                    meta = api.get_meta(w["item_id"]) or {"id": w["item_id"], "name": f"Item {w['item_id']}"}
                    e = item_embed(meta, v)
                    e.title = f"🔔 {meta['name']}"
                    e.color = 0x00FF00 if (cur or 0) > (last or 0) else 0xFF5555
                    e.add_field(name="Perubahan", value=f"{fmt(last)} → {fmt(cur)}", inline=False)
                    await send_to(self, w["target_type"], w["target_id"], e)
                await db.update_last_value(w["id"], cur)
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[WATCH ERR] {e}")

    async def close(self):
        if self.poll_task:
            self.poll_task.cancel()
        if db.conn:
            await db.conn.close()
        await api.close()
        await super().close()


bot = RolimonsBot()


# ============================================================
# AUTOCOMPLETE
# ============================================================
async def item_ac(interaction, current):
    if not api._name_index:
        await api.load_items_index()
    if not current:
        return []
    results = process.extract(current, api._name_index, scorer=fuzz.WRatio, limit=25, score_cutoff=50)
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n, _, _ in results]


# ============================================================
# COMMANDS
# ============================================================
@bot.tree.command(name="cari", description="Cari item (fuzzy)")
async def cari_cmd(interaction, query: str):
    await interaction.response.defer()
    r = await api.search_items(query, limit=10)
    if not r:
        await interaction.followup.send(f"❌ Gak nemu `{query}`.")
        return
    e = discord.Embed(title=f"🔍 `{query}`", color=0x00A8FF)
    e.description = "\n".join(f"• **{x['name']}** — 💰 {fmt(x.get('value'))} · RAP {fmt(x.get('rap'))}" for x in r[:10])
    await interaction.followup.send(embed=e)


@bot.tree.command(name="item", description="Statistik item")
@app_commands.autocomplete(nama=item_ac)
async def item_cmd(interaction, nama: str):
    await interaction.response.defer()
    m = await api.search_items(nama, limit=1)
    if not m:
        await interaction.followup.send(f"❌ Gak nemu `{nama}`.")
        return
    meta = m[0]
    v = await api.get_item_value(meta["id"])
    d = await api.get_item_details(meta["id"])
    await interaction.followup.send(embed=item_embed(meta, v, d))


@bot.tree.command(name="price", description="Value + RAP cepet")
@app_commands.autocomplete(nama=item_ac)
async def price_cmd(interaction, nama: str):
    await interaction.response.defer()
    m = await api.search_items(nama, limit=1)
    if not m:
        await interaction.followup.send(f"❌ Gak nemu `{nama}`.")
        return
    v = await api.get_item_value(m[0]["id"])
    await interaction.followup.send(embed=item_embed(m[0], v))


@bot.tree.command(name="player", description="Statistik player")
async def player_cmd(interaction, user_id: int):
    await interaction.response.defer()
    p = await api.get_player(user_id)
    o = await api.get_player_owned(user_id)
    if not p:
        await interaction.followup.send(f"❌ Player `{user_id}` gak ketemu.")
        return
    e = discord.Embed(title=p.get("name", "?"), url=f"https://www.rolimons.com/player/{user_id}", color=0x00FF88)
    if p.get("thumbnail"):
        e.set_thumbnail(url=p["thumbnail"])
    e.add_field(name="💰 Value", value=fmt(p.get("value")), inline=True)
    e.add_field(name="📊 RAP", value=fmt(p.get("rap")), inline=True)
    e.add_field(name="🏆 Rank", value=f"#{p.get('rank', 'N/A')}", inline=True)
    if o and o.get("items"):
        top = o["items"][:5]
        e.add_field(name="Top 5", value="\n".join(f"• {i.get('name', '?')} — {fmt(i.get('value'))}" for i in top), inline=False)
    await interaction.followup.send(embed=e)


@bot.tree.command(name="deals", description="Deals aktif")
async def deals_cmd(interaction):
    await interaction.response.defer()
    d = await api.get_deals()
    if not d or not d.get("deals"):
        await interaction.followup.send("❌ Gak ada deals.")
        return
    e = discord.Embed(title="💸 Deals", color=0xFFD700)
    e.description = "\n".join(
        f"• **{x.get('name', '?')}
