import discord
from discord.ext import commands, tasks
import os
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import requests
import uuid
from flask import Flask, send_file, abort
import threading
from pathlib import Path
import subprocess
import time

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

# ---------- Flask App Setup for Image Serving ----------
app = Flask(__name__)
IMAGES_DIR = Path("uploaded_images")
IMAGES_DIR.mkdir(exist_ok=True)

# Ngrok setup for public tunnel
PUBLIC_URL = None  # Will be set after ngrok starts

@app.route('/image/<filename>')
def serve_image(filename):
    """Serve uploaded images"""
    image_path = IMAGES_DIR / filename
    if image_path.exists():
        return send_file(image_path)
    else:
        abort(404)

def start_ngrok():
    """Start ngrok tunnel and get public URL"""
    global PUBLIC_URL
    try:
        # Start ngrok tunnel
        port = int(os.getenv("PORT", 5000))
        process = subprocess.Popen(
            ["ngrok", "http", str(port), "--log=stdout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait a bit for ngrok to start
        time.sleep(3)
        
        # Get the public URL from ngrok API
        response = requests.get("http://localhost:4040/api/tunnels")
        tunnels = response.json()["tunnels"]
        
        for tunnel in tunnels:
            if tunnel["proto"] == "https":
                PUBLIC_URL = tunnel["public_url"]
                break
        
        if not PUBLIC_URL and tunnels:
            PUBLIC_URL = tunnels[0]["public_url"]
            
        print(f"Ngrok tunnel started: {PUBLIC_URL}")
        return process
        
    except Exception as e:
        print(f"Failed to start ngrok: {e}")
        print("Falling back to localhost (images won't be publicly accessible)")
        PUBLIC_URL = "http://localhost:5000"
        return None

def run_flask():
    """Run Flask app in a separate thread"""
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def download_and_store_image(attachment_url):
    """Download image from Discord and store locally"""
    try:
        # Generate unique filename
        file_extension = attachment_url.split('.')[-1].split('?')[0]
        if file_extension not in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
            file_extension = 'png'  # default extension
        
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        file_path = IMAGES_DIR / unique_filename
        
        # Download the image
        response = requests.get(attachment_url, stream=True)
        response.raise_for_status()
        
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Return the public URL
        return f"{PUBLIC_URL}/image/{unique_filename}"
    except Exception as e:
        print(f"Error downloading image: {e}")
        return None

registered_users = {}  # {discord_username: real_name}
submissions_today = {}  # {discord_username: count}

def is_valid_day_sheet(title):
    try:
        datetime.datetime.strptime(title, "%Y-%m-%d")
        return True
    except:
        return False

def load_users():
    try:
        reg_sheet = sheet.worksheet("Registered_Users")
    except:
        reg_sheet = sheet.add_worksheet(title="Registered_Users", rows=200, cols=2)
        reg_sheet.append_row(["Discord Username", "Real Name"])
        return
    
    rows = reg_sheet.get_all_values()[1:]
    for row in rows:
        if len(row) >= 2:
            registered_users[row[0]] = row[1]

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
    
    # Start ngrok tunnel
    ngrok_process = start_ngrok()
    
    # Start Flask server in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print(f"Bot Ready ✔: {bot.user}")
    print(f"Image server running at: {PUBLIC_URL}")
    daily_reminder.start()


# ---------- Register ----------
@bot.command()
async def register(ctx):
    if ctx.guild is not None:
        return await ctx.reply("📩 DM me to register!")

    uname = ctx.author.name

    if uname in registered_users:
        return await ctx.reply("Already registered 🤝")

    await ctx.reply("Send your REAL FULL NAME 👇")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        msg = await bot.wait_for("message", timeout=60, check=check)
        real_name = msg.content.strip()
        registered_users[uname] = real_name

        reg_sheet = sheet.worksheet("Registered_Users")
        reg_sheet.append_row([uname, real_name])
        await ctx.reply(f"✔ Registered Successfully {real_name} 🎯")

    except:
        await ctx.reply("⏳ Timeout! Try /register again.")


# ---------- Submit ----------
@bot.command()
async def submit(ctx, *, problem_name="No Name"):
    if ctx.guild is not None:
        return await ctx.reply("Submit privately here 😄")

    uname = ctx.author.name

    if uname not in registered_users:
        return await ctx.reply("❌ Register first using `/register`")

    if not ctx.message.attachments:
        return await ctx.reply("⚠️ Attach screenshot also!")

    # Download and store the image
    await ctx.reply("📥 Uploading your image...")
    
    attachment_url = ctx.message.attachments[0].url
    permanent_url = download_and_store_image(attachment_url)
    
    if not permanent_url:
        return await ctx.reply("❌ Failed to upload image. Try again!")

    submissions_today[uname] = submissions_today.get(uname, 0) + 1

    ws = get_today_sheet()
    ws.append_row([
        str(datetime.datetime.now().date()),
        uname,
        permanent_url,  # Use permanent URL instead of Discord CDN
        problem_name
    ])

    await ctx.reply(f"🔥 Submission #{submissions_today[uname]} saved with permanent link!")


# ---------- Status ----------
@bot.command()
async def status(ctx):
    if ctx.guild is not None:
        return await ctx.reply("DM me 😄")

    uname = ctx.author.name
    count = submissions_today.get(uname, 0)

    if count > 0:
        await ctx.reply(f"✔ You submitted {count} time(s) today! 🔥")
    else:
        await ctx.reply("❌ No submissions yet today 😬")


# ---------- Not Completed Today (Admin Only) ----------
@bot.command()
async def notcompleted(ctx):
    if ctx.guild is None:
        return await ctx.reply("Use this in server 😄")

    if not ctx.author.guild_permissions.administrator:
        return await ctx.reply("❌ Admin only!")

    today = datetime.datetime.now().strftime("%Y-%m-%d")

    try:
        today_ws = sheet.worksheet(today)
    except:
        return await ctx.reply("⚠️ Nobody submitted today 😅")

    submitted = set(today_ws.col_values(2)[1:])
    not_done = [
        registered_users[u] for u in registered_users
        if u not in submitted
    ]

    if not not_done:
        return await ctx.reply("🎉 Everyone completed today!")

    result = "\n".join(f"• {name}" for name in not_done)
    await ctx.reply(f"❌ Pending Submissions:\n\n{result}")


# ---------- Daily DM Reminder ----------
@tasks.loop(time=datetime.time(hour=22, minute=0))
async def daily_reminder():
    for uname in registered_users:
        if uname not in submissions_today:
            user = discord.utils.get(bot.users, name=uname)
            if user:
                try:
                    await user.send("⏲️ Reminder: Submit today's CP!")
                except:
                    pass
    submissions_today.clear()


TOKEN = os.getenv("TOKEN")
bot.run(TOKEN)
