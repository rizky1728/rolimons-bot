# Rolimons Discord Bot

Bot Discord buat cek statistik item Roblox dari [Rolimons](https://www.rolimons.com).
Multi-server, notif item baru, watch value, panel button UI.

## Fitur

- 🔍 Cari item — fuzzy search (`/cari`, `/item`, `/price`)
- 👤 Player stats — value, RAP, rank, inventory (`/player`)
- 💸 Market — deals aktif (`/deals`)
- 🏆 Top items — by value (`/top`)
- 🆕 Notif item baru — filter any/value/rare
- 🔔 Watch item — pantau value berubah
- 🧮 Trade calc — hitung value trade (`/calc`)
- 🎛️ Panel UI — tombol interaktif (`/panel`)
- 🌐 Multi-server — config per-guild, persistent di SQLite
- 🔗 Webhook support — kirim notif ke webhook

## Setup

### 1. Clone repo

```bash
git clone https://github.com/USERNAME/rolimons-bot.git
cd rolimons-bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Bikin bot Discord

1. Buka https://discord.com/developers/applications
2. New Application → kasih nama
3. Bot tab → Reset Token → copy token
4. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`
5. Copy URL → buka di browser → invite ke server lu

### 4. Setup .env

```bash
cp .env.example .env
```

Edit `.env`, isi token:
```
DISCORD_TOKEN=TOKEN_LU_DISINI
```

### 5. Run

```bash
python bot.py
```

## Commands

| Command | Fungsi |
|---|---|
| `/cari <query>` | Cari item by nama |
| `/item <nama>` | Statistik lengkap item |
| `/price <nama>` | Value + RAP cepet |
| `/player <user_id>` | Statistik player Roblox |
| `/deals` | Deals aktif |
| `/top [jumlah]` | Top items by value |
| `/calc <give> <receive>` | Kalkulator trade |
| `/panel` | Buka panel button UI |
| `/status` | Status bot |
| `/help` | Daftar command |

## Deploy

### Railway (gratis)

1. Push repo ke GitHub
2. Buka railway.app → New Project → Deploy from GitHub
3. Pilih repo → Variables → tambah `DISCORD_TOKEN`
4. Deploy otomatis

### Fly.io

```bash
fly launch
fly secrets set DISCORD_TOKEN=TOKEN_LU
fly deploy
```

## Lisensi

MIT
