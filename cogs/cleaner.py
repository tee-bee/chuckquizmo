import discord
from discord import app_commands
from discord.ext import commands
from utils.data_manager import get_quiz_lookup, delete_quiz_file
from utils.db_manager import get_session_lookup, delete_session, delete_sessions_range

# Use the same permission constants/logic
ADMIN_IDS = [368792134645448704, 193855542366568448]
SERVER_ID = 238080556708003851
ROLE_ID = 983357933565919252

def is_privileged(interaction: discord.Interaction) -> bool:
    if interaction.user.id in ADMIN_IDS: return True
    if interaction.guild_id == SERVER_ID:
        role = interaction.guild.get_role(ROLE_ID)
        if role and role in interaction.user.roles: return True
    return False

# --- UI VIEWS ---

class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.value = None

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        # Button interactions must also be deferred or handled
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()

class MultiQuizSelect(discord.ui.Select):
    def __init__(self, quiz_map):
        options = []
        # Limit to 25 items due to Discord restrictions
        for name, filename in list(quiz_map.items())[:25]:
            options.append(discord.SelectOption(label=name, value=name))
        super().__init__(placeholder="Select quizzes to delete...", min_values=1, max_values=len(options))

    async def callback(self, interaction: discord.Interaction):
        # Defer the dropdown selection immediately
        await interaction.response.defer()

class MultiQuizDeleteView(discord.ui.View):
    def __init__(self, quiz_map):
        super().__init__(timeout=60)
        self.select = MultiQuizSelect(quiz_map)
        self.add_item(self.select)
        self.selected_quizzes = []

    @discord.ui.button(label="Delete Selected", style=discord.ButtonStyle.danger, row=1)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer button click
        await interaction.response.defer()
        
        if not self.select.values:
            await interaction.followup.send("No quizzes selected.", ephemeral=True)
            return
        
        count = 0
        for name in self.select.values:
            if delete_quiz_file(name):
                count += 1
        
        # Disable view after action
        self.stop()
        await interaction.edit_original_response(content=f"🗑️ **Deleted {count} quizzes.**", view=None)

# --- COG ---

class Cleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.group = app_commands.Group(name="clear", description="Deletion tools for Quizzes and History")

    async def quiz_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        quiz_map = get_quiz_lookup()
        choices = []
        for real_name, filename in quiz_map.items():
            if current.lower() in real_name.lower():
                choices.append(app_commands.Choice(name=real_name, value=real_name))
        return choices[:25]

    async def session_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        sessions = get_session_lookup(limit=25)
        choices = []
        for s in sessions:
            if current.lower() in s['label'].lower():
                choices.append(app_commands.Choice(name=s['label'], value=s['id']))
        return choices[:25]

    # 1. DELETE SPECIFIC QUIZ
    @app_commands.command(name="quiz", description="Delete a specific quiz file")
    @app_commands.describe(name="The name of the quiz to delete")
    @app_commands.autocomplete(name=quiz_autocomplete)
    async def clear_quiz(self, interaction: discord.Interaction, name: str):
        # DEFER HERE
        await interaction.response.defer(ephemeral=True)

        if not is_privileged(interaction):
            await interaction.followup.send("⛔ Admin Only.")
            return

        view = ConfirmView()
        await interaction.followup.send(f"⚠️ Are you sure you want to delete quiz **{name}**?", view=view)
        await view.wait()
        
        if view.value:
            if delete_quiz_file(name):
                await interaction.edit_original_response(content=f"✅ Deleted **{name}**.", view=None)
            else:
                await interaction.edit_original_response(content=f"❌ Could not find **{name}**.", view=None)
        else:
            await interaction.edit_original_response(content="❌ Cancelled.", view=None)

    # 2. DELETE SPECIFIC SESSION
    @app_commands.command(name="session", description="Delete a specific history session")
    @app_commands.describe(session_id="The ID of the session to delete")
    @app_commands.autocomplete(session_id=session_autocomplete)
    async def clear_session(self, interaction: discord.Interaction, session_id: int):
        # DEFER HERE
        await interaction.response.defer(ephemeral=True)

        if not is_privileged(interaction):
            await interaction.followup.send("⛔ Admin Only.")
            return

        view = ConfirmView()
        await interaction.followup.send(f"⚠️ Delete Session ID **{session_id}** (and all its stats)?", view=view)
        await view.wait()

        if view.value:
            delete_session(session_id)
            await interaction.edit_original_response(content=f"✅ Deleted Session **{session_id}**.", view=None)
        else:
            await interaction.edit_original_response(content="❌ Cancelled.", view=None)

    # 3. DELETE MULTIPLE QUIZZES
    @app_commands.command(name="quizzes_select", description="Select multiple quizzes to delete")
    async def clear_quizzes_select(self, interaction: discord.Interaction):
        # DEFER HERE
        await interaction.response.defer(ephemeral=True)

        if not is_privileged(interaction):
            await interaction.followup.send("⛔ Admin Only.")
            return

        quiz_map = get_quiz_lookup()
        if not quiz_map:
            await interaction.followup.send("No quizzes found.")
            return

        view = MultiQuizDeleteView(quiz_map)
        await interaction.followup.send("👇 Select quizzes to delete (Max 25 at a time):", view=view)

    # 4. DELETE SESSION RANGE
    @app_commands.command(name="sessions_range", description="Delete a range of history sessions")
    @app_commands.describe(start_id="Start of Session ID Range", end_id="End of Session ID Range")
    @app_commands.autocomplete(start_id=session_autocomplete, end_id=session_autocomplete)
    async def clear_sessions_range(self, interaction: discord.Interaction, start_id: int, end_id: int):
        # DEFER HERE
        await interaction.response.defer(ephemeral=True)

        if not is_privileged(interaction):
            await interaction.followup.send("⛔ Admin Only.")
            return

        low = min(start_id, end_id)
        high = max(start_id, end_id)
        
        view = ConfirmView()
        await interaction.followup.send(f"⚠️ **DANGER:** Delete ALL sessions between ID **{low}** and **{high}**?", view=view)
        await view.wait()

        if view.value:
            count = delete_sessions_range(start_id, end_id)
            await interaction.edit_original_response(content=f"✅ Deleted **{count}** sessions (and related data).", view=None)
        else:
            await interaction.edit_original_response(content="❌ Cancelled.", view=None)

async def setup(bot):
    await bot.add_cog(Cleaner(bot))
    # Add the group to the tree explicitly
    bot.tree.add_command(Cleaner(bot).group)