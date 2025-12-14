import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from utils.classes import Quiz, Question, QuestionType
from utils.data_manager import save_quiz, load_quiz, get_quiz_lookup

ADMIN_IDS = [368792134645448704, 193855542366568448]

def is_hardcoded_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_IDS

def get_correct_answer_str(question: Question) -> str:
    if question.type == QuestionType.REORDER:
        return "Sequence: " + " -> ".join([chr(65+i) for i in question.correct_indices])
    parts = []
    for idx in question.correct_indices:
        if idx < len(question.options):
            label = chr(65 + idx)
            text = question.options[idx]
            parts.append(f"**{label}** ({text})")
    return ", ".join(parts)

# --- SETTINGS MODAL ---
class QuestionSettingsModal(discord.ui.Modal, title="Extra Settings"):
    time_limit = discord.ui.TextInput(label="Time Limit (seconds)", placeholder="30", default="30")
    weight = discord.ui.TextInput(label="Weight Multiplier", placeholder="1.0", default="1.0")
    explanation = discord.ui.TextInput(label="Failure Explanation", style=discord.TextStyle.paragraph, required=False, placeholder="Shown when wrong...")

    def __init__(self, parent_view, question_obj):
        super().__init__()
        self.parent_view = parent_view
        self.question_obj = question_obj
        self.time_limit.default = str(question_obj.time_limit)
        self.weight.default = str(question_obj.weight)
        self.explanation.default = question_obj.explanation or ""

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.question_obj.time_limit = int(self.time_limit.value)
            self.question_obj.weight = float(self.weight.value)
            self.question_obj.explanation = self.explanation.value if self.explanation.value.strip() else None
            
            save_quiz(self.parent_view.quiz)
            await interaction.response.send_message("✅ Settings updated!", ephemeral=True)
            if hasattr(self.parent_view, 'refresh_display'):
                await self.parent_view.refresh_display(interaction)
        except ValueError:
            await interaction.response.send_message("❌ Invalid number format.", ephemeral=True)

class EditQuestionModal(discord.ui.Modal, title="Edit Text & Options"):
    # LIMIT: 5 Components Max (1 Question + 4 Options)
    question_text = discord.ui.TextInput(label="Question Text", style=discord.TextStyle.paragraph)
    opt_a = discord.ui.TextInput(label="Option A", placeholder="Answer...")
    opt_b = discord.ui.TextInput(label="Option B", placeholder="Answer...")
    opt_c = discord.ui.TextInput(label="Option C", placeholder="Answer...", required=False)
    opt_d = discord.ui.TextInput(label="Option D", placeholder="Answer...", required=False)
    
    def __init__(self, parent_view, question_obj):
        super().__init__()
        self.parent_view = parent_view
        self.question_obj = question_obj
        self.question_text.default = question_obj.text
        opts = question_obj.options
        if len(opts) > 0: self.opt_a.default = opts[0]
        if len(opts) > 1: self.opt_b.default = opts[1]
        if len(opts) > 2: self.opt_c.default = opts[2]
        if len(opts) > 3: self.opt_d.default = opts[3]

    async def on_submit(self, interaction: discord.Interaction):
        raw_opts = [self.opt_a.value, self.opt_b.value, self.opt_c.value, self.opt_d.value]
        final_opts = [o for o in raw_opts if o.strip()]
        
        if len(final_opts) < 2:
            await interaction.response.send_message("Need at least 2 options.", ephemeral=True)
            return

        # Go to Answer Selection
        view = UpdateAnswerSelectionView(self.parent_view, self.question_obj, self.question_text.value, final_opts)
        await interaction.response.send_message(f"**Updating:** {self.question_text.value}\n\n👇 **Select Correct Answer(s) / Order:**", view=view, ephemeral=True)

