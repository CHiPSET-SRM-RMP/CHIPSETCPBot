import discord
from discord.ext import commands, tasks
import os
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import uuid
from pathlib import Path
import pytz

# ================== CONFIG ==================

TOKEN = os.getenv("TOKEN")

VM_PUBLIC_IP = "52.172.194.26"
IMAGE_BASE_URL = f"http://{VM_PUBLIC_IP}:8080"

IMAGE_DIR = Path("/home/Chakradhar/cpbot_images")
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

SHEET_ID = "1qPoJ0uBdVCQZMZYWRS6Bt60YjJnYUkD4OePSTRMiSrI"
SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]

IST = pytz.timezone("Asia/Kolkata")

# ================== GOOGLE SHEETS ==================

sheets_creds = ServiceAccountCredentials.from_json_keyfile_name(
    "service_account.json",
    SCOPE
)

sheet_client = gspread.authorize(sheets_creds)
sheet = sheet_client.open_by_key(SHEET_ID)

# ================== BOT ==================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

registered_users = {}      # {username: real_name}
submissions_today = {}     # {username: count}

# ================== HELPERS ==================
def get_week_range(date_str):
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    start = d - datetime.timedelta(days=d.weekday())  # Monday
    end = start + datetime.timedelta(days=6)          # Sunday
    return start, end

def today_str():
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")

def is_valid_date(date_str):
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except:
        return False

def is_date_within_last_3_days(date_str):
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.datetime.now(IST).date()
        diff = (today - d).days
        return 0 <= diff <= 3
    except:
        return False

def load_registered_users():
    try:
        ws = sheet.worksheet("Registered_Users")
    except:
        ws = sheet.add_worksheet("Registered_Users", rows=200, cols=2)
        ws.append_row(["Discord Username", "Real Name"])
        return

    rows = ws.get_all_values()[1:]
    for row in rows:
        if len(row) >= 2:
            registered_users[row[0]] = row[1]

def get_sheet_for_date(date_str):
    try:
        ws = sheet.worksheet(date_str)
    except:
        ws = sheet.add_worksheet(date_str, rows=300, cols=4)
        ws.append_row(["Date", "Username", "Screenshot", "Problem"])
    return ws

def save_image_locally(discord_url):
    r = requests.get(discord_url)
    r.raise_for_status()

    ext = discord_url.split('.')[-1].split('?')[0]
    if ext not in ["png", "jpg", "jpeg", "gif", "webp"]:
        ext = "png"

    filename = f"{uuid.uuid4()}.{ext}"
    filepath = IMAGE_DIR / filename

    with open(filepath, "wb") as f:
        f.write(r.content)

    return f"{IMAGE_BASE_URL}/{filename}"

# ================== EVENTS ==================

@bot.event
async def on_ready():
    load_registered_users()
    daily_reminder.start()
    print(f"✅ Bot online: {bot.user}")

# ================== COMMANDS ==================

@bot.command()
async def register(ctx):
    if ctx.guild:
        return await ctx.reply("📩 DM me to register")

    uname = ctx.author.name
    if uname in registered_users:
        return await ctx.reply("Already registered 🤝")

    await ctx.reply("Send your **REAL NAME** 👇")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    msg = await bot.wait_for("message", check=check, timeout=60)
    real_name = msg.content.strip()

    registered_users[uname] = real_name
    sheet.worksheet("Registered_Users").append_row([uname, real_name])

    await ctx.reply(f"✅ Registered as **{real_name}**")

