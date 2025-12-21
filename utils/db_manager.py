import sqlite3
import json
import os
import time
from .classes import Quiz, Question, CustomPowerUp, EffectType

DB_FILE = "data/quiz_history.db"

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    if not os.path.exists("data"):
        os.makedirs("data")
        
    conn = get_connection()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_name TEXT,
        date_played TIMESTAMP,
        total_questions INTEGER,
        total_players INTEGER,
        completion_rate REAL,
        avg_accuracy REAL,
        results_sent INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        user_id INTEGER,
        name TEXT,
        score INTEGER,
        rank INTEGER,
        correct_count INTEGER,
        incorrect_count INTEGER,
        unattempted_count INTEGER,
        join_time REAL,
        finish_time REAL,
        total_time_taken REAL,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_db_id INTEGER,
        question_index INTEGER,
        question_text TEXT,
        chosen_indices TEXT, 
        chosen_text TEXT, 
        is_correct INTEGER, 
        time_taken REAL,
        points_earned INTEGER,
        FOREIGN KEY(player_db_id) REFERENCES players(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS powerup_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        user_id INTEGER,
        powerup_name TEXT,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )''')
    
    # [NEW] Moderation Tables
    c.execute('''CREATE TABLE IF NOT EXISTS moderation_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        admin_id INTEGER,
        action_type TEXT,
        reason TEXT,
        quiz_name TEXT,
        timestamp REAL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS banned_users (
        user_id INTEGER PRIMARY KEY,
        admin_id INTEGER,
        reason TEXT,
        timestamp REAL
    )''')
    
    # Migrations for existing DBs
    try: c.execute("ALTER TABLE answers ADD COLUMN chosen_text TEXT")
    except: pass
    try: c.execute("ALTER TABLE sessions ADD COLUMN avg_accuracy REAL")
    except: pass
    try: c.execute("ALTER TABLE sessions ADD COLUMN results_sent INTEGER DEFAULT 0")
    except: pass
    
    conn.commit()
    conn.close()

setup_database()

def save_full_report(session_obj, global_stats, powerup_logs):
    conn = get_connection()
    c = conn.cursor()
    
    c.execute('''INSERT INTO sessions (quiz_name, date_played, total_questions, total_players, completion_rate, avg_accuracy, results_sent)
                 VALUES (?, ?, ?, ?, ?, ?, 0)''', 
              (session_obj.quiz.name, time.time(), len(session_obj.quiz.questions), len(session_obj.players), global_stats['completion_rate'], global_stats.get('avg_accuracy', 0.0)))
    
    session_db_id = c.lastrowid
    
    for p_log in powerup_logs:
        c.execute("INSERT INTO powerup_usage (session_id, user_id, powerup_name) VALUES (?, ?, ?)", 
                  (session_db_id, p_log['user_id'], p_log['name']))

    sorted_players = sorted(session_obj.players.values(), key=lambda p: p.score, reverse=True)
    
    for rank, player in enumerate(sorted_players, 1):
        total_qs = len(session_obj.quiz.questions)
        attempted = len(player.answers_log)
        unattempted = total_qs - attempted
        
        if player.completion_timestamp > 0 and player.join_time > 0:
            total_duration = player.completion_timestamp - player.join_time
        elif attempted > 0 and player.join_time > 0:
            total_duration = time.time() - player.join_time 
        else:
            total_duration = 0
            
        c.execute('''INSERT INTO players (session_id, user_id, name, score, rank, correct_count, incorrect_count, unattempted_count, join_time, finish_time, total_time_taken)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (session_db_id, player.user_id, player.name, player.score, rank, player.correct_answers, player.incorrect_answers, unattempted, player.join_time, player.completion_timestamp, total_duration))
        
        player_db_id = c.lastrowid
        
        for log in player.answers_log:
            chosen_txt = log.get('chosen_text', "")
            c.execute('''INSERT INTO answers (player_db_id, question_index, question_text, chosen_indices, chosen_text, is_correct, time_taken, points_earned)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                      (player_db_id, log['q_index'], log['q_text'], json.dumps(log['chosen']), chosen_txt, 1 if log['is_correct'] else 0, log['time'], log['points']))
            
    conn.commit()
    conn.close()
    return session_db_id

def check_results_sent(session_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT results_sent FROM sessions WHERE session_id = ?", (session_id,))
    res = c.fetchone()
    conn.close()
    return bool(res['results_sent']) if res else False

def mark_results_sent(session_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE sessions SET results_sent = 1 WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

def get_total_session_count():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sessions")
    res = c.fetchone()
    count = res[0] if res else 0
    conn.close()
    return count

def get_session_ids_by_limit(limit_str):
    conn = get_connection()
    c = conn.cursor()
    if limit_str == "All-Time":
        c.execute("SELECT session_id FROM sessions")
    else:
        try:
            parts = limit_str.split()
            if parts[1] == "Quiz": limit = 1
            else: limit = int(parts[1])
            c.execute("SELECT session_id FROM sessions ORDER BY session_id DESC LIMIT ?", (limit,))
        except:
            return []
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_leaderboard_data(session_ids):
    if not session_ids: return []
    conn = get_connection()
    c = conn.cursor()
    placeholder = ",".join("?" for _ in session_ids)
    query = f'''
        SELECT name, SUM(score) as total_score, SUM(correct_count) as total_correct,
        SUM(correct_count + incorrect_count) as total_attempts, COUNT(session_id) as games_played
        FROM players WHERE session_id IN ({placeholder}) GROUP BY user_id
    '''
    c.execute(query, session_ids)
    rows = c.fetchall()
    conn.close()
    results = []
    for r in rows:
        avg_score = r['total_score'] / r['games_played'] if r['games_played'] > 0 else 0
        accuracy = (r['total_correct'] / r['total_attempts']) * 100 if r['total_attempts'] > 0 else 0.0
        results.append({"name": r['name'], "avg_score": int(avg_score), "accuracy": accuracy, "games": r['games_played']})
    return results

def get_roundup_data(session_ids):
    if not session_ids: return None
    conn = get_connection()
    c = conn.cursor()
    placeholder = ",".join("?" for _ in session_ids)
    data = {}
    c.execute(f"SELECT COUNT(DISTINCT user_id) FROM players WHERE session_id IN ({placeholder})", session_ids)
    data['unique_users'] = c.fetchone()[0]
    c.execute(f"SELECT SUM(correct_count + incorrect_count) FROM players WHERE session_id IN ({placeholder})", session_ids)
    res = c.fetchone()[0]
    data['total_questions_answered'] = res if res else 0
    c.execute(f"SELECT powerup_name, COUNT(*) as cnt FROM powerup_usage WHERE session_id IN ({placeholder}) GROUP BY powerup_name ORDER BY cnt DESC LIMIT 1", session_ids)
    row = c.fetchone()
    data['top_powerup'] = f"{row[0]} ({row[1]} uses)" if row else "None"
    c.execute(f'''SELECT question_text, AVG(is_correct) as acc FROM answers 
        WHERE player_db_id IN (SELECT id FROM players WHERE session_id IN ({placeholder})) GROUP BY question_text ORDER BY acc DESC''', session_ids)
    q_rows = c.fetchall()
    if q_rows:
        easiest = q_rows[0]
        hardest = q_rows[-1]
        data['easiest_q'] = f"{easiest[0][:40]}... ({easiest[1]*100:.1f}%)"
        data['hardest_q'] = f"{hardest[0][:40]}... ({hardest[1]*100:.1f}%)"
    else:
        data['easiest_q'] = "N/A"
        data['hardest_q'] = "N/A"
    c.execute(f'''SELECT quiz_name, SUM(total_players) as total_p FROM sessions
        WHERE session_id IN ({placeholder}) GROUP BY quiz_name ORDER BY total_p DESC LIMIT 1''', session_ids)
    quiz_row = c.fetchone()
    data['most_played_quiz'] = f"{quiz_row[0]} ({quiz_row[1]} plays)" if quiz_row else "N/A"
    conn.close()
    return data

def get_recent_sessions(limit=10):
    return get_history_page(limit, 0)

def get_history_page(limit, offset):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT session_id, quiz_name, date_played, total_players, completion_rate, avg_accuracy FROM sessions ORDER BY session_id DESC LIMIT ? OFFSET ?", (limit, offset))
    rows = c.fetchall()
    conn.close()
    return rows

def get_session_lookup(limit=25):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT session_id, quiz_name, date_played, total_players FROM sessions ORDER BY session_id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    results = []
    for r in rows:
        date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(r['date_played']))
        label = f"{r['quiz_name']} | {date_str} | P:{r['total_players']}"
        results.append({'id': r['session_id'], 'label': label})
    return results

def get_session_details(session_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = c.fetchone()
    if not row: 
        conn.close()
        return None, None, None
    session = dict(row)
    c.execute("SELECT * FROM players WHERE session_id = ? ORDER BY score DESC", (session_id,))
    players_data = c.fetchall()
    class MockPlayer:
        def __init__(self, row):
            self.user_id = row['user_id']
            self.name = row['name']
            self.score = row['score']
            self.correct_answers = row['correct_count']
            self.incorrect_answers = row['incorrect_count']
            self.total_time = row['total_time_taken']
            self.avatar_url = "" 
            self.answers_log = []
    players = []
    for p_row in players_data:
        p_obj = MockPlayer(p_row)
        c.execute("SELECT * FROM answers WHERE player_db_id = ?", (p_row['id'],))
        ans_rows = c.fetchall()
        for a in ans_rows:
            p_obj.answers_log.append({
                "q_index": a['question_index'],
                "is_correct": bool(a['is_correct']),
                "time": a['time_taken'],
                "chosen_text": a['chosen_text']
            })
        players.append(p_obj)
    q_analytics = get_question_analytics(session_id)
    conn.close()
    return session, players, q_analytics

def get_question_analytics(session_id):
    conn = get_connection()
    c = conn.cursor()
    query = '''SELECT a.question_index, a.question_text, a.is_correct, a.time_taken, a.chosen_text, p.name 
        FROM answers a JOIN players p ON a.player_db_id = p.id WHERE p.session_id = ? ORDER BY a.question_index'''
    c.execute(query, (session_id,))
    rows = c.fetchall()
    conn.close()
    analytics = {}
    for r in rows:
        idx = r['question_index']
        if idx not in analytics:
            analytics[idx] = {"text": r['question_text'], "total_time": 0, "count": 0, "correct_count": 0, "responses": []}
        data = analytics[idx]
        data["total_time"] += r['time_taken']
        data["count"] += 1
        if r['is_correct']: data["correct_count"] += 1
        data["responses"].append({"player": r['name'], "answer": r['chosen_text'], "correct": bool(r['is_correct']), "time": r['time_taken']})
    return analytics

def delete_session(session_id):
    conn = get_connection()
    c = conn.cursor()
    # Delete dependent rows first (Foreign Keys)
    c.execute("DELETE FROM answers WHERE player_db_id IN (SELECT id FROM players WHERE session_id = ?)", (session_id,))
    c.execute("DELETE FROM players WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM powerup_usage WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

def delete_sessions_range(start_id, end_id):
    conn = get_connection()
    c = conn.cursor()
    
    # Handle reverse inputs
    low = min(start_id, end_id)
    high = max(start_id, end_id)
    
    # Delete dependent rows in batch
    c.execute("DELETE FROM answers WHERE player_db_id IN (SELECT id FROM players WHERE session_id BETWEEN ? AND ?)", (low, high))
    c.execute("DELETE FROM players WHERE session_id BETWEEN ? AND ?", (low, high))
    c.execute("DELETE FROM powerup_usage WHERE session_id BETWEEN ? AND ?", (low, high))
    
    # Delete sessions
    c.execute("DELETE FROM sessions WHERE session_id BETWEEN ? AND ?", (low, high))
    deleted_count = c.rowcount
    
    conn.commit()
    conn.close()
    return deleted_count

# --- MODERATION FUNCTIONS ---

def log_moderation_action(user_id, user_name, admin_id, action_type, reason, quiz_name):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO moderation_logs (user_id, user_name, admin_id, action_type, reason, quiz_name, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, user_name, admin_id, action_type, reason, quiz_name, time.time()))
    conn.commit()
    conn.close()

def ban_user_db(user_id, admin_id, reason):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO banned_users (user_id, admin_id, reason, timestamp) VALUES (?, ?, ?, ?)",
              (user_id, admin_id, reason, time.time()))
    conn.commit()
    conn.close()

def unban_user_db(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def check_is_banned(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return bool(res)

def get_moderation_history(limit=25):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM moderation_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows