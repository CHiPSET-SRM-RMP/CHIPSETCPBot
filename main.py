import discord
from discord.ext import commands, tasks
import os
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# ---------- Google Sheets Setup ----------
SHEET_ID = "1qPoJ0uBdVCQZMZYWRS6Bt60YjJnYUkD4OePSTRMiSrI"

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

google_creds_json = os.getenv("GOOGLE_CREDS")

if google_creds_json:
    google_creds = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
else:
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)

client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID)

registered_users = set()
submissions_today = {}  # store counts instead of just names

# Validate Daily Sheet Name Function
def is_valid_day_sheet(title):
    try:
        datetime.datetime.strptime(title, "%Y-%m-%d")
        return True
    except:
        return False

# Load Users
def load_users():
    try:
        reg_sheet = sheet.worksheet("Registered_Users")
    except:
        reg_sheet = sheet.add_worksheet(title="Registered_Users", rows=200, cols=1)
        reg_sheet.append_row(["Username"])

    rows = reg_sheet.get_all_values()[1:]
    for row in rows:
        registered_users.add(row[0])

# ---------- Discord Bot Setup ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

def get_today_sheet():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    try:
        ws = sheet.worksheet(today)
    except:
        ws = sheet.add_worksheet(title=today, rows=200, cols=4)
        ws.append_row(["Date", "Username", "Screenshot", "Problem Name"])
    return ws

@bot.event
async def on_ready():
    load_users()
    print(f"Bot is online ✔ {bot.user}")
    daily_reminder.start()


# ---------- Register ----------
@bot.command()
async def register(ctx):
    if ctx.guild is not None:
        return await ctx.reply("DM me to register 😄")

    uname = ctx.author.name
    if uname in registered_users:
        return await ctx.reply("Already registered 🤝")

    registered_users.add(uname)
    reg_sheet = sheet.worksheet("Registered_Users")
    reg_sheet.append_row([uname])

    await ctx.reply("✔ Registered Successfully!")


# ---------- Submit ----------
@bot.command()
async def submit(ctx, *, problem_name="No Name"):
    if ctx.guild is not None:
        return await ctx.reply("Submit only in DM 😄")

    if not ctx.message.attachments:
        return await ctx.reply("Attach screenshot!")

    uname = ctx.author.name

    # Count submissions
    submissions_today[uname] = submissions_today.get(uname, 0) + 1

    ws = get_today_sheet()
    ws.append_row([
        str(datetime.datetime.now().date()),
        uname,
        ctx.message.attachments[0].url,
        problem_name
    ])

    await ctx.reply(f"🔥 Submission #{submissions_today[uname]} saved! 💪 Keep going!")


# ---------- Status ----------
@bot.command()
async def status(ctx):
    if ctx.guild is not None:
        return await ctx.reply("Use in DM 😄")

    uname = ctx.author.name
    if uname in submissions_today:
        await ctx.reply(f"✔ You submitted {submissions_today[uname]} time(s) today! 🔥")
    else:
        await ctx.reply("❌ You haven't submitted yet 😬")


# ---------- Summary ----------
@bot.command()
async def summarize(ctx):
    if ctx.guild is None:
        return await ctx.reply("Use in server 😄")

    if not ctx.author.guild_permissions.administrator:
        return await ctx.reply("Admin only ❌")

    today = datetime.datetime.now()
    sheet_title = f"Summary-{today.strftime('%B')}-{today.strftime('%Y')}"

    try:
        sheet.worksheet(sheet_title)
        return await ctx.reply(f"📄 Already exists: {sheet_title}")
    except:
        sws = sheet.add_worksheet(title=sheet_title, rows=200, cols=4)
        sws.append_row(["Username", "Days Submitted", "Total Days", "Consistency %"])

    valid_days = [ws for ws in sheet.worksheets() if is_valid_day_sheet(ws.title)]
    total_days = len(valid_days)

    users = sheet.worksheet("Registered_Users").col_values(1)[1:]

    for uname in users:
        submitted_days = 0
        for ws in valid_days:
            names = ws.col_values(2)
            if uname in names:
                submitted_days += 1

        consistency = (submitted_days / total_days * 100) if total_days > 0 else 0
        sws.append_row([uname, submitted_days, total_days, f"{consistency:.1f}%"])

    await ctx.reply(f"📊 Summary created: **{sheet_title}** 🎯")


# ---------- DM Reminder ----------
@tasks.loop(time=datetime.time(hour=22, minute=0))
async def daily_reminder():
    for uname in registered_users:
        if uname not in submissions_today:
            user = discord.utils.get(bot.users, name=uname)
            if user:
                try:
                    await user.send("⏲️ Don't forget to submit today's CP!")
                except:
                    pass
    submissions_today.clear()


TOKEN = os.getenv("TOKEN")
bot.run(TOKEN)
