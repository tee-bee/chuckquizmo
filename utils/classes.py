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
    
    # New: Store the message object to allow push updates (glitch/power play)
    board_message: Any = None 

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