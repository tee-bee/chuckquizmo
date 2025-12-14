import json
import os
from .classes import Quiz, Question, CustomPowerUp, EffectType

DATA_DIR = "data"
QUIZ_DIR = os.path.join(DATA_DIR, "quizzes")
POWERUP_FILE = os.path.join(DATA_DIR, "powerups.json")

def ensure_dirs():
    if not os.path.exists(QUIZ_DIR):
        os.makedirs(QUIZ_DIR)

ensure_dirs()

DEFAULT_POWERUPS = [
    CustomPowerUp("Streak Saver", "Protects streak on wrong answer", EffectType.STREAK_SAVER, 0, "🛡️"),
    CustomPowerUp("50/50", "Removes half of incorrect answers", EffectType.FIFTY_FIFTY, 0, "✂️"),
    CustomPowerUp("2x Multiplier", "Double points for next question", EffectType.MULTIPLIER, 2.0, "✖️"),
    CustomPowerUp("Supersonic", "High multiplier if answered fast", EffectType.MULTIPLIER, 2.5, "🚀"),
    CustomPowerUp("Time Freeze", "Freezes timer for max points", EffectType.TIME_FREEZE, 0, "❄️"),
    CustomPowerUp("Eraser", "Removes one wrong option", EffectType.ERASER, 0, "✏️"),
    CustomPowerUp("Immunity", "Second chance if wrong", EffectType.IMMUNITY, 0, "💉"),
    CustomPowerUp("Gift", "Give 800pts to another player", EffectType.GIFT, 800, "🎁"),
    CustomPowerUp("Double Jeopardy", "2x Points if correct, LOSE ALL if wrong", EffectType.DOUBLE_JEOPARDY, 0, "⚖️"),
    CustomPowerUp("Power Play", "+50% Score for EVERYONE (20s)", EffectType.POWER_PLAY, 0, "📢"),
    CustomPowerUp("Glitch", "Glitch everyone's screen (10s)", EffectType.GLITCH, 0, "👾")
]

def save_quiz(quiz: Quiz):
    filename = f"{quiz.name.replace(' ', '_').lower()}.json"
    path = os.path.join(QUIZ_DIR, filename)
    with open(path, 'w') as f:
        json.dump(quiz.to_dict(), f, indent=4)

def load_quiz(name: str) -> Quiz:
    quiz_map = get_quiz_lookup()
    if name in quiz_map: 
        filename = quiz_map[name]
    else:
        filename = name if name.endswith(".json") else f"{name}.json"
        
    path = os.path.join(QUIZ_DIR, filename)
    if not os.path.exists(path): return None
    
    with open(path, 'r') as f:
        data = json.load(f)
        
    q_objs = []
    for q_data in data['questions']:
        q = Question(
            text=q_data['text'],
            options=q_data['options'],
            correct_indices=q_data['correct_indices'],
            type=q_data.get('type', 'standard'),
            time_limit=q_data.get('time_limit', 30),
            weight=q_data.get('weight', 1.0),
            explanation=q_data.get('explanation'),
            image_url=q_data.get('image_url'),
            allow_multi_select=q_data.get('allow_multi_select', False)
        )
        q_objs.append(q)
        
    return Quiz(data['name'], data['creator_id'], q_objs)

def get_quiz_lookup():
    if not os.path.exists(QUIZ_DIR): return {}
    files = [f for f in os.listdir(QUIZ_DIR) if f.endswith(".json")]
    lookup = {}
    for f in files:
        try:
            with open(os.path.join(QUIZ_DIR, f), 'r') as file:
                data = json.load(file)
                lookup[data['name']] = f
        except: continue
    return lookup

def load_powerups():
    if not os.path.exists(POWERUP_FILE):
        return DEFAULT_POWERUPS
    try:
        with open(POWERUP_FILE, 'r') as f:
            data = json.load(f)
            return [CustomPowerUp(**d) for d in data]
    except:
        return DEFAULT_POWERUPS

# --- NEW FUNCTION ---
def save_all_powerups(powerups_list):
    data = [p.to_dict() for p in powerups_list]
    with open(POWERUP_FILE, 'w') as f:
        json.dump(data, f, indent=4)
        
def delete_quiz_file(name: str) -> bool:
    quiz_map = get_quiz_lookup()
    if name in quiz_map:
        filename = quiz_map[name]
    else:
        filename = name if name.endswith(".json") else f"{name}.json"
    
    path = os.path.join(QUIZ_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False