import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import time
import random
import os
import json
from utils.classes import Quiz, Question, Player, GameSession, CustomPowerUp, EffectType, QuestionType
from utils.data_manager import load_quiz, load_powerups, get_quiz_lookup
from utils.db_manager import (
    save_full_report, get_recent_sessions, get_session_details, 
    get_total_session_count, get_session_ids_by_limit, 
    get_leaderboard_data, get_roundup_data, get_session_lookup,
    get_history_page, check_results_sent, mark_results_sent
)

ADMIN_IDS = [368792134645448704, 193855542366568448]
SERVER_ID = 238080556708003851
ROLE_ID = 983357933565919252

def is_privileged(interaction: discord.Interaction) -> bool:
    # 1. Global Admin Override
    if interaction.user.id in ADMIN_IDS:
        return True
    
    # 2. Server Specific Role Check
    if interaction.guild_id == SERVER_ID:
        role = interaction.guild.get_role(ROLE_ID)
        if role and role in interaction.user.roles:
            return True
            
    return False
STATE_FILE = "data/active_sessions.json"


active_sessions = {}

# --- HELPERS ---

def register_new_player(session: GameSession, user: discord.User) -> Player:
    if user.id in session.players:
        return session.players[user.id]
    all_powerups = load_powerups()
    starter = random.sample(all_powerups, min(3, len(all_powerups))) if all_powerups else []
    new_player = Player(
        user_id=user.id, name=user.display_name, avatar_url=user.display_avatar.url, inventory=starter
    )
    total_q = len(session.quiz.questions)
    order = list(range(total_q))
    random.shuffle(order)
    new_player.question_order = order
    new_player.join_time = time.time()
    session.players[user.id] = new_player
    return new_player

def glitch_text(text: str) -> str:
    chars = list(text)
    for i in range(len(chars)):
        if random.random() < 0.3 and chars[i] != " ":
            chars[i] = random.choice(["#", "$", "%", "&", "@", "?", "!", "0", "1"])
    return "".join(chars)

def build_game_embed(player: Player, question: Question, question_num: int, rank_str: str, current_sequence=None, glitch_active=False) -> discord.Embed:
    q_text = question.text
    type_text = ""
    if question.type == QuestionType.REORDER: type_text = "(Order Sequence)"
    elif question.allow_multi_select: type_text = "(Multi-Select)"
    
    if glitch_active:
        q_text = glitch_text(q_text)
        type_text = glitch_text(type_text)
    
    embed = discord.Embed(title=f"Q{question_num}: {q_text} {type_text}", color=0x00ff00)
    embed.set_author(name=f"Score: {player.score} pts | Rank: {rank_str}", icon_url=player.avatar_url or None)
    
    desc = ""
    
    # --- VISUALIZE ACTIVE POWERUPS ---
    # This makes them persistent inside the blue box!
    if player.active_powerups:
        pup_names = [f"**{p.name}**" for p in player.active_powerups]
        desc += f"⚡ **Active Effects:** {' | '.join(pup_names)}\n\n"
    # ---------------------------------

    is_frozen = any(p.effect == EffectType.TIME_FREEZE for p in player.active_powerups)

    if is_frozen: desc += "❄️ **TIMER FROZEN** ❄️\n(Max speed bonus secured)\n"
    else:
        if player.current_q_timestamp == 0:
            end = int(time.time() + question.time_limit)
        else:
            end = int(player.current_q_timestamp + question.time_limit)
        desc += f"⏱️ **Time Remaining:** <t:{end}:R>\n"
    
    if question.type == QuestionType.REORDER and current_sequence:
        seq_items = [question.options[i][:15] for i in current_sequence]
        if glitch_active: seq_items = [glitch_text(s) for s in seq_items]
        seq_str = " -> ".join(seq_items)
        desc += f"\n**Current Sequence:**\n`{seq_str}`"

    embed.description = desc
    if question.image_url: embed.set_image(url=question.image_url)

    if player.inventory:
        unique_items = {item.name: item for item in player.inventory}
        desc_text = ""
        for item in unique_items.values():
            name = item.name
            desc_i = item.description
            if glitch_active:
                name = glitch_text(name)
                desc_i = glitch_text(desc_i)
            desc_text += f"-# **{item.icon} {name}:** {desc_i}\n"
        embed.add_field(name="🎒 Your Power-ups", value=desc_text, inline=False)
    return embed

async def push_update_to_player(session: GameSession, player: Player, glitch=False):
    if not player.board_message: return
    try:
        real_idx = player.question_order[player.current_q_index]
        q = session.quiz.questions[real_idx]
        sorted_players = sorted(session.players.values(), key=lambda p: p.score, reverse=True)
        try: rank = sorted_players.index(player) + 1
        except: rank = 0
        embed = build_game_embed(player, q, player.current_q_index + 1, f"#{rank}", glitch_active=glitch)
        await player.board_message.edit(embed=embed)
    except: pass

async def open_board_logic(interaction: discord.Interaction, session: GameSession, player: Player):
    if player.completed:
        await interaction.response.send_message(f"🎉 **You have finished!**\nFinal Score: {player.score}", ephemeral=True)
        return
    real_idx = player.question_order[player.current_q_index]
    q1 = session.quiz.questions[real_idx]
    sorted_players = sorted(session.players.values(), key=lambda p: p.score, reverse=True)
    try: rank = sorted_players.index(player) + 1
    except: rank = 0
    rank_str = f"#{rank}"
    embed = build_game_embed(player, q1, player.current_q_index + 1, rank_str)
    view = GameView(session, player)
    if interaction.response.is_done():
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        msg = await interaction.original_response()
    player.board_message = msg

async def finish_game_logic(session: GameSession, interaction: discord.Interaction):
    if not session.is_running:
        msg = "Game is not running."
        if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
        else: await interaction.response.send_message(msg, ephemeral=True)
        return
    session.is_running = False
    session.end_time = time.time()
    session.bump_mode = None 
    if interaction.response.is_done(): await interaction.followup.send("🛑 **Stopping Game...**", ephemeral=True)
    else: await interaction.response.send_message("🛑 **Stopping Game...**", ephemeral=True)
    sorted_players = sorted(session.players.values(), key=lambda p: p.score, reverse=True)
    total_players = len(sorted_players)
    total_attempts = sum(len(p.answers_log) for p in sorted_players)
    total_correct = sum(p.correct_answers for p in sorted_players)
    total_possible = total_players * len(session.quiz.questions)
    comp_rate = total_attempts / total_possible if total_possible > 0 else 0
    avg_acc = total_correct / total_attempts if total_attempts > 0 else 0
    p_log = getattr(session, 'powerup_usage_log', [])
    sess_id = save_full_report(session, {"completion_rate": comp_rate, "avg_accuracy": avg_acc}, p_log)
    for attr in ['lobby_msg', 'dashboard_msg', 'connector_msg']:
        msg = getattr(session, attr, None)
        if msg:
            try: await msg.delete()
            except: pass
            setattr(session, attr, None) 
    s_data, p_data, q_data = get_session_details(sess_id)
    view = ReportNavigator(s_data, p_data, q_data)
    await interaction.followup.send(embed=view.get_embed(), view=view, ephemeral=True)
    try:
        if sorted_players:
            desc = "**🏆 Final Podium:**\n"
            medals = ["🥇", "🥈", "🥉"]
            for i, p in enumerate(sorted_players[:3]): desc += f"{medals[i]} **{p.name}** — {p.score} pts\n"
            desc += "\nCheck `Admin Report` for details."
        else: desc = "No players participated."
        over_embed = discord.Embed(title="🏁 Game Over!", description=desc, color=0xFF0000)
        await interaction.channel.send(embed=over_embed)
    except: pass

    if session.channel_id in active_sessions:
        del active_sessions[session.channel_id]

async def do_bump(session: GameSession, channel):
    for attr in ['lobby_msg', 'dashboard_msg', 'connector_msg']:
        msg = getattr(session, attr, None)
        if msg:
            try: await msg.delete()
            except: pass
            setattr(session, attr, None) 
    if not session.is_running:
        embed = discord.Embed(title=f"Quiz Lobby: {session.quiz.name}", description=f"**Players:** {len(session.players)}\n" + "\n".join([p.name for p in session.players.values()]))
        embed.set_footer(text="Type /join to enter!")
        session.lobby_msg = await channel.send(embed=embed)
    else:
        dashboard_view = LiveDashboardView(session)
        embed = discord.Embed(title="📊 Live Leaderboard", description="Refreshing...", color=0xFFD700)
        session.dashboard_msg = await channel.send(embed=embed, view=dashboard_view)
        session.connector_msg = await channel.send("🚀 **Game is Live!**", view=StartConnector(session))

# --- VIEWS ---

class ResultsConfirmation(discord.ui.View):
    def __init__(self, final_content, target_channel, background_ids, trophy_ids, session_id):
        super().__init__(timeout=300)
        self.final_content = final_content
        self.target_channel = target_channel
        self.background_ids = background_ids
        self.trophy_ids = trophy_ids
        self.session_id = session_id
    @discord.ui.button(label="Confirm & Send", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.target_channel.send(self.final_content)
        mark_results_sent(self.session_id)
        await interaction.response.edit_message(content=f"✅ Sent to {self.target_channel.mention}!", view=None)
        bg_str = " ".join([str(uid) for uid in self.background_ids])
        tr_str = " ".join([str(uid) for uid in self.trophy_ids])
        msg = f"**IDs for Role Assignment:**\n\n**Background Winners (>=25% Acc):**\n```\n{bg_str}\n```\n**Trophy Winners (Top 3 Score):**\n```\n{tr_str}\n```"
        await interaction.followup.send(msg, ephemeral=True)
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)

class ResultsResendView(discord.ui.View):
    def __init__(self, final_content, target_channel):
        super().__init__(timeout=300)
        self.final_content = final_content
        self.target_channel = target_channel
    @discord.ui.button(label="Resend Results to Channel", style=discord.ButtonStyle.blurple)
    async def resend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.target_channel.send(self.final_content)
        await interaction.response.edit_message(content=f"✅ Resent to {self.target_channel.mention}!", view=None)

class HistorySessionSelect(discord.ui.Select):
    def __init__(self, sessions):
        options = []
        for s in sessions:
            options.append(discord.SelectOption(label=f"{s['quiz_name']} (P:{s['total_players']})", description=f"ID: {s['session_id']}", value=str(s['session_id'])))
        super().__init__(placeholder="Select a session to view report...", options=options)
    async def callback(self, interaction: discord.Interaction):
        sess_id = int(self.values[0])
        s_data, p_data, q_data = get_session_details(sess_id)
        if not s_data: return
        nav_view = ReportNavigator(s_data, p_data, q_data)
        await interaction.response.send_message(embed=nav_view.get_embed(), view=nav_view, ephemeral=True)

class HistoryPaginationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.page = 0
        self.limit = 25
        self.update_components()
    def update_components(self):
        self.clear_items()
        offset = self.page * self.limit
        sessions = get_history_page(self.limit, offset)
        if sessions: self.add_item(HistorySessionSelect(sessions))
        prev_btn = discord.ui.Button(label="◀️ Newer", style=discord.ButtonStyle.primary, disabled=(self.page == 0), row=1)
        prev_btn.callback = self.prev_page
        self.add_item(prev_btn)
        next_sessions = get_history_page(1, (self.page + 1) * self.limit)
        next_btn = discord.ui.Button(label="Older ▶️", style=discord.ButtonStyle.primary, disabled=(len(next_sessions) == 0), row=1)
        next_btn.callback = self.next_page
        self.add_item(next_btn)
        total = get_total_session_count()
        max_p = (total + self.limit - 1) // self.limit
        lbl = discord.ui.Button(label=f"Page {self.page + 1}/{max_p}", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        self.add_item(lbl)
    async def prev_page(self, interaction):
        self.page -= 1
        self.update_components()
        await interaction.response.edit_message(view=self)
    async def next_page(self, interaction):
        self.page += 1
        self.update_components()
        await interaction.response.edit_message(view=self)

class LeaderboardView(discord.ui.View):
    def __init__(self, data, duration_label, author_id):
        super().__init__(timeout=300)
        self.data = data
        self.duration_label = duration_label
        self.author_id = author_id # Store the command caller's ID
        self.mode = "score" 
        self.update_embed()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the person who ran the command to click buttons
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("⛔ These buttons are not for you.", ephemeral=True)
            return False
        return True

    def update_embed(self):
        title = f"🏆 Leaderboard ({self.duration_label})"
        
        # Sort Data based on mode
        if self.mode == "score": 
            sorted_data = sorted(self.data, key=lambda x: x['avg_score'], reverse=True)
            sort_desc = "Average Score"
        elif self.mode == "accuracy": 
            sorted_data = sorted(self.data, key=lambda x: x['accuracy'], reverse=True)
            sort_desc = "Accuracy"
        elif self.mode == "total":
            # Calculate Total Score (Avg * Games)
            sorted_data = sorted(self.data, key=lambda x: x['avg_score'] * x['games'], reverse=True)
            sort_desc = "Total Score (All Games)"

        desc = f"Sorted by: **{sort_desc}**\n\n"
        
        for i, entry in enumerate(sorted_data[:15]): 
            if self.mode == "accuracy":
                val = f"{entry['accuracy']:.1f}%"
            elif self.mode == "total":
                # Calculate total on the fly for display
                total_pts = int(entry['avg_score'] * entry['games'])
                val = f"{total_pts} pts (Total)"
            else:
                val = f"{entry['avg_score']} pts (Avg)"
            
            desc += f"{i+1}. **{entry['name']}** — {val} ({entry['games']} games)\n"
            
        self.embed = discord.Embed(title=title, description=desc, color=0xFFD700)

    @discord.ui.button(label="Sort by Avg.", style=discord.ButtonStyle.blurple)
    async def sort_score(self, interaction, button):
        self.mode = "score"
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed, view=self)

    @discord.ui.button(label="Sort by Acc.", style=discord.ButtonStyle.blurple)
    async def sort_acc(self, interaction, button):
        self.mode = "accuracy"
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed, view=self)

    @discord.ui.button(label="Show Total Score", style=discord.ButtonStyle.blurple)
    async def sort_total(self, interaction, button):
        self.mode = "total"
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed, view=self)

class UserSearchModal(discord.ui.Modal, title="Search for a User"):
    username = discord.ui.TextInput(label="Username", placeholder="Enter name to find...")
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view
    async def on_submit(self, interaction):
        query = self.username.value.lower()
        found = -1
        for i, p in enumerate(self.parent_view.players_data):
            if query in p.name.lower():
                found = i
                break
        if found != -1:
            self.parent_view.view_mode = "players"
            self.parent_view.current_page = found + 1 
            self.parent_view.update_buttons()
            await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
        else: await interaction.response.send_message("Not found.", ephemeral=True)