@bot.command()
async def submit(ctx, *, args=""):
    if ctx.guild:
        return await ctx.reply("Submit in DM only")

    uname = ctx.author.name
    if uname not in registered_users:
        return await ctx.reply("❌ Please /register first")

    if not ctx.message.attachments:
        return await ctx.reply("⚠️ Attach screenshot")

    parts = args.split()
    date_str = today_str()
    problem = args or "No Name"

    if parts and is_valid_date(parts[-1]):
        date_str = parts[-1]
        problem = " ".join(parts[:-1]) or "No Name"

        if not is_date_within_last_3_days(date_str):
            return await ctx.reply("❌ Allowed only **today or last 3 days**")

    await ctx.reply("📤 Saving image…")

    image_url = save_image_locally(ctx.message.attachments[0].url)

    ws = get_sheet_for_date(date_str)
    ws.append_row([
        date_str,
        uname,
        image_url,
        problem
    ])

    if date_str == today_str():
        submissions_today[uname] = submissions_today.get(uname, 0) + 1
        await ctx.reply(f"🔥 Submission #{submissions_today[uname]} saved for **today**")
    else:
        await ctx.reply(f"✅ Backdated submission saved for **{date_str}**")

@bot.command()
async def status(ctx):
    if ctx.guild:
        return await ctx.reply("DM me")

    count = submissions_today.get(ctx.author.name, 0)
    if count:
        await ctx.reply(f"✔ You submitted {count} today")
    else:
        await ctx.reply("❌ No submissions today")

@bot.command()
async def notcompleted(ctx):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only")

    try:
        ws = sheet.worksheet(today_str())
    except:
        return await ctx.reply("No submissions today")

    submitted = set(ws.col_values(2)[1:])

    pending = [
        registered_users[u]
        for u in registered_users
        if u not in submitted
    ]

    if not pending:
        return await ctx.reply("🎉 Everyone submitted!")

    msg = "\n".join(f"• {name}" for name in pending)
    await ctx.reply(f"❌ Not submitted today:\n\n{msg}")

@bot.command()
async def summarize(ctx):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only")

    now = datetime.datetime.now(IST)
    title = f"Summary-{now.strftime('%B')}-{now.year}"

    try:
        sheet.worksheet(title)
        return await ctx.reply("Summary already exists")
    except:
        sws = sheet.add_worksheet(title, rows=200, cols=4)
        sws.append_row(["Real Name", "Days Submitted", "Total Days", "Consistency %"])

    day_sheets = [ws for ws in sheet.worksheets() if is_valid_date(ws.title)]
    total_days = len(day_sheets)

    submissions_per_day = [
        set(ws.col_values(2)[1:]) for ws in day_sheets
    ]

    for uname, real_name in registered_users.items():
        days = sum(1 for day in submissions_per_day if uname in day)
        percent = (days / total_days * 100) if total_days else 0
        sws.append_row([real_name, days, total_days, f"{percent:.1f}%"])

    await ctx.reply("📊 Summary generated")
@bot.command()
async def weeksummarize(ctx, date_str):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only")

    if not is_valid_date(date_str):
        return await ctx.reply("❌ Use YYYY-MM-DD format")

    start, end = get_week_range(date_str)
    title = f"Week-{start}_to_{end}"

    try:
        sheet.worksheet(title)
        return await ctx.reply("Weekly summary already exists")
    except:
        ws = sheet.add_worksheet(title, rows=200, cols=4)
        ws.append_row(["Real Name", "Days Submitted", "Total Days", "Consistency %"])

    week_days = [
        (start + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(7)
    ]

    submissions = []
    for d in week_days:
        try:
            day_ws = sheet.worksheet(d)
            submissions.append(set(day_ws.col_values(2)[1:]))
        except:
            submissions.append(set())

    for uname, real_name in registered_users.items():
        days = sum(1 for day in submissions if uname in day)
        percent = (days / 7) * 100
        ws.append_row([real_name, days, 7, f"{percent:.1f}%"])

    await ctx.reply(f"📊 Weekly summary created ({start} → {end})")


# ================== REMINDER ==================

@tasks.loop(time=datetime.time(hour=22, minute=0, tzinfo=IST))
async def daily_reminder():
    for uname in registered_users:
        if uname not in submissions_today:
            user = discord.utils.get(bot.users, name=uname)
            if user:
                try:
                    await user.send("⏰ Reminder: submit today’s CP")
                except:
                    pass
    submissions_today.clear()

# ================== RUN ==================

bot.run(TOKEN)
