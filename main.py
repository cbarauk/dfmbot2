import os
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

# --- Environment Variables ---
TOKEN = os.getenv("DISCORD_TOKEN")
ENFORCER_ROLE_ID = os.getenv("ENFORCER_ROLE_ID", "").strip()
DB_PATH = "/data/data.db"  # Railway volume mount path


def get_enforcer_mention() -> str:
    if not ENFORCER_ROLE_ID:
        return "@unknown-role"

    try:
        int(ENFORCER_ROLE_ID)
    except ValueError:
        return "@unknown-role"

    return f"<@&{ENFORCER_ROLE_ID}>"

# --- Intents Setup ---
# We need message_content and members intents for the bot to read messages and ping roles properly
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Database Initialization ---
async def init_db():
    # The directory /data should exist if mounted as a volume on Railway.
    # If testing locally, uncomment the following lines:
    # import os
    # os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS heist_stats (
                user_id INTEGER NOT NULL,
                heist_type TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, heist_type)
            )
        """)
        await db.commit()

async def get_heist_totals():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT heist_type, SUM(count) FROM heist_stats GROUP BY heist_type"
        )
        rows = await cursor.fetchall()

    totals = {row[0]: int(row[1] or 0) for row in rows}
    return {
        "Fleeca": totals.get("Fleeca", 0),
        "Store": totals.get("Store", 0),
        "ATM": totals.get("ATM", 0),
    }


async def get_leaderboard_entries(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, heist_type, count FROM heist_stats ORDER BY user_id ASC"
        )
        rows = await cursor.fetchall()

    grouped = {}
    for user_id, heist_type, count in rows:
        grouped.setdefault(user_id, {"Fleeca": 0, "Store": 0, "ATM": 0})
        if heist_type in {"Fleeca", "Store", "ATM"}:
            grouped[user_id][heist_type] = int(count)

    leaderboard = []
    for user_id, stats in grouped.items():
        total = stats["Fleeca"] + stats["Store"] + stats["ATM"]
        leaderboard.append((user_id, total, stats))

    leaderboard.sort(key=lambda item: item[1], reverse=True)
    return leaderboard[:limit]


def build_leaderboard_text(entries):
    if not entries:
        return "Leaderboard:\nNo scores yet."

    numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["Leaderboard:"]
    for index, (user_id, total, stats) in enumerate(entries, start=1):
        fleeca = stats.get("Fleeca", 0)
        store = stats.get("Store", 0)
        atm = stats.get("ATM", 0)
        medal = numbers[index - 1] if index - 1 < len(numbers) else f"{index}."
        lines.append(f"{medal} <@{user_id}> - {total} total • 🏦 {fleeca} • 🏪 {store} • 💳 {atm}")
    return "\n".join(lines)


async def refresh_leaderboard(channel):
    entries = await get_leaderboard_entries()
    leaderboard_message = build_leaderboard_text(entries)

    if channel is None:
        return

    async for message in channel.history(limit=30):
        if message.author == bot.user and message.content.startswith("Leaderboard:"):
            await message.edit(content=leaderboard_message)
            return

    await channel.send(leaderboard_message)


def build_heist_tracker_embed(totals=None):
    totals = totals or {"Fleeca": 0, "Store": 0, "ATM": 0}
    embed = discord.Embed(
        title="DFM Heist Progress",
        description="Click a button below to update the team heist tracker.",
        color=discord.Color.dark_gold()
    )
    embed.add_field(name="Fleeca", value=f"{totals['Fleeca']}/5", inline=True)
    embed.add_field(name="Store", value=f"{totals['Store']}/5", inline=True)
    embed.add_field(name="ATM", value=f"{totals['ATM']}/5", inline=True)
    return embed


# --- Persistent UI View ---
class HeistView(discord.ui.View):
    def __init__(self):
        # timeout=None makes the view persistent across restarts
        super().__init__(timeout=None)

    async def refresh_tracker_message(self, interaction: discord.Interaction):
        if interaction.message is None:
            return

        totals = await get_heist_totals()
        await interaction.message.edit(embed=build_heist_tracker_embed(totals), view=self)
        if interaction.channel is not None:
            await refresh_leaderboard(interaction.channel)

    async def handle_heist(self, interaction: discord.Interaction, heist_name: str):
        if not ENFORCER_ROLE_ID:
            await interaction.response.send_message(
                "Configuration error: the Enforcer role ID is missing. Please set ENFORCER_ROLE_ID to a valid Discord role ID.",
                ephemeral=False
            )
            return

        try:
            int(ENFORCER_ROLE_ID)
        except ValueError:
            await interaction.response.send_message(
                "Configuration error: ENFORCER_ROLE_ID must be a valid numeric Discord role ID.",
                ephemeral=False
            )
            return

        await interaction.response.defer()

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT count FROM heist_stats WHERE user_id = ? AND heist_type = ?",
                (interaction.user.id, heist_name)
            )
            row = await cursor.fetchone()
            current_count = row[0] if row else 0

            new_count = current_count + 1

            if new_count >= 5:
                await db.execute(
                    "INSERT INTO heist_stats (user_id, heist_type, count) VALUES (?, ?, 0) "
                    "ON CONFLICT(user_id, heist_type) DO UPDATE SET count = 0",
                    (interaction.user.id, heist_name)
                )
                await db.commit()

                enforcer_mention = get_enforcer_mention()
                user_mention = interaction.user.mention
                if interaction.message is not None:
                    await interaction.message.edit(
                        embed=build_heist_tracker_embed(await get_heist_totals()),
                        view=self
                    )
                    if interaction.channel is not None:
                        await refresh_leaderboard(interaction.channel)
                await interaction.followup.send(
                    f"{enforcer_mention}, {user_mention} has hit 5/5 on {heist_name} and needs their prestige!"
                )
            else:
                await db.execute(
                    "INSERT INTO heist_stats (user_id, heist_type, count) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, heist_type) DO UPDATE SET count = ?",
                    (interaction.user.id, heist_name, new_count, new_count)
                )
                await db.commit()

                if interaction.message is not None:
                    await self.refresh_tracker_message(interaction)

    @discord.ui.button(label="🏦 Fleeca", style=discord.ButtonStyle.primary, custom_id="heist_btn_fleeca")
    async def fleeca_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_heist(interaction, "Fleeca")

    @discord.ui.button(label="🏪 Store", style=discord.ButtonStyle.success, custom_id="heist_btn_store")
    async def store_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_heist(interaction, "Store")

    @discord.ui.button(label="💳 ATM", style=discord.ButtonStyle.secondary, custom_id="heist_btn_atm")
    async def atm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_heist(interaction, "ATM")


# --- Bot Events ---
@bot.event
async def on_ready():
    await init_db()
    # Register the persistent view
    bot.add_view(HeistView())
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    # --- AUTO POST TO SPECIFIC CHANNEL ---
    TARGET_CHANNEL_ID = 1539033757200158871
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    
    if channel is None:
        print(f"ERROR: Could not find channel with ID {TARGET_CHANNEL_ID}. Make sure the bot is in the server.")
    else:
        # Check if a heist menu is already in the channel to prevent spam on restarts
        async for message in channel.history(limit=50):
            if message.author == bot.user and message.components:
                print("Heist menu already exists in the channel. Skipping auto-post.")
                break
        else:
            # If no existing menu was found, post a new one
            embed = build_heist_tracker_embed(await get_heist_totals())
            await channel.send(embed=embed, view=HeistView())
            await refresh_leaderboard(channel)
            print(f"Successfully posted heist menu to channel: {channel.name}")

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


# --- Slash Commands ---
@bot.tree.command(name="setup_heists", description="Post the persistent heist progression buttons.")
@app_commands.default_permissions(administrator=True)
async def setup_heists(interaction: discord.Interaction):
    embed = build_heist_tracker_embed(await get_heist_totals())
    # Pass the persistent view to the message
    await interaction.channel.send(embed=embed, view=HeistView())
    await refresh_leaderboard(interaction.channel)
    await interaction.response.send_message("Heist buttons posted successfully!", ephemeral=True)


@bot.tree.command(name="resetdfm", description="Reset the heist tracker values.")
@app_commands.default_permissions(administrator=True)
async def resetdfm(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM heist_stats")
        await db.commit()

    await interaction.response.send_message("DFM heist tracker has been reset.", ephemeral=True)

    async for message in interaction.channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            if "DFM Heist Progress" in message.embeds[0].title:
                await message.edit(embed=build_heist_tracker_embed({"Fleeca": 0, "Store": 0, "ATM": 0}), view=HeistView())
                break

    await refresh_leaderboard(interaction.channel)


@bot.tree.command(name="resetleaderboard", description="Reset the leaderboard message.")
@app_commands.default_permissions(administrator=True)
async def resetleaderboard(interaction: discord.Interaction):
    async for message in interaction.channel.history(limit=20):
        if message.author == bot.user and message.content.startswith("Leaderboard:"):
            await message.edit(content="Leaderboard:\nNo scores yet.")
            break

    await interaction.response.send_message("Leaderboard reset.", ephemeral=True)


@bot.tree.command(name="adddfm", description="Add a heist count to a specific user.")
@app_commands.default_permissions(administrator=True)
async def adddfm(
    interaction: discord.Interaction,
    user: discord.Member,
    heist: str,
    amount: int = 1,
):
    normalized_heist = heist.title()
    if normalized_heist not in {"Fleeca", "Store", "ATM"}:
        await interaction.response.send_message("Heist must be Fleeca, Store, or ATM.", ephemeral=True)
        return

    if amount < 1:
        await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT count FROM heist_stats WHERE user_id = ? AND heist_type = ?",
            (user.id, normalized_heist)
        )
        row = await cursor.fetchone()
        current_count = row[0] if row else 0

        new_count = current_count + amount
        await db.execute(
            "INSERT INTO heist_stats (user_id, heist_type, count) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, heist_type) DO UPDATE SET count = ?",
            (user.id, normalized_heist, new_count, new_count)
        )
        await db.commit()

    await interaction.response.send_message(
        f"Added {amount} to {user.mention} for {normalized_heist}. They now have {new_count}/5.",
        ephemeral=True
    )

    async for message in interaction.channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            if "DFM Heist Progress" in message.embeds[0].title:
                await message.edit(embed=build_heist_tracker_embed(await get_heist_totals()), view=HeistView())
                break

    await refresh_leaderboard(interaction.channel)


@bot.tree.command(name="removedfm", description="Remove a heist count from a specific user.")
@app_commands.default_permissions(administrator=True)
async def removedfm(
    interaction: discord.Interaction,
    user: discord.Member,
    heist: str,
    amount: int = 1,
):
    normalized_heist = heist.title()
    if normalized_heist not in {"Fleeca", "Store", "ATM"}:
        await interaction.response.send_message("Heist must be Fleeca, Store, or ATM.", ephemeral=True)
        return

    if amount < 1:
        await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT count FROM heist_stats WHERE user_id = ? AND heist_type = ?",
            (user.id, normalized_heist)
        )
        row = await cursor.fetchone()
        current_count = row[0] if row else 0

        new_count = max(0, current_count - amount)
        await db.execute(
            "INSERT INTO heist_stats (user_id, heist_type, count) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, heist_type) DO UPDATE SET count = ?",
            (user.id, normalized_heist, new_count, new_count)
        )
        await db.commit()

    await interaction.response.send_message(
        f"Removed {amount} from {user.mention} for {normalized_heist}. They now have {new_count}/5.",
        ephemeral=True
    )

    async for message in interaction.channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            if "DFM Heist Progress" in message.embeds[0].title:
                await message.edit(embed=build_heist_tracker_embed(await get_heist_totals()), view=HeistView())
                break

    await refresh_leaderboard(interaction.channel)


@bot.tree.command(name="my_stats", description="View your current heist progression.")
async def my_stats(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT heist_type, count FROM heist_stats WHERE user_id = ?",
            (interaction.user.id,)
        )
        rows = await cursor.fetchall()

    # Format stats, defaulting to 0 if not found
    stats = {row[0]: row[1] for row in rows}
    fleeca = stats.get("Fleeca", 0)
    store = stats.get("Store", 0)
    atm = stats.get("ATM", 0)

    embed = discord.Embed(
        title="Your Heist Stats",
        color=discord.Color.blurple()
    )
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    embed.add_field(name="🏦 Fleeca", value=f"**{fleeca}/5**", inline=True)
    embed.add_field(name="🏪 Store", value=f"**{store}/5**", inline=True)
    embed.add_field(name="💳 ATM", value=f"**{atm}/5**", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN or not ENFORCER_ROLE_ID:
        raise ValueError("Missing DISCORD_TOKEN or ENFORCER_ROLE_ID environment variables.")
    bot.run(TOKEN)