class UpdateAnswerSelectionView(discord.ui.View):
    def __init__(self, hub_view, question_obj, new_text, new_options):
        super().__init__(timeout=300)
        self.hub_view = hub_view
        self.question_obj = question_obj
        self.new_text = new_text
        self.new_options = new_options
        self.add_item(CorrectAnswerSelect(new_options))

    @discord.ui.button(label="Save Changes", style=discord.ButtonStyle.green)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        select = [child for child in self.children if isinstance(child, CorrectAnswerSelect)][0]
        if not select.values:
            await interaction.response.send_message("Select at least one!", ephemeral=True)
            return
            
        self.question_obj.text = self.new_text
        self.question_obj.options = self.new_options
        self.question_obj.correct_indices = [int(v) for v in select.values]
        
        # If Reorder, the select menu returns values in index order (0, 1, 2) usually sorted.
        # This basic implementation assumes the user selects them in the correct order if reorder is active,
        # OR that the list provided in the modal was already in the correct sequence.
        
        self.question_obj.allow_multi_select = len(self.question_obj.correct_indices) > 1
        save_quiz(self.hub_view.quiz)
        
        await interaction.response.edit_message(content="✅ **Updated!**", view=None)
        await self.hub_view.refresh_display(interaction)

class QuestionModal(discord.ui.Modal, title="New Question"):
    # LIMIT: 5 Components Max
    question_text = discord.ui.TextInput(label="Question Text", style=discord.TextStyle.paragraph)
    opt_a = discord.ui.TextInput(label="Option A", placeholder="Enter answer...")
    opt_b = discord.ui.TextInput(label="Option B", placeholder="Enter answer...")
    opt_c = discord.ui.TextInput(label="Option C", placeholder="Answer...", required=False)
    opt_d = discord.ui.TextInput(label="Option D", placeholder="Answer...", required=False)
    
    def __init__(self, hub_view, q_type=QuestionType.STANDARD):
        super().__init__()
        self.hub_view = hub_view
        self.q_type = q_type

    async def on_submit(self, interaction: discord.Interaction):
        raw_options = [self.opt_a.value, self.opt_b.value, self.opt_c.value, self.opt_d.value]
        final_options = [opt for opt in raw_options if opt.strip()]
        
        if len(final_options) < 2:
            await interaction.response.send_message("Need at least 2 options.", ephemeral=True)
            return

        view = AnswerSelectionView(self.hub_view, self.question_text.value, final_options, self.q_type)
        msg = "Select the CORRECT answer(s)." if self.q_type == QuestionType.STANDARD else "Select options IN THE CORRECT ORDER."
        await interaction.response.send_message(f"**Draft ({self.q_type}):** {self.question_text.value}\n{msg}", view=view, ephemeral=True)

class DeleteConfirmView(discord.ui.View):
    def __init__(self, parent_view, index):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.index = index
    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.parent_view.quiz.questions):
            self.parent_view.quiz.questions.pop(self.index)
            save_quiz(self.parent_view.quiz)
            await self.parent_view.hub_view.refresh_display(interaction)
            await interaction.response.edit_message(content="🗑️ **Deleted.**", view=None)
        else:
            await interaction.response.edit_message(content="❌ Error.", view=None)
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)

class CorrectAnswerSelect(discord.ui.Select):
    def __init__(self, options_list):
        choices = []
        labels = ["A", "B", "C", "D"]
        for i, opt_text in enumerate(options_list):
            if opt_text.strip():
                choices.append(discord.SelectOption(label=f"Option {labels[i]}", description=opt_text[:50], value=str(i)))
        super().__init__(placeholder="Select correct answer(s) / order...", min_values=1, max_values=len(choices), options=choices)
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

