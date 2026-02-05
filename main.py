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

ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", 1446465532441395372))

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
paused_dates = set()       # {"YYYY-MM-DD"}

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


def load_submissions_today():
    """Preload today's submission counts so reminders don't ping submitters after restart."""
    date_str = today_str()
    try:
        ws = sheet.worksheet(date_str)
    except:
        return

    submitted = ws.col_values(2)[1:]
    for uname in submitted:
        submissions_today[uname] = submissions_today.get(uname, 0) + 1


def get_paused_ws():
    try:
        ws = sheet.worksheet("Paused_Dates")
    except:
        ws = sheet.add_worksheet("Paused_Dates", rows=200, cols=1)
        ws.append_row(["Date"])
    return ws


def load_paused_dates():
    ws = get_paused_ws()
    dates = ws.col_values(1)[1:]
    for d in dates:
        if d:
            paused_dates.add(d.strip())


def is_paused_date(date_str: str) -> bool:
    if date_str in paused_dates:
        return True
    try:
        ws = get_paused_ws()
        rows = set(v.strip() for v in ws.col_values(1)[1:] if v)
        if date_str in rows:
            paused_dates.add(date_str)
            return True
    except:
        pass
    return False

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


def count_submissions_between(start_date, end_date):
    """Count submissions per user between two dates (inclusive)."""
    counts = {u: 0 for u in registered_users}
    current = start_date
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        if date_str in paused_dates:
            current += datetime.timedelta(days=1)
            continue
        try:
            ws = sheet.worksheet(date_str)
            submitted = ws.col_values(2)[1:]
            for uname in submitted:
                if uname in counts:
                    counts[uname] += 1
        except:
            pass
        current += datetime.timedelta(days=1)
    return counts


def last_four_day_range():
    today = datetime.datetime.now(IST).date()
    start = today - datetime.timedelta(days=4)
    end = today - datetime.timedelta(days=1)
    return start, end


def get_announcement_channel():
    """Best-effort lookup for a text channel to post announcements."""
    if ANNOUNCE_CHANNEL_ID:
        try:
            channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
            if channel and channel.permissions_for(channel.guild.me).send_messages:
                return channel
        except:
            pass

    for guild in bot.guilds:
        me = guild.me or guild.get_member(bot.user.id)
        if not me:
            continue
        for channel in guild.text_channels:
            if channel.permissions_for(me).send_messages:
                return channel
    return None

# ================== EVENTS ==================

@bot.event
async def on_ready():
    load_registered_users()
    load_paused_dates()
    load_submissions_today()
    if not daily_reminder.is_running():
        daily_reminder.start()
    if not weekly_reminder.is_running():
        weekly_reminder.start()
    if not monthly_target_check.is_running():
        monthly_target_check.start()
    if not inactive_reminder.is_running():
        inactive_reminder.start()
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

    if is_paused_date(date_str):
        return await ctx.reply(f"⏸️ Submissions paused for **{date_str}**")

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
async def status(ctx, date: str | None = None):
    if ctx.guild:
        return await ctx.reply("DM me")

    if date is None:
        target_date = today_str()
    else:
        if not is_valid_date(date):
            return await ctx.reply("❌ Please use date in **YYYY-MM-DD** format.")
        
        if not is_date_within_last_3_days(date):
            return await ctx.reply("❌ You can view status only for **today or last 3 days**.")
        
        target_date = date

    try:
        ws = sheet.worksheet(target_date)
    except:
        return await ctx.reply(f"❌ No submissions logged for **{target_date}**.")
    
    submitted_users = ws.col_values(2)[1:]
    count=submitted_users.count(ctx.author.name)
    
    if target_date==today_str():
        label = "today"
    else:
        label = target_date

    if count>0:
        return await ctx.reply(f"✅ You have submitted **{count}** time(s) for **{label}**.")
    else:
        return await ctx.reply(f"❌ No submissions on **{label}**.")

@bot.command()
async def delete(ctx, date: str | None = None):
    if ctx.guild:
        return await ctx.reply("DM me")
    
    uname = ctx.author.name
    
    if date is None:
        target_date = today_str()
    else:
        if not is_valid_date(date):
            return await ctx.reply("❌ Please use date in **YYYY-MM-DD** format.")
        if not is_date_within_last_3_days(date):
            return await ctx.reply("❌ You can delete submissions only for **today or last 3 days**.")
        target_date = date

    try:
        ws=sheet.worksheet(target_date)
    except:
        return await ctx.reply(f"❌ No submissions recorded for **{target_date}**.")
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return await ctx.reply(f"❌ No submissions on **{target_date}**.")
    
    user_rows = []
    for row_idx, row in enumerate(rows[1:], 2):  
        if row[1] == uname:  
            user_rows.append(row_idx)

    if not user_rows:
        label = "today" if target_date == today_str() else target_date
        return await ctx.reply(f"❌ No submissions for you on **{label}**.")
    
    row_to_delete = user_rows[-1]
    ws.delete_rows(row_to_delete)

    if target_date == today_str():
        submissions_today[uname] = max(0, submissions_today.get(uname, 1) - 1)

    if target_date == today_str():
        label = "today"
    else:
        label = target_date
    await ctx.reply(f"✅ Deleted your submission from **{label}**.")


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
async def pause(ctx, date: str | None = None):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only")

    target_date = today_str() if date is None else date
    if not is_valid_date(target_date):
        return await ctx.reply("❌ Please use date in **YYYY-MM-DD** format.")

    if target_date in paused_dates:
        return await ctx.reply(f"ℹ️ Already paused for **{target_date}**")

    ws = get_paused_ws()
    paused_dates.add(target_date)
    ws.append_row([target_date])
    await ctx.reply(f"⏸️ Submissions paused for **{target_date}** (ignored in reminders and targets)")


