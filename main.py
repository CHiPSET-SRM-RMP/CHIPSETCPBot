import discord
from discord.ext import commands, tasks
import os
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import json
import requests
import uuid
import io

# ---------- Google Sheets Setup ----------
SHEET_ID = "1qPoJ0uBdVCQZMZYWRS6Bt60YjJnYUkD4OePSTRMiSrI"

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Google Sheets credentials
google_creds_json = os.getenv("GOOGLE_CREDS")

if google_creds_json:
    google_creds = json.loads(google_creds_json)
    sheets_creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
else:
    sheets_creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)

client = gspread.authorize(sheets_creds)
sheet = client.open_by_key(SHEET_ID)

# ---------- Google Drive Setup for Image Storage ----------
# Separate credentials for Drive (can be same or different from Sheets)
drive_creds_json = os.getenv("DRIVE_CREDS")

if drive_creds_json:
    drive_creds_dict = json.loads(drive_creds_json)
    drive_creds = ServiceAccountCredentials.from_json_keyfile_dict(drive_creds_dict, scope)
else:
    # Try to load from drive_service_account.json, fallback to service_account.json
    try:
        drive_creds = ServiceAccountCredentials.from_json_keyfile_name("drive_service_account.json", scope)
    except FileNotFoundError:
        print("drive_service_account.json not found, using service_account.json for Drive")
        drive_creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)

drive_service = build('drive', 'v3', credentials=drive_creds)

# Folder ID where images will be stored (this is a regular folder, not a shared drive)
DRIVE_FOLDER_ID_CONSTANT = "1_5_PPNN9YLOOC00Z-Wg1uhmDKfcglN5G"

# Create or get the folder for bot images
def get_or_create_drive_folder():
    """Get the folder in Drive for storing images"""
    
    # Print service account email for debugging
    if drive_creds_json:
        drive_creds_dict = json.loads(drive_creds_json)
        print(f"Service account email: {drive_creds_dict.get('client_email')}")
    
    # Try to access the specified folder
    try:
        folder = drive_service.files().get(
            fileId=DRIVE_FOLDER_ID_CONSTANT,
            fields='id, name',
            supportsAllDrives=True
        ).execute()
        print(f"✅ Using folder: {folder.get('name')} (ID: {folder.get('id')})")
        return folder.get('id')
    except Exception as e:
        print(f"❌ ERROR: Could not access folder {DRIVE_FOLDER_ID_CONSTANT}")
        print(f"Error details: {e}")
        print(f"\n⚠️  SOLUTION: Share the folder with the service account:")
        print(f"   1. Open: https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID_CONSTANT}")
        print(f"   2. Click 'Share'")
        if drive_creds_json:
            drive_creds_dict = json.loads(drive_creds_json)
            print(f"   3. Add this email as Editor: {drive_creds_dict.get('client_email')}")
        print(f"   4. Restart the bot\n")
        raise Exception("Folder access denied. Please share the folder with the service account.")

DRIVE_FOLDER_ID = None  # Will be set on bot startup

def upload_to_drive(attachment_url):
    """Download image from Discord and upload to Google Drive"""
    try:
        # Download the image from Discord
        response = requests.get(attachment_url, stream=True)
        response.raise_for_status()
        
        # Get file extension
        file_extension = attachment_url.split('.')[-1].split('?')[0]
        if file_extension not in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
            file_extension = 'png'
        
        # Generate unique filename
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        
        # Determine MIME type
        mime_types = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        mime_type = mime_types.get(file_extension, 'image/png')
        
        # Upload to Google Drive
        file_metadata = {
            'name': unique_filename,
            'parents': [DRIVE_FOLDER_ID]
        }
        
        media = MediaIoBaseUpload(
            io.BytesIO(response.content),
            mimetype=mime_type,
            resumable=True
        )
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink',
            supportsAllDrives=True
        ).execute()
        
        file_id = file.get('id')
        
        # Make the file publicly accessible
        drive_service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'},
            supportsAllDrives=True
        ).execute()
        
        # Return direct link to image
        return f"https://drive.google.com/uc?export=view&id={file_id}"
        
    except Exception as e:
        print(f"Error uploading to Drive: {e}")
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
    global DRIVE_FOLDER_ID
    load_users()
    
    # Initialize Google Drive folder
    DRIVE_FOLDER_ID = get_or_create_drive_folder()
    
    print(f"Bot Ready ✔: {bot.user}")
    print(f"Google Drive folder ready for image storage")
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

    # Upload image to Google Drive
    await ctx.reply("📥 Uploading your image to Google Drive...")
    
    attachment_url = ctx.message.attachments[0].url
    permanent_url = upload_to_drive(attachment_url)
    
    if not permanent_url:
        return await ctx.reply("❌ Failed to upload image. Try again!")

    submissions_today[uname] = submissions_today.get(uname, 0) + 1

    ws = get_today_sheet()
    ws.append_row([
        str(datetime.datetime.now().date()),
        uname,
        permanent_url,  # Permanent Google Drive URL
        problem_name
    ])

    await ctx.reply(f"🔥 Submission #{submissions_today[uname]} saved to Google Drive!")


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
