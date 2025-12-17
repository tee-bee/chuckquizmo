import discord
import os
import asyncio
import sys
import importlib
import time
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# 1. Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit()

ADMIN_IDS = [368792134645448704, 193855542366568448]

# --- AUTOCOMPLETE & RELOAD COMMAND ---

async def reload_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    # Scan cogs
    if os.path.exists('./cogs'):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                name = f"cogs.{filename[:-3]}"
                if current.lower() in name.lower():
                    choices.append(app_commands.Choice(name=name, value=name))
    # Scan utils
    if os.path.exists('./utils'):
        for filename in os.listdir('./utils'):
            if filename.endswith('.py'):
                name = f"utils.{filename[:-3]}"
                if current.lower() in name.lower():
                    choices.append(app_commands.Choice(name=name, value=name))
    return choices[:25]

@app_commands.command(name="reload", description="Reload any file (Cogs or Utils)")
@app_commands.describe(extension="Select the file to reload")
@app_commands.autocomplete(extension=reload_autocomplete)
async def reload_cog(interaction: discord.Interaction, extension: str):
    await interaction.response.defer(ephemeral=True)

    if interaction.user.id not in ADMIN_IDS:
        await interaction.followup.send("⛔ Admin only.")
        return

    try:
        # Try reloading as a Discord Extension (Cogs)
        await interaction.client.reload_extension(extension)
        await interaction.client.tree.sync()
        
        # UPDATE TIMESTAMP
        interaction.client.extension_times[extension] = time.time()
        
        await interaction.followup.send(f"✅ **Reloaded Extension:** `{extension}`")
        
    except commands.ExtensionNotLoaded:
        try:
            # Try loading it as new
            await interaction.client.load_extension(extension)
            await interaction.client.tree.sync()
            
            # UPDATE TIMESTAMP
            interaction.client.extension_times[extension] = time.time()
            
            await interaction.followup.send(f"✅ **Loaded New Extension:** `{extension}`")
        except Exception as e:
             await interaction.followup.send(f"❌ **Load Error:** `{e}`")

    except commands.NoEntryPointError:
        # Python module reload (Utils)
        try:
            if extension in sys.modules:
                importlib.reload(sys.modules[extension])
                await interaction.followup.send(f"✅ **Reloaded Module:** `{extension}`")
            else:
                importlib.import_module(extension)
                await interaction.followup.send(f"✅ **Imported Module:** `{extension}`")
        except Exception as e:
            await interaction.followup.send(f"❌ **Module Error:** `{e}`")

    except Exception as e:
        await interaction.followup.send(f"❌ **Error:** `{e}`")

# --- BOT CLASS ---

class QuizBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix='/', intents=intents)
        
        # TIMING TRACKERS
        self.boot_time = time.time()
        self.extension_times = {}

    async def setup_hook(self):
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    ext_name = f'cogs.{filename[:-3]}'
                    try:
                        await self.load_extension(ext_name)
                        # RECORD LOAD TIME
                        self.extension_times[ext_name] = time.time()
                        print(f"Loaded extension: {filename}")
                    except Exception as e:
                        print(f"Failed to load extension {filename}: {e}")
        
        self.tree.add_command(reload_cog)
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

async def main():
    bot = QuizBot()
    async with bot:
        await bot.start(TOKEN)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass