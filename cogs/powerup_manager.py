import discord
from discord import app_commands
from discord.ext import commands
from utils.classes import CustomPowerUp, EffectType
from utils.data_manager import load_powerups, save_all_powerups

ADMIN_IDS = [368792134645448704, 193855542366568448]

def is_hardcoded_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_IDS

# --- NEW: The Browser View ---
class PowerupBrowser(discord.ui.View):
    def __init__(self, powerups):
        super().__init__(timeout=300)
        self.powerups = powerups
        self.index = 0
        self.mode = "scroll" # 'scroll' or 'list'
        self.update_buttons()

    def get_current_embed(self) -> discord.Embed:
        if not self.powerups:
            return discord.Embed(title="No Powerups Found", color=discord.Color.red())

        if self.mode == "scroll":
            # SINGLE ITEM VIEW
            p = self.powerups[self.index]
            embed = discord.Embed(
                title=f"{p.icon} {p.name}",
                description=f"**Description:**\n{p.description}",
                color=0x00ff00
            )
            embed.add_field(name="Effect Type", value=f"`{p.effect}`", inline=True)
            embed.add_field(name="Value", value=f"`{p.value}`", inline=True)
            embed.set_footer(text=f"Item {self.index + 1} of {len(self.powerups)}")
            return embed
        
        else:
            # FULL LIST VIEW
            embed = discord.Embed(title=f"📜 Full Powerup List ({len(self.powerups)})", color=0x00ffff)
            desc = ""
            for i, p in enumerate(self.powerups):
                desc += f"`#{i+1}` **{p.icon} {p.name}** ({p.effect})\n"
            
            # Discord limits descriptions to 4096 chars, unlikely to hit with powerups but good to know
            embed.description = desc
            return embed

    def update_buttons(self):
        # Update button states based on mode and index
        if self.mode == "list":
            # In list mode, nav buttons are useless
            self.prev_btn.disabled = True
            self.next_btn.disabled = True
            self.mode_btn.label = "Switch to Scroll View"
            self.mode_btn.style = discord.ButtonStyle.primary
        else:
            # In scroll mode, check boundaries
            self.prev_btn.disabled = (self.index == 0)
            self.next_btn.disabled = (self.index == len(self.powerups) - 1)
            self.mode_btn.label = "Switch to List View"
            self.mode_btn.style = discord.ButtonStyle.secondary

    @discord.ui.button(label="◀️ Prev", style=discord.ButtonStyle.gray, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Switch View", style=discord.ButtonStyle.secondary, row=0)
    async def mode_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Toggle Mode
        self.mode = "list" if self.mode == "scroll" else "scroll"
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.gray, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.powerups) - 1:
            self.index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

# --- CREATION MODAL ---
class PowerUpModal(discord.ui.Modal, title="Create Custom Power-up"):
    name = discord.ui.TextInput(label="Name", placeholder="e.g. Mega Boost")
    description = discord.ui.TextInput(label="Description", placeholder="e.g. Multiplies score by 5x")
    icon = discord.ui.TextInput(label="Icon (Emoji)", placeholder="🚀", max_length=5)
    value_input = discord.ui.TextInput(label="Value (Number)", placeholder="e.g. 3.0 (for multiplier) or 500 (for bonus)")

    def __init__(self, effect_type):
        super().__init__()
        self.effect_type = effect_type

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = float(self.value_input.value)
        except ValueError:
            await interaction.response.send_message("❌ Value must be a number.", ephemeral=True)
            return

        new_p = CustomPowerUp(
            name=self.name.value,
            description=self.description.value,
            effect=self.effect_type,
            value=val,
            icon=self.icon.value
        )
        
        current_list = load_powerups()
        current_list.append(new_p)
        save_all_powerups(current_list)
        
        await interaction.response.send_message(f"✅ Created Power-up: **{new_p.name}** ({self.effect_type})", ephemeral=True)

class PowerUpManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.group = app_commands.Group(name="powerup", description="Manage Game Powerups")

    @app_commands.command(name="create", description="Create a new powerup")
    @app_commands.describe(effect_type="What does this powerup do?")
    @app_commands.choices(effect_type=[
        app_commands.Choice(name="Score Multiplier (e.g. 3x)", value=EffectType.MULTIPLIER),
        app_commands.Choice(name="Flat Bonus Points (e.g. +500)", value=EffectType.FLAT_BONUS),
        app_commands.Choice(name="Add to Streak (e.g. +2)", value=EffectType.STREAK_ADD),
    ])
    async def create(self, interaction: discord.Interaction, effect_type: str):
        if not is_hardcoded_admin(interaction):
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return
        
        await interaction.response.send_modal(PowerUpModal(effect_type))

    @app_commands.command(name="list", description="Browse all active powerups")
    async def list_powerups(self, interaction: discord.Interaction):
        if not is_hardcoded_admin(interaction):
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return
            
        powerups = load_powerups()
        if not powerups:
            await interaction.response.send_message("No powerups found.", ephemeral=True)
            return

        # Initialize the Browser View
        view = PowerupBrowser(powerups)
        embed = view.get_current_embed()
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="delete", description="Delete a powerup by name")
    async def delete_powerup(self, interaction: discord.Interaction, name: str):
        if not is_hardcoded_admin(interaction):
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return
            
        powerups = load_powerups()
        # Filter out the one to delete
        new_list = [p for p in powerups if p.name.lower() != name.lower()]
        
        if len(new_list) == len(powerups):
            await interaction.response.send_message(f"❌ Could not find powerup named '{name}'", ephemeral=True)
        else:
            save_all_powerups(new_list)
            await interaction.response.send_message(f"🗑️ Deleted **{name}**.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(PowerUpManager(bot))
    bot.tree.add_command(PowerUpManager(bot).group)