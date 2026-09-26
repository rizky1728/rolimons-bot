import discord
from discord.ui import View, Modal, TextInput


# ============================================================
# MAIN PANEL
# ============================================================
class PanelView(View):
    def __init__(self, guild_id: int, db, api):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.db = db
        self.api = api

    @discord.ui.button(label="🆕 Item Baru", style=discord.ButtonStyle.primary)
    async def btn_newitem(self, interaction, button):
        v = FilterView(self.guild_id, self.db, self.api)
        await interaction.response.edit_message(embed=filter_embed(), view=v)

    @discord.ui.button(label="🔔 Watch", style=discord.ButtonStyle.success)
    async def btn_watch(self, interaction, button):
        await interaction.response.send_modal(WatchModal(self.guild_id, self.db, self.api))

    @discord.ui.button(label="📋 List", style=discord.ButtonStyle.secondary)
    async def btn_list(self, interaction, button):
        e = await build_list_embed(self.guild_id, self.db, self.api)
        await interaction.response.edit_message(embed=e, view=BackView(self.guild_id, self.db, self.api))

    @discord.ui.button(label="❌ Stop", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction, button):
        await self.db.remove_newitem_sub(self.guild_id, "channel", str(interaction.channel_id))
        await self.db.remove_watch(self.guild_id, "channel", str(interaction.channel_id))
        e = discord.Embed(
            title="✅ Stopped",
            description="Semua subscription & watch di channel ini dihapus.",
            color=0x00FF88,
        )
        await interaction.response.edit_message(embed=e, view=BackView(self.guild_id, self.db, self.api))


# ============================================================
# FILTER VIEW
# ============================================================
class FilterView(View):
    def __init__(self, guild_id: int, db, api):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.db = db
        self.api = api

    @discord.ui.button(label="🌐 Any", style=discord.ButtonStyle.secondary)
    async def f_any(self, interaction, button):
        await self.db.add_newitem_sub(self.guild_id, "channel", str(interaction.channel_id), "any", None)
        await done(interaction, self.guild_id, self.db, self.api, "✅ Subscribe any")

    @discord.ui.button(label="💰 Value", style=discord.ButtonStyle.primary)
    async def f_value(self, interaction, button):
        await interaction.response.send_modal(
            ValueModal(self.guild_id, str(interaction.channel_id), self.db, self.api)
        )

    @discord.ui.button(label="💎 Rare", style=discord.ButtonStyle.primary)
    async def f_rare(self, interaction, button):
        await self.db.add_newitem_sub(self.guild_id, "channel", str(interaction.channel_id), "rare", "true")
        await done(interaction, self.guild_id, self.db, self.api, "✅ Subscribe rare only")

    @discord.ui.button(label="⬅️ Balik", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            embed=main_embed(), view=PanelView(self.guild_id, self.db, self.api)
        )


# ============================================================
# MODALS
# ============================================================
class ValueModal(Modal, title="Minimum Value"):
    val = TextInput(label="Value", placeholder="Contoh: 1000", required=True, max_length=10)

    def __init__(self, guild_id, channel_id, db, api):
        super().__init__()
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.db = db
        self.api = api

    async def on_submit(self, interaction):
        try:
            v = int(self.val.value)
        except ValueError:
            await interaction.response.send_message("❌ Harus angka.", ephemeral=True)
            return
        await self.db.add_newitem_sub(self.guild_id, "channel", self.channel_id, "value", str(v))
        await done(interaction, self.guild_id, self.db, self.api, f"✅ Subscribe value >= {v}")


class WatchModal(Modal, title="Watch Item"):
    item = TextInput(label="Nama item", placeholder="Contoh: Dominus Empyreus", required=True, max_length=60)
    menit = TextInput(label="Interval (menit)", placeholder="15", required=False, max_length=3, default="15")

    def __init__(self, guild_id, db, api):
        super().__init__()
        self.guild_id = guild_id
        self.db = db
        self.api = api

    async def on_submit(self, interaction):
        await interaction.response.defer()
        m = await self.api.search_items(self.item.value, limit=1)
        if not m:
            await interaction.followup.send(f"❌ Item `{self.item.value}` gak nemu.", ephemeral=True)
            return
        try:
            interval = max(5, int(self.menit.value or "15"))
        except ValueError:
            interval = 15
        await self.db.add_watch(self.guild_id, "channel", str(interaction.channel_id), m[0]["id"], interval)
        e = discord.Embed(
            title="✅ Watch Aktif",
            description=f"**{m[0]['name']}** — cek tiap **{interval} menit**",
            color=0x00FF88,
        )
        await interaction.followup.send(embed=e)


# ============================================================
# BACK
# ============================================================
class BackView(View):
    def __init__(self, guild_id, db, api):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.db = db
        self.api = api

    @discord.ui.button(label="🏠 Menu", style=discord.ButtonStyle.primary)
    async def home(self, interaction, button):
        await interaction.response.edit_message(
            embed=main_embed(), view=PanelView(self.guild_id, self.db, self.api)
        )


# ============================================================
# HELPERS
# ============================================================
def main_embed():
    return discord.Embed(
        title="🤖 Rolimons Panel",
        description=(
            "**🆕 Item Baru** — notif item baru rilis\n"
            "**🔔 Watch** — pantau value item\n"
            "**📋 List** — lihat semua subscription\n"
            "**❌ Stop** — hapus semua sub di channel ini"
        ),
        color=0x00A8FF,
    )


def filter_embed():
    return discord.Embed(
        title="🆕 Filter Item Baru",
        description=(
            "**🌐 Any** — semua item\n"
            "**💰 Value** — min value\n"
            "**💎 Rare** — rare only"
        ),
        color=0x00A8FF,
    )


async def build_list_embed(guild_id, db, api):
    subs = await db.list_newitem_subs(guild_id)
    watches = await db.list_watches(guild_id)
    e = discord.Embed(title="📋 Subscriptions", color=0x00A8FF)
    if subs:
        e.add_field(
            name="🆕 Item Baru",
            value="\n".join(
                f"• <#{s['target_id']}> — `{s['filter_type']}` = `{s['filter_value']}`"
                for s in subs
            ) or "—",
            inline=False,
        )
    if watches:
        lines = []
        for w in watches:
            meta = api.get_meta(w["item_id"])
            name = meta["name"] if meta else f"Item {w['item_id']}"
            lines.append(f"• {name} — tiap {w['interval_min']}m")
        e.add_field(name="🔔 Watches", value="\n".join(lines), inline=False)
    if not subs and not watches:
        e.description = "Kosong. Tap 🆕 atau 🔔 buat mulai."
    return e


async def done(interaction, guild_id, db, api, msg):
    e = discord.Embed(title=msg, color=0x00FF88)
    await interaction.response.edit_message(embed=e, view=BackView(guild_id, db, api))


# ============================================================
# REGISTER
# ============================================================
def register_panel(bot, db, api):
    @bot.tree.command(name="panel", description="Buka control panel")
    async def panel_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=main_embed(), view=PanelView(interaction.guild_id, db, api)
        )
