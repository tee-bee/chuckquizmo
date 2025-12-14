import discord
import os
import asyncio
import sys          
import importlib
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# 1. Load environment variables from .env file
load_dotenv()

# 2. Retrieve the token
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit()

ADMIN_IDS = [368792134645448704, 193855542366568448]

ADMIN_IDS = [368792134645448704, 193855542366568448]

# 1. Define the Autocomplete Logic
async def reload_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    
    # Scan cogs folder
    if os.path.exists('./cogs'):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                name = f"cogs.{filename[:-3]}"
                if current.lower() in name.lower():
                    choices.append(app_commands.Choice(name=name, value=name))
                    
    # Scan utils folder
    if os.path.exists('./utils'):
        for filename in os.listdir('./utils'):
            if filename.endswith('.py'):
                name = f"utils.{filename[:-3]}"
                if current.lower() in name.lower():
                    choices.append(app_commands.Choice(name=name, value=name))
                    
    return choices[:25]

# 2. Define the Slash Command
@app_commands.command(name="reload", description="Reload any file (Cogs or Utils)")
@app_commands.describe(extension="Select the file to reload")
@app_commands.autocomplete(extension=reload_autocomplete)
async def reload_cog(interaction: discord.Interaction, extension: str):
    await interaction.response.defer(ephemeral=True)

    if interaction.user.id not in ADMIN_IDS:
        await interaction.followup.send("⛔ Admin only.")
        return

    try:
        # Try reloading as a Discord Extension first (for Cogs)
        await interaction.client.reload_extension(extension)
        await interaction.client.tree.sync()
        await interaction.followup.send(f"✅ **Reloaded Extension:** `{extension}`")
        
    except commands.ExtensionNotLoaded:
        try:
            # If not loaded, try loading it
            await interaction.client.load_extension(extension)
            await interaction.client.tree.sync()
            await interaction.followup.send(f"✅ **Loaded New Extension:** `{extension}`")
        except Exception as e:
             await interaction.followup.send(f"❌ **Load Error:** `{e}`")

    except commands.NoEntryPointError:
        # This error means it's a Python file but not a Cog (e.g., utils.classes)
        try:
            if extension in sys.modules:
                importlib.reload(sys.modules[extension])
                await interaction.followup.send(f"✅ **Reloaded Module:** `{extension}`\n*(Tip: You may need to reload cogs that use this module to see changes)*")
            else:
                importlib.import_module(extension)
                await interaction.followup.send(f"✅ **Imported Module:** `{extension}`")
        except Exception as e:
            await interaction.followup.send(f"❌ **Module Error:** `{e}`")

    except Exception as e:
        await interaction.followup.send(f"❌ **Error:** `{e}`")

class QuizBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix='/', intents=intents)

    async def setup_hook(self):
        # Load all cogs from the cogs folder
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        print(f"Loaded extension: {filename}")
                    except Exception as e:
                        print(f"Failed to load extension {filename}: {e}")
       
        self.tree.add_command(reload_cog)
        
        # Sync slash commands
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
        # Handle graceful shutdown on Ctrl+C
        pass