class ReportNavigator(discord.ui.View):
    def __init__(self, session_data, players_data, question_data=None):
        super().__init__(timeout=600)
        self.session_data = session_data
        self.players_data = players_data
        self.question_data = question_data or {}
        self.view_mode = "players"
        self.current_page = 0 
        self.max_player_pages = len(players_data)
        self.max_question_pages = len(self.question_data)
        self.update_buttons()
    def update_buttons(self):
        max_p = self.max_player_pages if self.view_mode == "players" else self.max_question_pages
        self.prev_btn.disabled = (self.current_page == 0)
        self.next_btn.disabled = (self.current_page == max_p)
        mode = "Users" if self.view_mode == "players" else "Questions"
        self.switch_btn.label = f"Switch to {('Questions' if self.view_mode == 'players' else 'Users')}"
        if self.current_page == 0: self.page_label.label = "Global Summary"
        else: self.page_label.label = f"{mode} ({self.current_page}/{max_p})"
    def get_embed(self):
        if self.current_page == 0: return self.get_summary_embed()
        if self.view_mode == "players": return self.get_player_embed(self.current_page - 1)
        else: return self.get_question_embed(self.current_page - 1)
    def get_summary_embed(self):
        embed = discord.Embed(title=f"📜 History: {self.session_data['quiz_name']}", color=0xFFD700)
        embed.add_field(name="Date", value=f"<t:{int(self.session_data['date_played'])}:F>", inline=False)
        embed.add_field(name="Total Players", value=str(self.session_data['total_players']), inline=True)
        acc = self.session_data.get('avg_accuracy', 0.0)
        embed.add_field(name="Avg Accuracy", value=f"{acc*100:.1f}%", inline=True)
        return embed
    def get_player_embed(self, index):
        p = self.players_data[index]
        embed = discord.Embed(title=f"👤 {p.name}", color=0x00BFFF)
        time_str = f"{int(p.total_time // 60)}m {int(p.total_time % 60)}s"
        embed.add_field(name="Stats", value=f"Score: {p.score}\nCorrect: {p.correct_answers}\nTime: {time_str}", inline=False)
        log = ""
        for l in p.answers_log:
            status = "✅" if l['is_correct'] else "❌"
            log += f"Q{l['q_index']+1} {status} ({l['time']:.1f}s)\n"
        embed.add_field(name="Log", value=log or "None", inline=False)
        return embed
    def get_question_embed(self, index):
        keys = sorted(self.question_data.keys())
        if index >= len(keys): return discord.Embed(title="Error")
        q_idx = keys[index]
        d = self.question_data[q_idx]
        embed = discord.Embed(title=f"❓ Q{q_idx+1}", description=d['text'], color=0x9B59B6)
        acc = (d['correct_count']/d['count'])*100 if d['count']>0 else 0
        embed.add_field(name="Stats", value=f"Correct: {d['correct_count']}/{d['count']} ({acc:.1f}%)", inline=False)
        return embed
    @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary, row=0)
    async def prev_btn(self, interaction, button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    @discord.ui.button(label="Page", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_label(self, interaction, button): pass
    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary, row=0)
    async def next_btn(self, interaction, button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    @discord.ui.button(label="Switch View", style=discord.ButtonStyle.success, row=1)
    async def switch_btn(self, interaction, button):
        self.view_mode = "questions" if self.view_mode == "players" else "players"
        if self.current_page > 0: self.current_page = 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.secondary, row=1)
    async def search_btn(self, interaction, button):
        await interaction.response.send_modal(UserSearchModal(self))

class IntermissionView(discord.ui.View):
    def __init__(self, session, player, is_correct, correct_answer_str, points, powerup, is_last_question=False, gift_msg=None):
        super().__init__(timeout=None)
        self.session = session
        self.player = player
        self.is_correct = is_correct
        self.correct_str = correct_answer_str
        self.points = points
        self.pup = powerup
        self.gift_msg = gift_msg
        self.is_last_question = is_last_question
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label.startswith("Next Question"):
                if is_last_question:
                    child.label = "Finish Quiz 🏁"
                    child.style = discord.ButtonStyle.green
                break
    @discord.ui.button(label="Next Question ▶️", style=discord.ButtonStyle.blurple)
    async def next_q(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player.user_id:
            await interaction.response.send_message("Not your board.", ephemeral=True)
            return
        if self.player.current_q_index >= len(self.player.question_order):
             self.player.completed = True
             self.player.completion_timestamp = time.time()
             await interaction.response.edit_message(content=f"🎉 **Completed!** Final Score: {self.player.score}", view=None, embed=None)
             return
        real_idx = self.player.question_order[self.player.current_q_index]
        next_q = self.session.quiz.questions[real_idx]
        sorted_players = sorted(self.session.players.values(), key=lambda p: p.score, reverse=True)
        try: rank = sorted_players.index(self.player) + 1
        except: rank = 0
        self.player.current_q_timestamp = time.time()
        embed = build_game_embed(self.player, next_q, self.player.current_q_index + 1, f"#{rank}")
        view = GameView(self.session, self.player)
        msg = await interaction.response.edit_message(content=None, embed=embed, view=view)

class StartConnector(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
    @discord.ui.button(label="Open Game Board", style=discord.ButtonStyle.green)
    async def open(self, interaction, button):
        player = register_new_player(self.session, interaction.user)
        await open_board_logic(interaction, self.session, player)

class LiveDashboardView(discord.ui.View):
    def __init__(self, session: GameSession):
        super().__init__(timeout=None)
        self.session = session
    @discord.ui.button(label="Check My Rank", style=discord.ButtonStyle.primary, row=0)
    async def check_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.session.players:
            await interaction.response.send_message("You are not in this game.", ephemeral=True)
            return
        sorted_players = sorted(self.session.players.values(), key=lambda p: p.score, reverse=True)
        player = self.session.players[interaction.user.id]
        rank = sorted_players.index(player) + 1
        total = len(sorted_players)
        await interaction.response.send_message(f"🏅 **Your Rank:** #{rank} / {total}\n**Score:** {player.score} pts\n**Streak:** {player.streak} 🔥", ephemeral=True)
    @discord.ui.button(label="End Game (Admin)", style=discord.ButtonStyle.danger, row=1)
    async def end_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        await finish_game_logic(self.session, interaction)

class AdminDashboard(discord.ui.View):
    def __init__(self, session: GameSession):
        super().__init__(timeout=None)
        self.session = session

class AdminLaunchView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.green)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.session.players:
            await interaction.response.send_message("No players joined yet.", ephemeral=True)
            return
        self.session.is_running = True
        self.session.start_time = time.time()
        dash_embed = discord.Embed(title="📊 Live Leaderboard", description="Starting...", color=0xFFD700)
        self.session.dashboard_msg = await interaction.channel.send(embed=dash_embed, view=LiveDashboardView(self.session))
        self.session.connector_msg = await interaction.channel.send("🚀 **Game is Live!**", view=StartConnector(self.session))
        await interaction.response.edit_message(content="✅ **Game Started!**", view=None)

class GameView(discord.ui.View):
    def __init__(self, session: GameSession, player: Player, status_log: str = "", restored: bool = False):
        super().__init__(timeout=None)
        self.session = session
        self.player = player
        self.status_log = status_log
        self.restored = restored # <--- Flag to track if this is a "zombie" board
        
        # --- RESTORE STATE LOGIC ---
        saved_state = self.player.view_state
        self.current_selections = set(saved_state.get('selections', []))
        self.reorder_sequence = saved_state.get('reorder', [])
        # Convert string keys back to int
        saved_map = saved_state.get('map', {})
        self.displayed_to_original_map = {int(k): v for k, v in saved_map.items()}

        if not hasattr(self.session, 'powerup_usage_log'): self.session.powerup_usage_log = []
        
        if self.player.current_q_index < len(self.player.question_order):
            self.real_q_index = self.player.question_order[self.player.current_q_index]
            self.current_q = session.quiz.questions[self.real_q_index]
        else:
            self.current_q = None 
            self.real_q_index = -1
        
        if player.current_q_timestamp == 0.0:
            player.current_q_timestamp = time.time()
        self.question_start_time = player.current_q_timestamp
        
        if self.current_q:
             self.setup_answer_buttons()
             self.setup_powerup_buttons()

    def get_rank_str(self):
        sorted_players = sorted(self.session.players.values(), key=lambda p: p.score, reverse=True)
        try: rank = sorted_players.index(self.player) + 1
        except: rank = 0
        return f"#{rank}"

    def save_view_state(self):
        self.player.view_state = {
            'map': self.displayed_to_original_map,
            'selections': list(self.current_selections),
            'reorder': self.reorder_sequence
        }

    async def handle_restored(self, interaction: discord.Interaction):
        """Standard response for any interaction on a restored/stale board"""
        msg = "⚠️ **Board Expired:** The bot was reloaded. Please generate a fresh board from the main menu!"
        # Invalidate the UI so they stop clicking it
        await interaction.response.edit_message(content=msg, view=None, embed=None)

    def setup_answer_buttons(self):
        if not self.current_q: return
        
        if not self.displayed_to_original_map:
            original_options = list(enumerate(self.current_q.options))
            random.seed(f"{self.session.start_time}_{self.player.user_id}_{self.real_q_index}")
            shuffled_options = random.sample(original_options, len(original_options))
            random.seed()
            self.displayed_to_original_map = {i: opt[0] for i, opt in enumerate(shuffled_options)}
        
        self.save_view_state()

        shuffled_options = []
        for i in range(len(self.displayed_to_original_map)):
            orig_idx = self.displayed_to_original_map[i]
            if orig_idx < len(self.current_q.options):
                shuffled_options.append((orig_idx, self.current_q.options[orig_idx]))

        labels = ["A", "B", "C", "D", "E"]
        wrong_indices = [i for i, _ in enumerate(self.current_q.options) if i not in self.current_q.correct_indices]
        
        disabled_original_indices = []
        for p in self.player.active_powerups:
            if p.effect == EffectType.FIFTY_FIFTY:
                if len(wrong_indices) >= 2: disabled_original_indices = random.sample(wrong_indices, 2)
            elif p.effect == EffectType.ERASER:
                if wrong_indices: disabled_original_indices = [random.choice(wrong_indices)]
        
        for i, (orig_idx, text) in enumerate(shuffled_options):
            if i >= 5: break 
            custom_id = f"ans_{i}_{self.player.user_id}"
            is_disabled = orig_idx in disabled_original_indices
            style = discord.ButtonStyle.primary
            
            if self.current_q.type == QuestionType.REORDER:
                if orig_idx in self.reorder_sequence:
                    style = discord.ButtonStyle.success
                    is_disabled = True
            elif self.current_q.allow_multi_select:
                if i in self.current_selections: style = discord.ButtonStyle.success
            
            btn = discord.ui.Button(label=f"{labels[i]}: {text[:75]}", style=style, custom_id=custom_id, row=0 if i < 3 else 1, disabled=is_disabled)
            btn.callback = self.answer_callback
            self.add_item(btn)
            
        if self.current_q.allow_multi_select or self.current_q.type == QuestionType.REORDER:
            submit_btn = discord.ui.Button(label="Submit", style=discord.ButtonStyle.success, custom_id=f"submit_{self.player.user_id}", row=2, emoji="✅")
            submit_btn.callback = self.submit_callback
            self.add_item(submit_btn)
            if self.current_q.type == QuestionType.REORDER:
                reset_btn = discord.ui.Button(label="Reset Order", style=discord.ButtonStyle.danger, custom_id="reset", row=2, emoji="🔄")
                reset_btn.callback = self.reset_callback
                self.add_item(reset_btn)

    def setup_powerup_buttons(self):
        if not self.player.inventory: return
        are_buttons_disabled = len(self.player.active_powerups) > 0
        for i, pup in enumerate(self.player.inventory):
            custom_id = f"pup_{i}_{self.player.user_id}"
            is_specific_disabled = False
            if pup.effect == EffectType.FIFTY_FIFTY:
                opt_count = len(self.current_q.options)
                if opt_count == 2 or opt_count % 2 != 0: is_specific_disabled = True
            if pup.effect == EffectType.ERASER:
                if len(self.current_q.options) <= 2: is_specific_disabled = True
            final_disabled = are_buttons_disabled or is_specific_disabled
            btn = discord.ui.Button(label=f"{pup.icon} {pup.name}", style=discord.ButtonStyle.secondary, custom_id=custom_id, row=3, disabled=final_disabled)
            btn.callback = self.powerup_callback
            self.add_item(btn)

    async def check_ownership(self, interaction):
        if interaction.user.id != self.player.user_id:
            await interaction.response.send_message("⛔ Not your board!", ephemeral=True)
            return False
        return True

    async def powerup_callback(self, interaction):
        if self.restored: return await self.handle_restored(interaction)
        if not await self.check_ownership(interaction): return
        if len(self.player.active_powerups) > 0:
            await interaction.response.send_message("❌ One powerup per turn!", ephemeral=True)
            return
        parts = interaction.data['custom_id'].split("_")
        index = int(parts[1])
        if index >= len(self.player.inventory): return
        selected_powerup = self.player.inventory.pop(index)
        self.player.active_powerups.append(selected_powerup)
        self.session.powerup_usage_log.append({'user_id': self.player.user_id, 'name': selected_powerup.name})
        
        if selected_powerup.effect == EffectType.POWER_PLAY:
            self.session.global_powerplay_end = time.time() + 20
            self.session.global_powerplay_active = True
            for p in self.session.players.values():
                asyncio.create_task(push_update_to_player(self.session, p))
        elif selected_powerup.effect == EffectType.GLITCH:
            for p in self.session.players.values():
                if p.user_id != self.player.user_id:
                    asyncio.create_task(push_update_to_player(self.session, p, glitch=True))
            async def revert():
                await asyncio.sleep(10)
                for p in self.session.players.values():
                    if p.user_id != self.player.user_id:
                        asyncio.create_task(push_update_to_player(self.session, p, glitch=False))
            asyncio.create_task(revert())
            
        # --- FIX: SAVE STATUS TO LOG ---
        # This ensures the text stays if the user clicks other buttons
        self.status_log = f"⚡ **Activated: {selected_powerup.name}!**"
        # -------------------------------

        self.clear_items()
        self.setup_answer_buttons()
        self.setup_powerup_buttons()
        new_embed = build_game_embed(self.player, self.current_q, self.player.current_q_index + 1, self.get_rank_str())
        await interaction.response.edit_message(content=self.status_log, embed=new_embed, view=self)

    async def reset_callback(self, interaction):
        if self.restored: return await self.handle_restored(interaction) # <--- CHECK
        if not await self.check_ownership(interaction): return
        self.reorder_sequence.clear()
        self.save_view_state() 
        self.clear_items()
        self.setup_answer_buttons()
        self.setup_powerup_buttons()
        embed = build_game_embed(self.player, self.current_q, self.player.current_q_index + 1, self.get_rank_str())
        await interaction.response.edit_message(content=self.status_log, embed=embed, view=self)

    async def answer_callback(self, interaction):
        if self.restored: return await self.handle_restored(interaction) # <--- CHECK
        if not await self.check_ownership(interaction): return
        if self.player.current_q_timestamp == 0: return
        parts = interaction.data['custom_id'].split("_")
        clicked_display_idx = int(parts[1])
        
        if self.current_q.type == QuestionType.REORDER:
            if clicked_display_idx in self.displayed_to_original_map:
                orig_idx = self.displayed_to_original_map[clicked_display_idx]
                self.reorder_sequence.append(orig_idx)
                self.save_view_state()
                
                self.clear_items()
                self.setup_answer_buttons()
                self.setup_powerup_buttons()
                rank_str = self.get_rank_str()
                seq_text = "**Current Sequence:**\n"
                for idx, o_idx in enumerate(self.reorder_sequence):
                    seq_text += f"{idx+1}. {self.current_q.options[o_idx]}\n"
                full_content = f"{self.status_log}\n\n{seq_text}"
                embed = build_game_embed(self.player, self.current_q, self.player.current_q_index + 1, rank_str)
                await interaction.response.edit_message(content=full_content, embed=embed, view=self)
            
        elif self.current_q.allow_multi_select:
            if clicked_display_idx in self.current_selections: self.current_selections.remove(clicked_display_idx)
            else: self.current_selections.add(clicked_display_idx)
            self.save_view_state()
            
            self.clear_items()
            self.setup_answer_buttons()
            self.setup_powerup_buttons()
            embed = build_game_embed(self.player, self.current_q, self.player.current_q_index + 1, self.get_rank_str())
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.process_submission(interaction, [clicked_display_idx])

    async def submit_callback(self, interaction):
        if self.restored: return await self.handle_restored(interaction) # <--- CHECK
        if not await self.check_ownership(interaction): return
        if self.player.current_q_timestamp == 0: return
        if self.current_q.type == QuestionType.REORDER:
            await self.process_submission(interaction, [], reorder_final=self.reorder_sequence)
        else:
            if not self.current_selections:
                await interaction.response.send_message("Select at least one!", ephemeral=True)
                return
            await self.process_submission(interaction, list(self.current_selections))

    async def process_submission(self, interaction, selected_display_indices, reorder_final=None):
        if reorder_final is not None:
            orig_indices = reorder_final
            is_correct = (orig_indices == self.current_q.correct_indices)
        else:
            orig_indices = [self.displayed_to_original_map[i] for i in selected_display_indices]
            is_correct = set(orig_indices) == set(self.current_q.correct_indices)
            
        chosen_text = ", ".join([self.current_q.options[i] for i in orig_indices])
        time_taken = time.time() - self.question_start_time
        for p in self.player.active_powerups:
            if p.effect == EffectType.TIME_FREEZE:
                time_taken = 0.5
                break
        is_timeout = time_taken > self.current_q.time_limit
        
        self.player.answers_log.append({
            "q_index": self.real_q_index, "q_text": self.current_q.text, "chosen": orig_indices, "chosen_text": chosen_text, "is_correct": is_correct, "time": time_taken, "points": 0
        })
        
        if not is_correct:
            for p in self.player.active_powerups:
                if p.effect == EffectType.IMMUNITY:
                    self.player.active_powerups.remove(p)
                    self.current_selections.clear()
                    self.reorder_sequence.clear()
                    self.save_view_state()
                    embed = build_game_embed(self.player, self.current_q, self.player.current_q_index + 1, self.get_rank_str())
                    self.clear_items()
                    self.setup_answer_buttons()
                    self.setup_powerup_buttons()
                    await interaction.response.edit_message(content=f"🛡️ **Immunity used!**", embed=embed, view=self)
                    return
        
        points = 0
        new_pup = None
        gift_feedback = None
        
        if is_correct and not is_timeout:
            base_points = self.calculate_score(time_taken, self.current_q.time_limit)
            points = int(base_points * self.current_q.weight)
            if any(p.effect == EffectType.DOUBLE_JEOPARDY for p in self.player.active_powerups):
                points *= 2
            self.player.score += points
            self.player.streak += 1
            self.player.correct_answers += 1
            self.player.answers_log[-1]['points'] = points
            
            if len(self.player.inventory) < 3 and random.random() < 0.4:
                pool = [p for p in load_powerups() if p.name not in [x.name for x in self.player.inventory]]
                if pool:
                    new_pup = random.choice(pool)
                    self.player.inventory.append(new_pup)
            
            for p in self.player.active_powerups:
                if p.effect == EffectType.GIFT:
                    others = [x for x in self.session.players.values() if x.user_id != self.player.user_id]
                    if others:
                        rec = random.choice(others)
                        rec.score += int(p.value)
                        rec.notifications.append(f"🎁 **{self.player.name} gifted you {int(p.value)} pts!**")
                        gift_feedback = f"Gifted {int(p.value)}pts to {rec.name}!"
                    else: gift_feedback = "Gift failed (No players)"
        else:
            self.player.incorrect_answers += 1
            self.session.question_stats[self.real_q_index] += 1
            if any(p.effect == EffectType.DOUBLE_JEOPARDY for p in self.player.active_powerups):
                self.player.score = 0
            if not any(p.effect == EffectType.STREAK_SAVER for p in self.player.active_powerups):
                self.player.streak = 0
        
        # --- MODIFIED CLEANUP LOGIC ---
        self.player.view_state = {} 
        
        if is_correct:
            # If Correct: Keep protection items (Immunity/Streak Saver) because they weren't needed.
            # Consume everything else (Multipliers, Gifts, One-time modifiers like 50-50).
            self.player.active_powerups = [
                p for p in self.player.active_powerups 
                if p.effect in [EffectType.STREAK_SAVER, EffectType.IMMUNITY]
            ]
        else:
            # If Incorrect: We used our chances. Clear everything.
            # (Immunity was already removed in the block above if it triggered)
            self.player.active_powerups.clear()

        self.player.current_q_index += 1
        self.player.current_q_timestamp = 0 
        
        await self.show_intermission(interaction, is_correct, points, new_pup, is_timeout, gift_feedback)

    def calculate_score(self, time_taken, limit):
        if time_taken > limit: return 0
        raw = 600 + int(400 * max(0, 1 - (time_taken/limit)))
        mult = 1.0
        for p in self.player.active_powerups:
            if p.effect == EffectType.MULTIPLIER: mult += (p.value - 1.0)
            elif p.effect == EffectType.FLAT_BONUS: raw += p.value
        if self.session.global_powerplay_active: mult += 0.5
        return int((raw * mult) + (self.player.streak * 100))

    async def show_intermission(self, interaction, correct, points, powerup, timeout, gift_msg=None):
        is_last = self.player.current_q_index >= len(self.player.question_order)
        color = 0xFF0000 if (timeout or not correct) else 0x00FF00
        title = "⏰ Time's Up!" if timeout else ("✅ Correct!" if correct else "❌ Incorrect!")
        desc = f"**Points:** +{points}\n**Streak:** {self.player.streak} 🔥\n"
        if powerup: desc += f"**Loot:** {powerup.name}!\n"
        if gift_msg: desc += f"**Gift:** {gift_msg}\n"
        if (not correct or timeout) and self.current_q.explanation: desc += f"\n**Explanation:**\n{self.current_q.explanation}"
        embed = discord.Embed(title=title, description=desc, color=color)
        if self.current_q.type == QuestionType.REORDER:
            ans_str = " -> ".join([self.current_q.options[i] for i in self.current_q.correct_indices])
            embed.add_field(name="Correct Sequence", value=ans_str)
        else:
            ans_str = ", ".join([self.current_q.options[i] for i in self.current_q.correct_indices])
            embed.add_field(name="Correct Answer", value=ans_str)
        view = IntermissionView(self.session, self.player, correct, ans_str, points, powerup, is_last_question=is_last, gift_msg=gift_msg)
        if interaction:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        elif self.player.board_message:
            await self.player.board_message.edit(content=None, embed=embed, view=view)

class Gameplay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state_loaded = False
        self.startup_time = time.time()
        self.bot.loop.create_task(self.load_state())
        self.dashboard_update.start()
        self.bump_task.start()
        self.check_timeouts.start()
    def cog_unload(self):
        if self.state_loaded: 
            self.save_state()
        self.dashboard_update.cancel()
        self.bump_task.cancel()
        self.check_timeouts.cancel()

    def save_state(self):
        if not self.state_loaded: return
        data = {}
        for cid, session in active_sessions.items():
            data[str(cid)] = session.to_dict()
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Failed to save state: {e}")

    async def load_state(self):
        await self.bot.wait_until_ready()
        if not os.path.exists(STATE_FILE): 
            self.state_loaded = True
            return
        
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            
            for cid_str, s_data in data.items():
                try:
                    # 1. Reconstruct Quiz
                    quiz_name = s_data['quiz_name']
                    quiz = load_quiz(quiz_name)
                    if not quiz: continue
                    
                    # 2. Reconstruct Session
                    channel_id = int(s_data['channel_id'])
                    session = GameSession(channel_id, quiz)
                    session.is_running = s_data['is_running']
                    session.start_time = s_data['start_time']
                    session.end_time = s_data.get('end_time', 0)
                    # Skip sessions that have already ended
                    if session.end_time > 0: 
                        continue
                    session.global_powerplay_active = s_data.get('global_powerplay_active', False)
                    session.global_powerplay_end = s_data.get('global_powerplay_end', 0)
                    session.question_stats = {int(k): v for k, v in s_data['question_stats'].items()}
                    session.powerup_usage_log = s_data.get('powerup_usage_log', [])
                    session.bump_mode = s_data.get('bump_mode')
                    session.bump_interval = s_data.get('bump_interval', 0)
                    session.bump_threshold = s_data.get('bump_threshold', 0)
                    session.last_bump_time = s_data.get('last_bump_time', 0)
                    session.message_counter = s_data.get('message_counter', 0)

                    # 3. Reconstruct Players
                    for uid_str, p_data in s_data['players'].items():
                        session.players[int(uid_str)] = Player.from_dict(p_data)

                    # 4. Recover & Refresh Messages
                    msg_ids = s_data.get('msg_ids', {})
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        if msg_ids.get('lobby'):
                            try: session.lobby_msg = await channel.fetch_message(msg_ids['lobby'])
                            except: pass
                        if msg_ids.get('dashboard'):
                            try: session.dashboard_msg = await channel.fetch_message(msg_ids['dashboard'])
                            except: pass
                        if msg_ids.get('connector'):
                            try: session.connector_msg = await channel.fetch_message(msg_ids['connector'])
                            except: pass

                        # FORCE REFRESH: This deletes old msgs and sends a fresh Leaderboard
                        await do_bump(session, channel)

                    # 5. REGISTER VIEWS (Restored Mode)
                    for player in session.players.values():
                        if not player.completed:
                            # restored=True means any click triggers "Board Expired"
                            view = GameView(session, player, restored=True)
                            self.bot.add_view(view)

                    active_sessions[channel_id] = session
                    print(f"Restored session for channel {channel_id}")
                except Exception as e:
                    print(f"Error restoring session {cid_str}: {e}")
                    
        except Exception as e:
            print(f"Failed to load state: {e}")
        
        self.state_loaded = True

    async def session_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        sessions = get_session_lookup(limit=25)
        choices = []
        for s in sessions:
            if current.lower() in s['label'].lower():
                choices.append(app_commands.Choice(name=s['label'], value=s['id']))
        return choices[:25]

    async def quiz_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        quiz_map = get_quiz_lookup()
        choices = []
        for real_name, filename in quiz_map.items():
            if current.lower() in real_name.lower():
                choices.append(app_commands.Choice(name=real_name, value=filename))
        return choices[:25]
    
    async def duration_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        total = get_total_session_count()
        choices = []
        limit = min(total, 10)
        for i in range(1, limit + 1):
            label = "Last Quiz" if i == 1 else f"Last {i} Quizzes"
            choices.append(app_commands.Choice(name=label, value=f"Last {i} Quizzes"))
        choices.append(app_commands.Choice(name="All-Time", value="All-Time"))
        return choices
        
    async def powerup_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        all_pups = load_powerups()
        choices = []
        for p in all_pups:
            if current.lower() in p.name.lower():
                choices.append(app_commands.Choice(name=p.name, value=p.name))
        return choices[:25]

    @app_commands.command(name="results", description="Generate formatted results for a session")
    @app_commands.autocomplete(session_id=session_autocomplete)
    async def results_cmd(self, interaction: discord.Interaction, session_id: int, channel: discord.TextChannel, custom_message: str, emoji: str = "💀"):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        s_data, players, q_data = get_session_details(session_id)
        if not s_data:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return
        avg_acc_str = f"{s_data.get('avg_accuracy', 0)*100:.1f}%"
        total_q = s_data['total_questions']
        bg_winners = []
        bg_ids = []
        for p in players:
            acc = p.correct_answers / total_q if total_q > 0 else 0
            if acc >= 0.25:
                bg_winners.append(f"<@{p.user_id}>")
                bg_ids.append(p.user_id)
        bg_str = ", ".join(bg_winners) if bg_winners else "None"
        sorted_by_score = sorted(players, key=lambda x: x.score, reverse=True)[:3]
        top_str = ""
        trophy_ids = []
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(sorted_by_score):
            acc = (p.correct_answers / total_q)*100 if total_q > 0 else 0
            top_str += f"## {medals[i]} <@{p.user_id}> **{acc:.1f}%, {p.score}**\n"
            trophy_ids.append(p.user_id)
        hardest_q = None
        hardest_idx = -1
        min_rate = 1.1
        for idx, data in q_data.items():
            if data['count'] > 0:
                rate = data['correct_count'] / data['count']
                if rate < min_rate:
                    min_rate = rate
                    hardest_q = data
                    hardest_idx = int(idx)
        hardest_str = "N/A"
        if hardest_q:
            quiz_obj = load_quiz(s_data['quiz_name'])
            correct_ans_txt = "Unknown"
            if quiz_obj and hardest_idx < len(quiz_obj.questions):
                q_obj = quiz_obj.questions[hardest_idx]
                if q_obj.type == QuestionType.REORDER:
                    correct_ans_txt = " -> ".join([q_obj.options[i] for i in q_obj.correct_indices])
                else:
                    correct_ans_txt = ", ".join([q_obj.options[i] for i in q_obj.correct_indices])
            avg_time_q = hardest_q['total_time'] / hardest_q['count']
            hardest_str = (
                f"__Hardest Question__ {emoji}\n"
                f"**{hardest_q['text']}**\n"
                f"Correct Answer: **{correct_ans_txt}**\n"
                f"Average Time Taken: *{avg_time_q:.2f}s* / Correct Answers: *{hardest_q['correct_count']}*"
            )
        final_msg = (
            f"# {s_data['quiz_name']} Results!\n\n"
            f"### Average accuracy this time was *{avg_acc_str}.*\n"
            f"Here's our background winners for today!\n"
            f"25%: {bg_str}\n\n"
            f"Here's our top 3!\n"
            f"{top_str}\n"
            f"{hardest_str}\n\n"
            f"{custom_message}"
        )
        bg_ids_str = " ".join([str(uid) for uid in bg_ids])
        tr_ids_str = " ".join([str(uid) for uid in trophy_ids])
        id_msg = f"**IDs for Role Assignment:**\n\n**Background Winners (>=25% Acc):**\n```\n{bg_ids_str}\n```\n**Trophy Winners (Top 3 Score):**\n```\n{tr_ids_str}\n```"
        if check_results_sent(session_id):
            await interaction.response.send_message(id_msg, view=ResultsResendView(final_msg, channel), ephemeral=True)
        else:
            view = ResultsConfirmation(final_msg, channel, bg_ids, trophy_ids, session_id)
            await interaction.response.send_message(f"**Preview for {channel.mention}:**\n\n{final_msg}", view=view, ephemeral=True)

    @app_commands.command(name="start_quiz", description="Load a quiz (Admin)")
    @app_commands.autocomplete(quiz_name=quiz_autocomplete)
    async def start_quiz(self, interaction: discord.Interaction, quiz_name: str):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        quiz = load_quiz(quiz_name)
        if not quiz:
            await interaction.response.send_message("Quiz not found.", ephemeral=True)
            return
        session = GameSession(interaction.channel_id, quiz)
        active_sessions[interaction.channel_id] = session
        embed = discord.Embed(title=f"Quiz Open: {quiz.name}", description="Type **/join** to participate!", color=0x00FF00)
        await interaction.response.send_message(embed=embed)
        session.lobby_msg = await interaction.original_response()
        await interaction.followup.send(
            content="**Admin Controls:** Click to start when players are ready.", 
            view=AdminLaunchView(session), 
            ephemeral=True
        )

    @app_commands.command(name="launch", description="Force start the game (Bypasses Lobby Button)")
    async def launch_game(self, interaction: discord.Interaction):
        # 1. Permission Check
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
            
        # 2. Session Check
        session = active_sessions.get(interaction.channel_id)
        if not session:
            await interaction.response.send_message("No active session to launch.", ephemeral=True)
            return
            
        if session.is_running:
            await interaction.response.send_message("Game is already running!", ephemeral=True)
            return

        if not session.players:
            await interaction.response.send_message("No players joined yet.", ephemeral=True)
            return

        # 3. Clean up the old Lobby Message so chat stays clean
        if session.lobby_msg:
            try: await session.lobby_msg.delete()
            except: pass
            session.lobby_msg = None

        # 4. Initialize Game State
        session.is_running = True
        session.start_time = time.time()

        # 5. Post Dashboard & Connector (Same as button logic)
        dash_embed = discord.Embed(title="📊 Live Leaderboard", description="Starting...", color=0xFFD700)
        session.dashboard_msg = await interaction.channel.send(embed=dash_embed, view=LiveDashboardView(session))
        session.connector_msg = await interaction.channel.send("🚀 **Game is Live!**", view=StartConnector(session))

        # 6. Confirm Command Execution
        await interaction.response.send_message("✅ **Game Launched!**", ephemeral=True)
    
    @app_commands.command(name="stop_quiz", description="Failsafe stop current quiz")
    async def stop_quiz(self, interaction: discord.Interaction):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        session = active_sessions.get(interaction.channel_id)
        if not session: 
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        await finish_game_logic(session, interaction)

    @app_commands.command(name="join", description="Join the active quiz")
    async def join_command(self, interaction: discord.Interaction):
        session = active_sessions.get(interaction.channel_id)
        if not session:
            await interaction.response.send_message("No active quiz.", ephemeral=True)
            return
        player = register_new_player(session, interaction.user)
        if session.is_running: await open_board_logic(interaction, session, player)
        else: await interaction.response.send_message("✅ Joined! Wait for start.", ephemeral=True)
        if session.lobby_msg and not session.is_running:
            try:
                embed = session.lobby_msg.embeds[0]
                embed.description = f"**Players:** {len(session.players)}\n" + "\n".join([p.name for p in session.players.values()])
                await session.lobby_msg.edit(embed=embed)
            except: pass

    @app_commands.command(name="debug", description="Admin Debug Tools")
    @app_commands.choices(action=[
        app_commands.Choice(name="Give Powerup", value="give_pup"),
        app_commands.Choice(name="Random Answer (Self)", value="rand_ans"),
        app_commands.Choice(name="Ping / Uptime", value="ping")
    ])
    @app_commands.autocomplete(powerup_name=powerup_autocomplete)
    async def debug(self, interaction: discord.Interaction, action: str, powerup_name: str = None):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return

        # --- 1. HANDLE PING/UPTIME ---
        if action == "ping":
            # Helper to format seconds into d h m s
            def format_uptime(seconds):
                minutes, seconds = divmod(int(seconds), 60)
                hours, minutes = divmod(minutes, 60)
                days, hours = divmod(hours, 24)
                return f"{days}d {hours}h {minutes}m {seconds}s"

            now = time.time()
            
            # Bot Uptime
            bot_uptime_sec = now - self.bot.boot_time
            bot_str = format_uptime(bot_uptime_sec)
            
            # Cogs Uptime
            cogs_desc = ""
            # Sort extensions alphabetically
            for ext_name, load_time in sorted(self.bot.extension_times.items()):
                diff = now - load_time
                # Highlight recently reloaded cogs (less than 1 min ago)
                icon = "🔄" if diff < 60 else "🟢" 
                cogs_desc += f"{icon} **{ext_name}:** `{format_uptime(diff)}`\n"

            latency = round(self.bot.latency * 1000)
            
            embed = discord.Embed(title="🏓 System Status", color=0x00FF00)
            embed.add_field(name="Latency", value=f"`{latency}ms`", inline=True)
            embed.add_field(name="Bot Uptime", value=f"`{bot_str}`", inline=True)
            if cogs_desc:
                embed.add_field(name="Extensions / Cogs Uptime", value=cogs_desc, inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # --- 2. VALIDATE SESSION (Required for other actions) ---
        session = active_sessions.get(interaction.channel_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        
        player = session.players.get(interaction.user.id)
        if not player:
            await interaction.response.send_message("You are not in the game.", ephemeral=True)
            return

        # --- 3. GAME ACTIONS ---
        if action == "give_pup":
            all_pups = load_powerups()
            target = next((p for p in all_pups if p.name == powerup_name), None)
            if target:
                player.inventory.append(target)
                await interaction.response.send_message(f"✅ Added {target.name}", ephemeral=True)
            else:
                await interaction.response.send_message("Powerup not found.", ephemeral=True)
        
        elif action == "rand_ans":
            if not session.is_running or player.completed:
                await interaction.response.send_message("Game not running or you finished.", ephemeral=True)
                return
            
            view = GameView(session, player) 
            view.setup_answer_buttons() 
            if view.current_q and view.current_q.options:
                options_count = len(view.current_q.options)
                rand_display_idx = random.randint(0, options_count - 1)
                await view.process_submission(interaction, [rand_display_idx])
            else:
                await interaction.response.send_message("Error finding question options.", ephemeral=True)

    @app_commands.command(name="leaderboard", description="View aggregate leaderboards")
    @app_commands.autocomplete(duration=duration_autocomplete)
    async def leaderboard(self, interaction: discord.Interaction, duration: str):
        session_ids = get_session_ids_by_limit(duration)
        if not session_ids:
            await interaction.response.send_message("No data.", ephemeral=False)
            return
        data = get_leaderboard_data(session_ids)
        if not data:
            await interaction.response.send_message("No data.", ephemeral=False)
            return
        
        # Pass the interaction.user.id to restrict button usage
        view = LeaderboardView(data, duration, interaction.user.id)
        await interaction.response.send_message(embed=view.embed, view=view, ephemeral=False)

    @app_commands.command(name="roundup", description="View aggregate statistics")
    @app_commands.autocomplete(duration=duration_autocomplete)
    async def roundup(self, interaction: discord.Interaction, duration: str):
        session_ids = get_session_ids_by_limit(duration)
        if not session_ids:
            await interaction.response.send_message("No data.", ephemeral=True)
            return
        data = get_roundup_data(session_ids)
        embed = discord.Embed(title=f"📊 Roundup ({duration})", color=0x9B59B6)
        embed.add_field(name="Unique Players", value=str(data['unique_users']), inline=True)
        embed.add_field(name="Total Answers", value=str(data['total_questions_answered']), inline=True)
        embed.add_field(name="Top Powerup", value=data['top_powerup'], inline=False)
        embed.add_field(name="Easiest Question", value=data['easiest_q'], inline=False)
        embed.add_field(name="Hardest Question", value=data['hardest_q'], inline=False)
        embed.add_field(name="Most Played Quiz", value=data['most_played_quiz'], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="bump", description="Manage bumping for the quiz embed")
    @app_commands.describe(mode="Manual, Timer (mins), Messages (count), or Off")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Manual Bump (Now)", value="manual"),
        app_commands.Choice(name="Auto Timer", value="timer"),
        app_commands.Choice(name="Auto Message Count", value="count"),
        app_commands.Choice(name="Off / Clear", value="off")
    ])
    async def bump_cmd(self, interaction: discord.Interaction, mode: str, value: int = 0):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        session = active_sessions.get(interaction.channel_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        if mode == "manual":
            await do_bump(session, interaction.channel)
            await interaction.response.send_message("✅ Bumped!", ephemeral=True)
        elif mode == "off":
            session.bump_mode = None
            await interaction.response.send_message("✅ Auto-bump disabled.", ephemeral=True)
        elif mode == "timer":
            if value < 30: 
                await interaction.response.send_message("Minimum timer is 30 seconds.", ephemeral=True)
                return
            session.bump_mode = "timer"
            session.bump_interval = value
            session.last_bump_time = time.time()
            await interaction.response.send_message(f"✅ Auto-bump set to every {value} seconds.", ephemeral=True)
        elif mode == "count":
            if value < 1:
                await interaction.response.send_message("Minimum count is 1.", ephemeral=True)
                return
            session.bump_mode = "count"
            session.bump_threshold = value
            session.message_counter = 0
            await interaction.response.send_message(f"✅ Auto-bump set to every {value} messages.", ephemeral=True)

    @tasks.loop(seconds=1)
    async def check_timeouts(self):
        now = time.time()
        for session in active_sessions.values():
            if not session.is_running: continue
            for player in session.players.values():
                if player.completed or player.current_q_timestamp == 0: continue
                is_frozen = any(p.effect == EffectType.TIME_FREEZE for p in player.active_powerups)
                if is_frozen: continue
                q_idx = player.question_order[player.current_q_index]
                q = session.quiz.questions[q_idx]
                if now > (player.current_q_timestamp + q.time_limit + 1):
                    player.incorrect_answers += 1
                    session.question_stats[q_idx] += 1
                    if any(p.effect == EffectType.DOUBLE_JEOPARDY for p in player.active_powerups): player.score = 0
                    if not any(p.effect == EffectType.STREAK_SAVER for p in player.active_powerups): player.streak = 0
                    
                    player.answers_log.append({
                        "q_index": q_idx, "q_text": q.text, "chosen": [], "chosen_text": "TIMEOUT", "is_correct": False, "time": q.time_limit, "points": 0
                    })
                    
                    # --- MODIFIED TIMEOUT CLEANUP ---
                    # Timeout consumes everything (especially Streak Saver), EXCEPT Immunity.
                    # Logic: You didn't "use" Immunity because you didn't pick a wrong answer.
                    player.active_powerups = [
                        p for p in player.active_powerups 
                        if p.effect == EffectType.IMMUNITY
                    ]
                    
                    player.current_q_index += 1
                    player.current_q_timestamp = 0 
                    if player.board_message:
                        try:
                            is_last = player.current_q_index >= len(player.question_order)
                            color = 0xFF0000
                            embed = discord.Embed(title="⏰ Time's Up!", description=f"**Points:** +0\n**Streak:** {player.streak} 🔥\n", color=color)
                            if q.explanation: embed.description += f"\n**Explanation:**\n{q.explanation}"
                            if q.type == QuestionType.REORDER:
                                ans_str = " -> ".join([q.options[i] for i in q.correct_indices])
                                embed.add_field(name="Correct Sequence", value=ans_str)
                            else:
                                ans_str = ", ".join([q.options[i] for i in q.correct_indices])
                                embed.add_field(name="Correct Answer", value=ans_str)
                            view = IntermissionView(session, player, False, ans_str, 0, None, is_last_question=is_last)
                            await player.board_message.edit(content=None, embed=embed, view=view)
                        except: pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        session = active_sessions.get(message.channel.id)
        if session and session.bump_mode == "count":
            session.message_counter += 1
            if session.message_counter >= session.bump_threshold:
                await do_bump(session, message.channel)
                session.message_counter = 0

    @app_commands.command(name="history", description="View past quiz reports (Admin)")
    async def view_history(self, interaction: discord.Interaction):
        if not is_privileged(interaction):
            await interaction.response.send_message("⛔ Admin Only.", ephemeral=True)
            return
        view = HistoryPaginationView()
        await interaction.response.send_message(content="Select a session:", view=view, ephemeral=True)

    @tasks.loop(seconds=1)
    async def check_timeouts(self):
        now = time.time()
        for session in active_sessions.values():
            if not session.is_running: continue
            for player in session.players.values():
                if player.completed or player.current_q_timestamp == 0: continue
                is_frozen = any(p.effect == EffectType.TIME_FREEZE for p in player.active_powerups)
                if is_frozen: continue
                q_idx = player.question_order[player.current_q_index]
                q = session.quiz.questions[q_idx]
                if now > (player.current_q_timestamp + q.time_limit + 1):
                    player.incorrect_answers += 1
                    session.question_stats[q_idx] += 1
                    if any(p.effect == EffectType.DOUBLE_JEOPARDY for p in player.active_powerups): player.score = 0
                    if not any(p.effect == EffectType.STREAK_SAVER for p in player.active_powerups): player.streak = 0
                    player.answers_log.append({
                        "q_index": q_idx, "q_text": q.text, "chosen": [], "chosen_text": "TIMEOUT", "is_correct": False, "time": q.time_limit, "points": 0
                    })
                    player.active_powerups.clear()
                    player.current_q_index += 1
                    player.current_q_timestamp = 0 
                    if player.board_message:
                        try:
                            is_last = player.current_q_index >= len(player.question_order)
                            color = 0xFF0000
                            embed = discord.Embed(title="⏰ Time's Up!", description=f"**Points:** +0\n**Streak:** {player.streak} 🔥\n", color=color)
                            if q.explanation: embed.description += f"\n**Explanation:**\n{q.explanation}"
                            if q.type == QuestionType.REORDER:
                                ans_str = " -> ".join([q.options[i] for i in q.correct_indices])
                                embed.add_field(name="Correct Sequence", value=ans_str)
                            else:
                                ans_str = ", ".join([q.options[i] for i in q.correct_indices])
                                embed.add_field(name="Correct Answer", value=ans_str)
                            view = IntermissionView(session, player, False, ans_str, 0, None, is_last_question=is_last)
                            await player.board_message.edit(content=None, embed=embed, view=view)
                        except: pass

    @tasks.loop(seconds=5)
    async def dashboard_update(self):
        if not self.state_loaded: return
        self.save_state() # CHANGED: Auto-save every loop
        for session in active_sessions.values():
            if session.is_running and hasattr(session, 'dashboard_msg') and session.dashboard_msg:
                sorted_players = sorted(session.players.values(), key=lambda p: p.score, reverse=True)
                desc = ""
                for i, p in enumerate(sorted_players[:10]): 
                    progress = f"{p.current_q_index}/{len(p.question_order)}"
                    status = "✅ Done" if p.completed else f"Q{progress}"
                    desc += f"**{i+1}. {p.name}** - {p.score} pts (Streak: {p.streak} 🔥) [{status}]\n"
                embed = discord.Embed(title="📊 Live Leaderboard", description=desc, color=0xFFD700)
                try: await session.dashboard_msg.edit(embed=embed)
                except: pass
                
    @tasks.loop(seconds=10)
    async def bump_task(self):
        for session in active_sessions.values():
            if session.bump_mode == "timer":
                if time.time() - session.last_bump_time > session.bump_interval:
                    channel = self.bot.get_channel(session.channel_id)
                    if channel:
                        await do_bump(session, channel)
                        session.last_bump_time = time.time()
                        
async def setup(bot):
    await bot.add_cog(Gameplay(bot))