@bot.command()
async def unpause(ctx, date: str | None = None):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only")

    target_date = today_str() if date is None else date
    if not is_valid_date(target_date):
        return await ctx.reply("❌ Please use date in **YYYY-MM-DD** format.")

    if target_date in paused_dates:
        paused_dates.remove(target_date)
        ws = get_paused_ws()
        rows = ws.col_values(1)
        for idx, val in enumerate(rows, start=1):
            if val == target_date:
                ws.delete_rows(idx)
                break
        await ctx.reply(f"▶️ Submissions unpaused for **{target_date}**")
    else:
        await ctx.reply(f"ℹ️ No pause set for **{target_date}**")

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

    current_year = now.year
    current_month = now.month

    day_sheets = []
    for ws in sheet.worksheets():
        if not is_valid_date(ws.title):
            continue
        try:
            d = datetime.datetime.strptime(ws.title, "%Y-%m-%d").date()
        except:
            continue
        if d.year == current_year and d.month == current_month:
            day_sheets.append(ws)

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

    end = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    start = end - datetime.timedelta(days=6)

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


@bot.command()
async def inactive(ctx):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only")

    start, end = last_four_day_range()
    counts = count_submissions_between(start, end)
    inactive_users = [registered_users[u] for u, total in counts.items() if total == 0]

    if not inactive_users:
        return

    msg = "\n".join(f"• {name}" for name in inactive_users)
    await ctx.send(f"Inactive last 4 days:\n{msg}")



# ================== REMINDER ==================

@tasks.loop(time=datetime.time(hour=22, minute=0, tzinfo=IST))
async def daily_reminder():
    if is_paused_date(today_str()):
        submissions_today.clear()
        return
    for uname in registered_users:
        if uname not in submissions_today:
            user = discord.utils.get(bot.users, name=uname)
            if user:
                try:
                    await user.send("⏰ Reminder: submit today’s CP")
                except:
                    pass
    submissions_today.clear()


@tasks.loop(time=datetime.time(hour=21, minute=15, tzinfo=IST))
async def weekly_reminder():
    now = datetime.datetime.now(IST)
    if now.weekday() != 6:  # Run only on Sundays
        return

    week_end = now.date()
    week_start = week_end - datetime.timedelta(days=6)
    counts = count_submissions_between(week_start, week_end)

    for uname, total in counts.items():
        if total < 3:
            user = discord.utils.get(bot.users, name=uname)
            if user:
                try:
                    await user.send(
                        f"📅 Weekly reminder: {total} submissions from {week_start} to {week_end}. Target is 3+."
                    )
                except:
                    pass


@tasks.loop(time=datetime.time(hour=21, minute=30, tzinfo=IST))
async def monthly_target_check():
    now = datetime.datetime.now(IST)
    if now.day != 1:  # Run on the first day of the month for the previous month
        return

    last_month_end = now.date() - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    counts = count_submissions_between(last_month_start, last_month_end)
    month_label = last_month_start.strftime("%B %Y")

    for uname, total in counts.items():
        if total < 14:
            user = discord.utils.get(bot.users, name=uname)
            if user:
                try:
                    await user.send(
                        f"🗓️ Monthly target alert: {total} submissions in {month_label}. Please aim for 14+ to hit the target."
                    )
                except:
                    pass


@tasks.loop(time=datetime.time(hour=21, minute=45, tzinfo=IST))
async def inactive_reminder():
    today = datetime.datetime.now(IST).date()
    if today.day % 4 != 0:  # Run on the 4th, 8th, 12th, etc. of each month
        return

    channel = get_announcement_channel()
    if not channel:
        return

    start, end = last_four_day_range()
    counts = count_submissions_between(start, end)
    inactive_users = [registered_users[u] for u, total in counts.items() if total == 0]

    if not inactive_users:
        return

    msg = "\n".join(f"• {name}" for name in inactive_users)
    await channel.send(f"Inactive last 4 days ({start} to {end}):\n{msg}")

# ================== RUN ==================

bot.run(TOKEN)
