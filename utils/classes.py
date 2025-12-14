from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import random

class EffectType:
    MULTIPLIER = "multiplier"       
    FLAT_BONUS = "flat_bonus"       
    STREAK_ADD = "streak_add"       
    GIFT = "gift"                   
    FIFTY_FIFTY = "50-50"           
    ERASER = "eraser"               
    IMMUNITY = "immunity"           
    TIME_FREEZE = "time_freeze"     
    STREAK_SAVER = "streak_saver"   
    POWER_PLAY = "power_play"       
    DOUBLE_JEOPARDY = "double_jeopardy" 
    GLITCH = "glitch"                   

class QuestionType:
    STANDARD = "standard"
    REORDER = "reorder"

@dataclass
class CustomPowerUp:
    name: str
    description: str
    effect: str   
    value: float  
    icon: str = "⚡"
    def to_dict(self): return self.__dict__
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass
class Question:
    text: str
    options: List[str]
    correct_indices: List[int]
    type: str = QuestionType.STANDARD 
    time_limit: int = 30 
    weight: float = 1.0
    explanation: Optional[str] = None
    image_url: Optional[str] = None
    allow_multi_select: bool = False
    def to_dict(self): return self.__dict__

@dataclass
class Player:
    user_id: int
    name: str
    avatar_url: str
    score: int = 0
    streak: int = 0
    
    current_q_index: int = 0
    question_order: List[int] = field(default_factory=list)
    
    inventory: List[CustomPowerUp] = field(default_factory=list)
    active_powerups: List[CustomPowerUp] = field(default_factory=list)
    completed: bool = False
    notifications: List[str] = field(default_factory=list)
    
    correct_answers: int = 0
    incorrect_answers: int = 0
    time_per_question: Dict[int, float] = field(default_factory=dict)
    current_q_timestamp: float = 0.0
    
    join_time: float = 0.0
    completion_timestamp: float = 0.0
    answers_log: List[dict] = field(default_factory=list)
    
    # Store the message object to allow push updates (glitch/power play)
    board_message: Any = None 
    
    # NEW: Stores the button layout so we can restore it after a reload
    view_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        data = self.__dict__.copy()
        data.pop('board_message', None) 
        data['inventory'] = [p.to_dict() for p in self.inventory]
        data['active_powerups'] = [p.to_dict() for p in self.active_powerups]
        return data

    @classmethod
    def from_dict(cls, data):
        inv_data = data.pop('inventory', [])
        act_data = data.pop('active_powerups', [])
        tpq_data = data.pop('time_per_question', {})
        # Ensure view_state exists if loading from old file
        if 'view_state' not in data: data['view_state'] = {}
        
        player = cls(**data)
        player.inventory = [CustomPowerUp.from_dict(x) for x in inv_data]
        player.active_powerups = [CustomPowerUp.from_dict(x) for x in act_data]
        player.time_per_question = {int(k): v for k, v in tpq_data.items()}
        return player

@dataclass
class Quiz:
    name: str
    creator_id: int
    questions: List[Question] = field(default_factory=list)
    def to_dict(self):
        return {
            "name": self.name,
            "creator_id": self.creator_id,
            "questions": [q.to_dict() for q in self.questions],
        }

class GameSession:
    def __init__(self, channel_id, quiz: Quiz):
        self.channel_id = channel_id
        self.quiz = quiz
        self.players: Dict[int, Player] = {} 
        self.is_running = False
        self.start_time = 0
        self.end_time = 0
        self.global_powerplay_active = False 
        self.global_powerplay_end = 0
        
        self.lobby_msg = None 
        self.dashboard_msg = None
        self.connector_msg = None 
        
        self.question_stats: Dict[int, int] = {i: 0 for i in range(len(quiz.questions))}
        self.powerup_usage_log: List[dict] = []
        
        self.bump_mode = None 
        self.bump_interval = 0 
        self.bump_threshold = 0 
        self.last_bump_time = 0
        self.message_counter = 0

    def to_dict(self):
        return {
            "channel_id": self.channel_id,
            "quiz_name": self.quiz.name,
            "players": {str(k): v.to_dict() for k, v in self.players.items()},
            "is_running": self.is_running,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "global_powerplay_active": self.global_powerplay_active,
            "global_powerplay_end": self.global_powerplay_end,
            "question_stats": {str(k): v for k, v in self.question_stats.items()},
            "powerup_usage_log": self.powerup_usage_log,
            "bump_mode": self.bump_mode,
            "bump_interval": self.bump_interval,
            "bump_threshold": self.bump_threshold,
            "last_bump_time": self.last_bump_time,
            "message_counter": self.message_counter,
            "msg_ids": {
                "lobby": self.lobby_msg.id if self.lobby_msg else None,
                "dashboard": self.dashboard_msg.id if self.dashboard_msg else None,
                "connector": self.connector_msg.id if self.connector_msg else None
            }
        }