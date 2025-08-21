import discord
from discord.ext import commands, tasks
import os
import requests
import json
import uuid
from datetime import datetime, timedelta
import asyncio

# ================= CONFIG =================
TOKEN = "YOUR_BOT_TOKEN"
OWNER_ID = 1234567890  # Replace with your Discord ID
STOCK_FOLDER = "stock"  # folder: stock/<guild_id>/<module>.txt
BOT_NAME = "Ruthless"
WORKINK_API_KEY = "eac03595-d0ef-41cc-991c-ba5c12081c53"

# default editable settings
default_settings = {
    "claim_cooldown": 300,
    "reminder_interval": 180,
    "progress_duration": 12,
    "progress_speed": 1,
    "max_recent": 20
}

# ================= INIT =================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

if not os.path.exists(STOCK_FOLDER):
    os.makedirs(STOCK_FOLDER)

# Pending claims: {user_id: {"guild_id", "module", "account", "link_id", "completed", "timestamp", "reminded"}}
pending_claims = {}

# Cooldowns: {user_id: datetime_of_last_claim}
cooldowns = {}

# Recent claims: list of {"user": str, "module": str, "account": str, "guild": str}
recent_claims = []

# Server settings: {guild_id: settings_dict}
server_settings = {}

# ================= HELPERS =================

def get_server_stock(guild_id):
    folder = os.path.join(STOCK_FOLDER, str(guild_id))
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def get_modules(guild_id):
    folder = get_server_stock(guild_id)
    return [f.replace(".txt","") for f in os.listdir(folder) if f.endswith(".txt")]

def get_stock(guild_id, module):
    path = os.path.join(get_server_stock(guild_id), f"{module}.txt")
    if not os.path.exists(path):
        return []
    with open(path,"r") as f:
        return [line.strip() for line in f.readlines() if line.strip()]

def save_stock(guild_id, module, accounts):
    path = os.path.join(get_server_stock(guild_id), f"{module}.txt")
    with open(path,"w") as f:
        for acc in accounts:
            f.write(acc+"\n")

def is_owner(ctx):
    return ctx.author.id == OWNER_ID

def get_setting(guild_id, key):
    return server_settings.get(guild_id, default_settings).get(key, default_settings[key])

def set_setting(guild_id, key, value):
    if guild_id not in server_settings:
        server_settings[guild_id] = default_settings.copy()
    server_settings[guild_id][key] = value

# ================= WORK.INK =================

def create_workink_link(user_id, module_name, account):
    unique_id = str(uuid.uuid4())[:8]
    payload = {
        "title": f"{BOT_NAME} - {module_name}",
        "link_description": f"Complete the task to get your {module_name} account",
        "destination": f"https://ruthless.com/delivery/{user_id}/{unique_id}",
        "f_domain": "w.ink",
        "custom": unique_id
    }
    headers = {"X-Api-Key": WORKINK_API_KEY, "Content-Type": "application/json"}
    url = "https://dashboard.work.ink/_api/v1/link"
    response = requests.post(url,json=payload,headers=headers)
    if response.status_code==200:
        data=response.json()
        return data.get("link", f"https://w.ink/{unique_id}"), unique_id
    else:
        print("Work.ink creation failed:", response.text)
        return None, None

def check_workink_completion(link_id):
    url = f"https://dashboard.work.ink/_api/v1/link/{link_id}"
    headers={"X-Api-Key": WORKINK_API_KEY}
    response=requests.get(url, headers=headers)
    if response.status_code==200:
        data=response.json()
        return data.get("completed", False)
    return False

# ================= BACKGROUND TASK =================

