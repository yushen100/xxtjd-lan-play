"""大厅路由：注册表决定消息归属，业务通过明确依赖参数注入。"""
import importlib.util
from pathlib import Path

def load(category):
    spec=importlib.util.spec_from_file_location('lobby_'+category,Path(__file__).with_name('1011_lobby_'+category+'_v1.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

MODULES={name:load(name) for name in ('account','growth','activities','shop','friends','mail')}
ROUTES={}
for kind in [3069]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['account'].handle_heartbeat
for kind in [3068]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['growth'].handle_equip_skin
for kind in [3240, 3249]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['growth'].handle_upgrade_star
for kind in [3400, 3424, 3425, 3426, 3427, 3428, 3053]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['growth'].handle_hero_growth
for kind in [3388, 3386, 3387, 3374, 3268, 3317, 3396, 3397, 3398, 3399, 3367, 3368, 3318, 3319, 3320, 3321, 3059, 3060, 3277, 3421, 3422, 3375, 3377, 3376, 3378]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_feature_actions
for kind in [3244, 3245, 3360, 3061, 3062, 3063]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['shop'].handle_special_shop
for kind in [3345, 3346, 3347, 3349, 3350, 3351, 3352]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_passport
for kind in [3334]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['growth'].handle_convert_currency
for kind in [3051, 3052, 3054]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['growth'].handle_rank_rewards
for kind in [3402]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['friends'].handle_friend_gift
for kind in [3401]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['friends'].handle_friend_info
for kind in [3089, 3090]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_activity_conditions
for kind in [3091]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_activity_reward
for kind in [3243, 3241]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_season_orbs
for kind in [3114]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_season_info
for kind in [3364, 3361]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_season_reward
for kind in [106, 3079, 3021, 3072, 3229, 3259, 3266, 3389, 2012, 3393, 3325, 3066, 3230, 3379]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_read_only_lists
for kind in [3055, 3056, 3058, 3027, 3335]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['friends'].handle_friend_actions
for kind in [3003, 3004]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['shop'].handle_purchase
for kind in [3365, 3366]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['shop'].handle_item_shop
for kind in [3043, 3044, 3045, 3046, 3086, 3391, 3392]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['mail'].handle_mail
for kind in [3269, 3270, 3271]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['activities'].handle_official_awards
for kind in [3102]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['account'].handle_chat
for kind in [3042, 3405, 3406, 3407, 3408, 3409, 3410, 3411, 3412, 3413, 3414]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['friends'].handle_friend_tasks
for kind in [3050, 3067, 3075, 3101]:
    if kind in ROUTES:
        raise RuntimeError('大厅消息重复注册')
    ROUTES[kind]=MODULES['account'].handle_profile

class LobbyServices:
    def __init__(self, *, BATTLE_RECORD_SQL, CHAT_LOCK, CHAT_LOG, FRIEND_COIN_GIFT, LOCAL_LEVEL, LOCAL_RANK_ID, LOCAL_RANK_SCORE, accounts, MAIL_SQL, MSG_NAMES, ROOT, SEASON_AWARD1, SEASON_AWARD2_RANK, SEASON_ORB, TABLES, add_hero, all_real_accounts, bridge, bubble_list, connect_db, economy, fill_friend, is_growth, limited_hero_price, login_response, pb, profile, season_bounds, season_info, send_mail, set_profile, skin_price, state):
        self.BATTLE_RECORD_SQL = BATTLE_RECORD_SQL
        self.CHAT_LOCK = CHAT_LOCK
        self.CHAT_LOG = CHAT_LOG
        self.FRIEND_COIN_GIFT = FRIEND_COIN_GIFT
        self.LOCAL_LEVEL = LOCAL_LEVEL
        self.LOCAL_RANK_ID = LOCAL_RANK_ID
        self.LOCAL_RANK_SCORE = LOCAL_RANK_SCORE
        self.accounts = accounts
        self.MAIL_SQL = MAIL_SQL
        self.MSG_NAMES = MSG_NAMES
        self.ROOT = ROOT
        self.SEASON_AWARD1 = SEASON_AWARD1
        self.SEASON_AWARD2_RANK = SEASON_AWARD2_RANK
        self.SEASON_ORB = SEASON_ORB
        self.TABLES = TABLES
        self.add_hero = add_hero
        self.all_real_accounts = all_real_accounts
        self.bridge = bridge
        self.bubble_list = bubble_list
        self.connect_db = connect_db
        self.economy = economy
        self.fill_friend = fill_friend
        self.is_growth = is_growth
        self.limited_hero_price = limited_hero_price
        self.login_response = login_response
        self.pb = pb
        self.profile = profile
        self.season_bounds = season_bounds
        self.season_info = season_info
        self.send_mail = send_mail
        self.set_profile = set_profile
        self.skin_price = skin_price
        self.state = state

def dispatch(services,message,account,uid,next_operation):
    handler=ROUTES.get(message.type,MODULES['account'].handle_unknown)
    return handler(services,message,account,uid,next_operation)