class AnswerSelectionView(discord.ui.View):
    def __init__(self, hub_view, question_text, options_list, q_type):
        super().__init__(timeout=300)
        self.hub_view = hub_view 
        self.question_text = question_text
        self.options_list = options_list
        self.q_type = q_type
        self.add_item(CorrectAnswerSelect(options_list))

    @discord.ui.button(label="Confirm & Add", style=discord.ButtonStyle.green, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        select_menu = [child for child in self.children if isinstance(child, CorrectAnswerSelect)][0]
        if not select_menu.values:
            await interaction.response.send_message("Select at least one!", ephemeral=True)
            return
        
        # Determine indices
        # For Reorder, we assume the user selects them in the correct order, OR we assume the text input order was correct.
        # Standard quizizz behavior: Create them in correct order.
        if self.q_type == QuestionType.REORDER:
            # If reorder, the correct answer is the sequence 0, 1, 2, 3 (based on how they typed it)
            # OR we can let them define it. For simplicity in this text-based modal, we assume inputs were correct order.
            correct_indices = list(range(len(self.options_list)))
        else:
            correct_indices = [int(v) for v in select_menu.values]

        new_question = Question(
            text=self.question_text,
            options=self.options_list,
            correct_indices=correct_indices,
            allow_multi_select=(len(correct_indices) > 1 and self.q_type == QuestionType.STANDARD),
            type=self.q_type
        )
        
        self.hub_view.quiz.questions.append(new_question)
        save_quiz(self.hub_view.quiz)
        
        await interaction.response.edit_message(content="✅ **Question Added!**", view=None, embed=None)
        await self.hub_view.refresh_display(interaction)

class QuestionSelector(discord.ui.Select):
    def __init__(self, quiz):
        options = []
        for i, q in enumerate(quiz.questions[:25]):
            # Increased limit to 95 chars (Discord max is 100)
            options.append(discord.SelectOption(label=f"Q{i+1}", description=q.text[:95], value=str(i)))
        if not options:
            options.append(discord.SelectOption(label="No questions yet", value="-1"))
        super().__init__(placeholder="Select a question to edit...", options=options, disabled=len(quiz.questions) == 0)
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "-1": return
        index = int(self.values[0])
        view = EditorControls(self.view.quiz, index, self.view)
        view.message = interaction.message 
        await interaction.response.edit_message(content=None, embed=view.get_embed(), view=view)

class QuizEditorHub(discord.ui.View):
    def __init__(self, quiz):
        super().__init__(timeout=None)
        self.quiz = quiz
        self.message = None
        self.refresh_components()
    def refresh_components(self):
        self.clear_items()
        self.add_item(QuestionSelector(self.quiz))
        self.add_item(self.add_std_btn)
        self.add_item(self.add_ord_btn)
        self.add_item(self.save_btn)
    def get_summary_embed(self):
        embed = discord.Embed(title=f"🛠️ Manager: {self.quiz.name}", color=0x2ECC71)
        if not self.quiz.questions:
            embed.description = "*No questions yet.*"
        else:
            desc = ""
            for i, q in enumerate(self.quiz.questions):
                ans_str = get_correct_answer_str(q)
                icon = "🔢" if q.type == QuestionType.REORDER else "📝"
                # Removed truncation here to show full question text
                line = f"`Q{i+1}` {icon} {q.text} → {ans_str}\n"
                if len(desc) + len(line) < 3800: desc += line
                else:
                    desc += "..."
                    break
            embed.description = desc
        embed.set_footer(text=f"Total: {len(self.quiz.questions)}")
        return embed
    
    @discord.ui.button(label="Add Standard Q", style=discord.ButtonStyle.green)
    async def add_std_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.quiz.questions) >= 25: return
        await interaction.response.send_modal(QuestionModal(self, QuestionType.STANDARD))

    @discord.ui.button(label="Add Reorder Q", style=discord.ButtonStyle.blurple)
    async def add_ord_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.quiz.questions) >= 25: return
        await interaction.response.send_modal(QuestionModal(self, QuestionType.REORDER))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **Closed.**", view=None, embed=None)
    async def refresh_display(self, interaction=None):
        self.refresh_components()
        try:
            if self.message: await self.message.edit(embed=self.get_summary_embed(), view=self)
        except discord.NotFound:
            if interaction: await interaction.followup.send(embed=self.get_summary_embed(), view=self, ephemeral=True)