@tasks.loop(seconds=10)
async def auto_check_claims():
    to_remove=[]
    for user_id, claim in pending_claims.items():
        if not claim["completed"] and check_workink_completion(claim["link_id"]):
            claim["completed"]=True
            user = await bot.fetch_user(user_id)
            guild_name = bot.get_guild(claim["guild_id"]).name if bot.get_guild(claim["guild_id"]) else "Unknown"
            stock = get_stock(claim["guild_id"], claim["module"])
            if claim["account"] in stock:
                stock.remove(claim["account"])
                save_stock(claim["guild_id"], claim["module"], stock)
            embed = discord.Embed(
                title=f"✅ {BOT_NAME} Account Delivered",
                description=f"**Module:** {claim['module']}\n**Account:**\n```\n{claim['account']}\n```",
                color=0x00FF00
            )
            embed.set_footer(text=f"{BOT_NAME} • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
            try:
                await user.send(embed=embed)
            except:
                pass
            # recent claims
            recent_claims.insert(0, {"user": user.name, "module": claim["module"], "account": claim["account"], "guild": guild_name})
            max_recent = get_setting(claim["guild_id"], "max_recent")
            if len(recent_claims) > max_recent:
                recent_claims.pop()
            to_remove.append(user_id)
        # reminder
        if not claim["completed"]:
            reminder_interval = get_setting(claim["guild_id"], "reminder_interval")
            if not claim.get("reminded", False) and (datetime.utcnow()-claim["timestamp"]).total_seconds() > reminder_interval:
                user = await bot.fetch_user(user_id)
                try:
                    await user.send(f"⏳ Reminder: Please complete your Work.ink task to receive your {claim['module']} account.")
                    claim["reminded"]=True
                except:
                    pass
    for uid in to_remove:
        del pending_claims[uid]

@auto_check_claims.before_loop
async def before_loop():
    await bot.wait_until_ready()

# ================= COMMANDS =================

@bot.command()
async def stock(ctx):
    guild_id = ctx.guild.id
    modules = get_modules(guild_id)
    if not modules:
        await ctx.send(embed=discord.Embed(title="❌ Stock", description="No modules available.", color=0xFF4D4F))
        return
    embed = discord.Embed(title=f"{BOT_NAME} Stock", description="Available modules:", color=0x00FFFF)
    for m in modules:
        embed.add_field(name=f"🗂️ {m}", value=f"{len(get_stock(guild_id,m))} accounts available", inline=False)
    embed.set_footer(text=f"{BOT_NAME} • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    await ctx.send(embed=embed)

@bot.command()
async def gen(ctx, module_name: str):
    guild_id = ctx.guild.id
    user_id = ctx.author.id
    # cooldown
    last = cooldowns.get(user_id)
    cooldown_time = get_setting(guild_id,"claim_cooldown")
    if last and (datetime.utcnow()-last).total_seconds()<cooldown_time:
        await ctx.send(f"❌ You must wait before generating another account.")
        return
    if user_id in pending_claims:
        await ctx.send(f"❌ You already have a pending claim. Complete it first.")
        return
    modules = get_modules(guild_id)
    if module_name not in modules:
        await ctx.send(embed=discord.Embed(title="❌ Error", description="Module does not exist.", color=0xFF4D4F))
        return
    stock = get_stock(guild_id,module_name)
    if not stock:
        await ctx.send(embed=discord.Embed(title="❌ Error", description="No accounts left.", color=0xFF4D4F))
        return
    account = stock[0]
    cooldowns[user_id]=datetime.utcnow()
    # PROGRESS BAR
    duration = get_setting(guild_id,"progress_duration")
    speed = get_setting(guild_id,"progress_speed")
    bar_length = 12
    embed = discord.Embed(title=f"⏳ Preparing {module_name} account", description="", color=0xFFFF00)
    progress_msg = await ctx.send(embed=embed)
    for i in range(bar_length+1):
        bar = "▓"*i + "░"*(bar_length-i)
        percent = int(i/bar_length*100)
        embed.description=f"[{bar}] {percent}%"
        await progress_msg.edit(embed=embed)
        await asyncio.sleep(speed)
    # send Work.ink link
    link, link_id = create_workink_link(user_id,module_name,account)
    if not link:
        await ctx.send(embed=discord.Embed(title="❌ Error", description="Failed to create Work.ink link.", color=0xFF4D4F))
        return
    pending_claims[user_id]={"guild_id":guild_id,"module":module_name,"account":account,"link_id":link_id,"completed":False,"timestamp":datetime.utcnow(),"reminded":False}
    try:
        await ctx.author.send(f"⏳ Complete this Work.ink task to get your {module_name} account:\n{link}")
        await progress_msg.edit(embed=discord.Embed(title="⏳ Work.ink link sent!", description=f"Check your DMs to complete the task and receive your account.", color=0x00FFFF))
    except:
        await ctx.send("❌ Cannot DM you. Enable DMs to receive your account.")

# ================= OWNER COMMANDS =================

@bot.command()
async def cancel(ctx, user: discord.User):
    if not is_owner(ctx): return
    if user.id in pending_claims:
        del pending_claims[user.id]
        await ctx.send(f"✅ Pending claim for {user.name} canceled.")
    else:
        await ctx.send(f"❌ User {user.name} has no pending claim.")

@bot.command()
async def recent(ctx):
    if not is_owner(ctx): return
    if not recent_claims:
        await ctx.send("No recent claims.")
        return
    embed = discord.Embed(title="📝 Recent Claims", color=0x00FFFF)
    for c in recent_claims:
        embed.add_field(name=f"{c['user']} ({c['guild']})", value=f"{c['module']} - {c['account']}", inline=False)
    embed.set_footer(text=f"{BOT_NAME} • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    await ctx.send(embed=embed)

@bot.command()
async def help(ctx):
    embed = discord.Embed(title=f"{BOT_NAME} Commands", description="User & Owner commands", color=0x00FFFF)
    embed.add_field(name="!stock", value="Show available modules and stock", inline=False)
    embed.add_field(name="!gen <module>", value="Generate a Work.ink link to get an account", inline=False)
    if is_owner(ctx):
        embed.add_field(name="!recent", value="Show recent claims", inline=False)
        embed.add_field(name="!cancel <user>", value="Cancel pending claim", inline=False)
        embed.add_field(name="!set_cooldown <seconds>", value="Set claim cooldown", inline=False)
        embed.add_field(name="!set_reminder <seconds>", value="Set DM reminder interval", inline=False)
        embed.add_field(name="!set_progress_duration <seconds>", value="Set progress bar duration", inline=False)
        embed.add_field(name="!set_progress_speed <seconds>", value="Set progress bar update speed", inline=False)
        embed.add_field(name="!set_max_recent <number>", value="Set max recent claims", inline=False)
    embed.set_footer(text=f"{BOT_NAME} • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    await ctx.send(embed=embed)

# ================= OWNER SETTINGS =================

@bot.command()
async def set_cooldown(ctx, seconds: int):
    if not is_owner(ctx): return
    set_setting(ctx.guild.id,"claim_cooldown",seconds)
    await ctx.send(f"✅ Claim cooldown set to {seconds} seconds.")

@bot.command()
async def set_reminder(ctx, seconds: int):
    if not is_owner(ctx): return
    set_setting(ctx.guild.id,"reminder_interval",seconds)
    await ctx.send(f"✅ Reminder interval set to {seconds} seconds.")

@bot.command()
async def set_progress_duration(ctx, seconds: int):
    if not is_owner(ctx): return
    set_setting(ctx.guild.id,"progress_duration",seconds)
    await ctx.send(f"✅ Progress bar duration set to {seconds} seconds.")

@bot.command()
async def set_progress_speed(ctx, seconds: int):
    if not is_owner(ctx): return
    set_setting(ctx.guild.id,"progress_speed",seconds)
    await ctx.send(f"✅ Progress bar speed set to {seconds} seconds per step.")

@bot.command()
async def set_max_recent(ctx, number: int):
    if not is_owner(ctx): return
    set_setting(ctx.guild.id,"max_recent",number)
    await ctx.send(f"✅ Max recent claims set to {number}.")

# ================= MODULE MANAGEMENT =================

@bot.command()
async def add_module(ctx, module_name: str):
    if not is_owner(ctx): return
    folder = get_server_stock(ctx.guild.id)
    path = os.path.join(folder, f"{module_name}.txt")
    if os.path.exists(path):
        await ctx.send("Module exists.")
        return
    open(path,"w").close()
    await ctx.send(f"Module {module_name} created.")

@bot.command()
async def remove_module(ctx, module_name: str):
    if not is_owner(ctx): return
    folder = get_server_stock(ctx.guild.id)
    path = os.path.join(folder, f"{module_name}.txt")
    if not os.path.exists(path):
        await ctx.send("Module does not exist.")
        return
    os.remove(path)
    await ctx.send(f"Module {module_name} removed.")

@bot.command()
async def add_stock(ctx, module_name: str, *, accounts: str):
    if not is_owner(ctx): return
    folder = get_server_stock(ctx.guild.id)
    path = os.path.join(folder, f"{module_name}.txt")
    if not os.path.exists(path):
        await ctx.send("Module does not exist.")
        return
    current = get_stock(ctx.guild.id,module_name)
    new_accounts = [a.strip() for a in accounts.split(",") if a.strip()]
    save_stock(ctx.guild.id,module_name, current+new_accounts)
    await ctx.send(f"Added {len(new_accounts)} accounts to {module_name}.")

@bot.command()
async def remove_stock(ctx, module_name: str, *, accounts: str):
    if not is_owner(ctx): return
    current = get_stock(ctx.guild.id,module_name)
    to_remove = [a.strip() for a in accounts.split(",")]
    new_stock = [acc for acc in current if acc not in to_remove]
    save_stock(ctx.guild.id,module_name,new_stock)
    await ctx.send(f"Removed {len(current)-len(new_stock)} accounts from {module_name}.")

# ================= RUN BOT =================

auto_check_claims.start()
bot.run(TOKEN)
