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

# --- Persistent UI View ---
class HeistView(discord.ui.View):
    def __init__(self):
        # timeout=None makes the view persistent across restarts
        super().__init__(timeout=None)

    async def handle_heist(self, interaction: discord.Interaction, heist_name: str):
        if not ENFORCER_ROLE_ID:
            await interaction.response.send_message(
                "Configuration error: the Enforcer role ID is missing. Please set ENFORCER_ROLE_ID to a valid Discord role ID.",
                ephemeral=False
            )
            return

        if ENFORCER_ROLE_ID != str(int(ENFORCER_ROLE_ID)):
            await interaction.response.send_message(
                "Configuration error: ENFORCER_ROLE_ID must be a valid numeric Discord role ID.",
                ephemeral=False
            )
            return

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
                await interaction.response.send_message(
                    f"{enforcer_mention}, {user_mention} has hit 5/5 on {heist_name} and needs their prestige!"
                )
            else:
                await db.execute(
                    "INSERT INTO heist_stats (user_id, heist_type, count) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, heist_type) DO UPDATE SET count = ?",
                    (interaction.user.id, heist_name, new_count, new_count)
                )
                await db.commit()

                await interaction.response.send_message(
                    f"{interaction.user.mention} is now {new_count}/5 on {heist_name}."
                )

    @discord.ui.button(label="Fleeca", style=discord.ButtonStyle.primary, custom_id="heist_btn_fleeca")
    async def fleeca_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_heist(interaction, "Fleeca")

    @discord.ui.button(label="Store", style=discord.ButtonStyle.success, custom_id="heist_btn_store")
    async def store_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_heist(interaction, "Store")

    @discord.ui.button(label="ATM", style=discord.ButtonStyle.secondary, custom_id="heist_btn_atm")
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
            embed = discord.Embed(
                title="Heist Progression System",
                description="Click a button below to log your heist progress!\n\n"
                            "**Fleeca** | **Store** | **ATM**\n"
                            "Hitting 5/5 will alert an Enforcer for prestige.",
                color=discord.Color.dark_gold()
            )
            await channel.send(embed=embed, view=HeistView())
            print(f"Successfully posted heist menu to channel: {channel.name}")

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


# --- Slash Commands ---
@bot.tree.command(name="setup_heists", description="Post the persistent heist progression buttons.")
@app_commands.default_permissions(administrator=True)
async def setup_heists(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Heist Progression System",
        description="Click a button below to log your heist progress!\n\n"
                    "**Fleeca** | **Store** | **ATM**\n"
                    "Hitting 5/5 will alert an Enforcer for prestige.",
        color=discord.Color.dark_gold()
    )
    # Pass the persistent view to the message
    await interaction.channel.send(embed=embed, view=HeistView())
    await interaction.response.send_message("Heist buttons posted successfully!", ephemeral=True)


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