class EditorControls(discord.ui.View):
    def __init__(self, quiz, index, hub_view):
        super().__init__(timeout=600)
        self.quiz = quiz
        self.index = index
        self.q = quiz.questions[index]
        self.hub_view = hub_view
        self.message = None 
    def get_embed(self):
        embed = discord.Embed(title=f"Editing Q{self.index+1} ({self.q.type})", description=self.q.text, color=0xFFA500)
        opts = ""
        for i, opt in enumerate(self.q.options):
            mark = "✅" if i in self.q.correct_indices else ""
            if self.q.type == QuestionType.REORDER: mark = f"[{self.q.correct_indices.index(i) + 1}]" if i in self.q.correct_indices else ""
            opts += f"{chr(65+i)}: {opt} {mark}\n"
        embed.add_field(name="Options", value=opts, inline=False)
        embed.add_field(name="Settings", value=f"Time: {self.q.time_limit}s\nWeight: {self.q.weight}x\nExplanation: {'Yes' if self.q.explanation else 'No'}", inline=False)
        if self.q.image_url: embed.set_thumbnail(url=self.q.image_url)
        return embed
    async def refresh_display(self, interaction=None):
        try:
            if self.message: await self.message.edit(embed=self.get_embed())
        except discord.NotFound:
            if interaction: await interaction.followup.send(content="Refreshed", embed=self.get_embed(), view=self, ephemeral=True)
    
    @discord.ui.button(label="Edit Text/Opts", style=discord.ButtonStyle.primary, row=0)
    async def edit_txt(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message 
        await interaction.response.send_modal(EditQuestionModal(self, self.q))
    
    @discord.ui.button(label="Settings", style=discord.ButtonStyle.secondary, row=0)
    async def edit_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message 
        await interaction.response.send_modal(QuestionSettingsModal(self, self.q))

    @discord.ui.button(label="Attach Image", style=discord.ButtonStyle.blurple, row=1)
    async def edit_img(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message
        await interaction.response.send_message("📷 **Upload Image:**", ephemeral=True)
        def check(m): return m.author == interaction.user and m.channel == interaction.channel and m.attachments
        try:
            msg = await interaction.client.wait_for('message', check=check, timeout=60)
            self.q.image_url = msg.attachments[0].url
            save_quiz(self.quiz)
            try: await msg.delete()
            except: pass
            await interaction.followup.send(f"✅ Attached!", ephemeral=True)
            await self.refresh_display(interaction)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Timed out.", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=1)
    async def delete_q(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DeleteConfirmView(self, self.index)
        await interaction.response.send_message(f"⚠️ Delete Q{self.index+1}?", view=view, ephemeral=True)

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.hub_view.refresh_display(interaction)
        await interaction.response.edit_message(content=None, embed=self.hub_view.get_summary_embed(), view=self.hub_view)

class QuizBuilder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    async def quiz_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        quiz_map = get_quiz_lookup()
        choices = []
        for real_name, filename in quiz_map.items():
            if current.lower() in real_name.lower():
                choices.append(app_commands.Choice(name=real_name, value=filename))
        return choices[:25]
    @app_commands.command(name="manage_quiz", description="Create or Edit a quiz")
    @app_commands.describe(mode="Choose Create to make new, Edit to modify existing")
    @app_commands.describe(name="The name of the quiz (Select existing for Edit, type new for Create)")
    @app_commands.choices(mode=[app_commands.Choice(name="Create New", value="create"), app_commands.Choice(name="Edit Existing", value="edit")])
    @app_commands.autocomplete(name=quiz_autocomplete)
    async def manage_quiz(self, interaction: discord.Interaction, mode: str, name: str):
        if not is_hardcoded_admin(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        quiz = load_quiz(name)
        if mode == "create":
            if quiz:
                await interaction.response.send_message(f"⚠️ **{name}** exists! Edit?", ephemeral=True)
                return
            quiz = Quiz(name=name, creator_id=interaction.user.id)
            save_quiz(quiz)
            msg = f"🆕 **Created:** {name}"
        else:
            if not quiz:
                await interaction.response.send_message(f"❌ **{name}** not found.", ephemeral=True)
                return
            msg = f"✏️ **Editing:** {name}"
        view = QuizEditorHub(quiz)
        await interaction.response.send_message(msg, embed=view.get_summary_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

async def setup(bot):
    await bot.add_cog(QuizBuilder(bot))