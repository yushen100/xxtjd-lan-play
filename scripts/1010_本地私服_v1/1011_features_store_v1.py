"""左侧功能的数据访问层：活动任务、材料兑换、战斗宝箱、星卡、玉石结晶、碎片融合幸运值、10钻升星、每日计数。

约定同 1011_repos_v1：只用调用方传入的连接，不开连接、不提交；建表语句与拆分前逐字一致。"""
import datetime


def today():
    return datetime.date.today().isoformat()


# —— 每日计数（星卡翻牌、开宝箱次数等）——
def bump(conn, uid, key):
    conn.execute('CREATE TABLE IF NOT EXISTS daily_counters(uid TEXT, day TEXT, key TEXT, n INTEGER, PRIMARY KEY(uid, day, key))')
    conn.execute('INSERT INTO daily_counters VALUES(?,?,?,1) ON CONFLICT(uid,day,key) DO UPDATE SET n=n+1', (uid, datetime.date.today().isoformat(), key))


# —— 英雄 / 钻石（星卡兑换用）——
def owned_hero_ids(conn, uid):
    return {r[0] for r in conn.execute('SELECT hero_id FROM heroes WHERE uid=?', (uid,))}


def owns_hero(conn, uid, hero_id):
    return conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()


def add_diamonds(conn, uid, num):
    conn.execute('UPDATE accounts SET diamonds=diamonds+? WHERE uid=?', (num, uid))


# —— Features2 的三张状态表 ——
def ensure_f2_tables(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS fuse_luck(uid TEXT, fuse_id INTEGER, value INTEGER, PRIMARY KEY(uid, fuse_id));
        CREATE TABLE IF NOT EXISTS crystal_state(uid TEXT PRIMARY KEY, free_unix INTEGER, day TEXT, draws INTEGER);
        CREATE TABLE IF NOT EXISTS astro_state(uid TEXT PRIMARY KEY, client_info TEXT DEFAULT '', day TEXT DEFAULT '', free INTEGER DEFAULT 1);''')


# 碎片融合幸运值
def fuse_luck_map(conn, uid):
    return dict(conn.execute('SELECT fuse_id, value FROM fuse_luck WHERE uid=?', (uid,)).fetchall())


def fuse_luck(conn, uid, fuse_id):
    return (conn.execute('SELECT value FROM fuse_luck WHERE uid=? AND fuse_id=?', (uid, fuse_id)).fetchone() or [0])[0]


def set_fuse_luck(conn, uid, fuse_id, value):
    conn.execute('INSERT INTO fuse_luck VALUES(?,?,?) ON CONFLICT(uid,fuse_id) DO UPDATE SET value=excluded.value', (uid, fuse_id, value))


# 玉石结晶
def crystal_free_unix(conn, uid):
    st = conn.execute('SELECT free_unix FROM crystal_state WHERE uid=?', (uid,)).fetchone()
    return st[0] if st else 0


def set_crystal_free_unix(conn, uid, free_at):
    conn.execute('INSERT INTO crystal_state VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET free_unix=excluded.free_unix', (uid, free_at, today(), 0))


# 星卡
def astro_state(conn, uid):
    conn.execute('INSERT OR IGNORE INTO astro_state(uid) VALUES(?)', (uid,))
    return dict(conn.execute('SELECT * FROM astro_state WHERE uid=?', (uid,)).fetchone())


def astro_reset_day(conn, uid, free, day, dust):
    """跨天：免费次数重置，星屑清零。"""
    conn.execute('UPDATE astro_state SET free=?, day=? WHERE uid=?', (free, day, uid))
    conn.execute('UPDATE native_items SET count=0 WHERE uid=? AND item_type=? AND item_id=?', (uid, *dust))


def astro_use_free(conn, uid):
    conn.execute('UPDATE astro_state SET free=free-1 WHERE uid=?', (uid,))


def astro_set_info(conn, uid, info):
    conn.execute('INSERT INTO astro_state(uid, client_info) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET client_info=excluded.client_info', (uid, info))


# 10钻升星
def onepay_done(conn, uid, day):
    conn.execute('CREATE TABLE IF NOT EXISTS onepay(uid TEXT, day TEXT, PRIMARY KEY(uid, day))')
    return conn.execute('SELECT 1 FROM onepay WHERE uid=? AND day=?', (uid, day)).fetchone() is not None


def onepay_mark(conn, uid, day):
    conn.execute('INSERT INTO onepay VALUES(?,?)', (uid, day))


# —— 战斗宝箱 ——
def ensure_box_tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS battle_boxes(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, box_type INTEGER)')
    conn.execute('CREATE TABLE IF NOT EXISTS box_slots(uid TEXT, slot INTEGER, box_type INTEGER, get_unix INTEGER, PRIMARY KEY(uid, slot))')
    conn.execute('CREATE TABLE IF NOT EXISTS box_origins(box_id INTEGER PRIMARY KEY, uid TEXT, room_id INTEGER, slot INTEGER DEFAULT 0, gains TEXT)')


def box_count(conn, uid):
    return conn.execute('SELECT COUNT(*) FROM battle_boxes WHERE uid=?', (uid,)).fetchone()[0]


def add_box(conn, uid, box_type, room_id=None):
    box_id = conn.execute('INSERT INTO battle_boxes(uid, box_type) VALUES(?,?)', (uid, box_type)).lastrowid
    if room_id is not None:
        conn.execute('INSERT INTO box_origins(box_id,uid,room_id) VALUES(?,?,?)', (box_id, uid, room_id))
    return box_id


def boxes(conn, uid):
    return [tuple(b) for b in conn.execute('SELECT id, box_type FROM battle_boxes WHERE uid=? ORDER BY id', (uid,)).fetchall()]


def box_slots(conn, uid):
    return dict((r[0], (r[1], r[2])) for r in conn.execute('SELECT slot, box_type, get_unix FROM box_slots WHERE uid=?', (uid,)))


def box_type(conn, uid, box_id):
    b = conn.execute('SELECT box_type FROM battle_boxes WHERE uid=? AND id=?', (uid, box_id)).fetchone()
    return b[0] if b else None


def busy_slots(conn, uid):
    return {r[0] for r in conn.execute('SELECT slot FROM box_slots WHERE uid=? AND box_type>0', (uid,))}


def put_box_in_slot(conn, uid, box_id, slot, kind, ready_unix):
    conn.execute('INSERT OR REPLACE INTO box_slots VALUES(?,?,?,?)', (uid, slot, kind, ready_unix))
    conn.execute('DELETE FROM battle_boxes WHERE uid=? AND id=?', (uid, box_id))
    conn.execute('UPDATE box_origins SET slot=? WHERE uid=? AND box_id=?', (slot, uid, box_id))


def slot_box(conn, uid, slot):
    return conn.execute('SELECT box_type, get_unix FROM box_slots WHERE uid=? AND slot=?', (uid, slot)).fetchone()


def finish_slot(conn, uid, slot, gains_json):
    conn.execute('UPDATE box_origins SET gains=?,slot=0 WHERE uid=? AND slot=?', (gains_json, uid, slot))
    conn.execute('DELETE FROM box_slots WHERE uid=? AND slot=?', (uid, slot))


def delete_box(conn, uid, box_id):
    conn.execute('DELETE FROM battle_boxes WHERE uid=? AND id=?', (uid, box_id))


# —— 限时活动 ——
def ensure_event_tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS event_takes(uid TEXT, task_id INTEGER, week INTEGER, num INTEGER, PRIMARY KEY(uid, task_id, week))')


def event_taken_map(conn, uid, week):
    return dict(conn.execute('SELECT task_id, num FROM event_takes WHERE uid=? AND week=?', (uid, week)).fetchall())


def event_taken(conn, uid, task_id, week):
    return (conn.execute('SELECT num FROM event_takes WHERE uid=? AND task_id=? AND week=?', (uid, task_id, week)).fetchone() or [0])[0]


def event_take(conn, uid, task_id, week):
    conn.execute('INSERT INTO event_takes VALUES(?,?,?,1) ON CONFLICT(uid,task_id,week) DO UPDATE SET num=num+1', (uid, task_id, week))


EVENT_PROGRESS_SQL = {   # 活动任务进度：本周起的逐局战绩
    'games': 'SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=?',
    'wins': 'SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=? AND win=1',
    'mvps': 'SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=? AND mvp=1',
}


def battle_count_since(conn, uid, start, kind):
    return conn.execute(EVENT_PROGRESS_SQL[kind], (uid, start)).fetchone()[0] or 0


# —— 材料兑换 ——
def ensure_exchange_tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS exchange_takes(uid TEXT, sub INTEGER, day TEXT, num INTEGER, PRIMARY KEY(uid, sub, day))')


def exchange_taken_map(conn, uid, day):
    return dict(conn.execute('SELECT sub, num FROM exchange_takes WHERE uid=? AND day=?', (uid, day)).fetchall())


def exchange_taken(conn, uid, sub, day):
    return (conn.execute('SELECT num FROM exchange_takes WHERE uid=? AND sub=? AND day=?', (uid, sub, day)).fetchone() or [0])[0]


def exchange_take(conn, uid, sub, day):
    conn.execute('INSERT INTO exchange_takes VALUES(?,?,?,1) ON CONFLICT(uid,sub,day) DO UPDATE SET num=num+1', (uid, sub, day))


# ===================== 专属抽英雄 / 日常任务（Features）=====================
def ensure_f1_tables(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS lottery_state(uid TEXT PRIMARY KEY, luck INTEGER DEFAULT 0, day TEXT DEFAULT '', draws INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS daily_claims(uid TEXT, day TEXT, week TEXT, task_id INTEGER, acti INTEGER, PRIMARY KEY(uid, day, task_id));
        CREATE TABLE IF NOT EXISTS weekly_box_claims(uid TEXT, week TEXT, box_id INTEGER, PRIMARY KEY(uid, week, box_id));
        CREATE TABLE IF NOT EXISTS login_days(uid TEXT, day TEXT, first_unix INTEGER, PRIMARY KEY(uid, day));''')


def note_login(conn, uid, day, unix):
    conn.execute('INSERT OR IGNORE INTO login_days VALUES(?,?,?)', (uid, day, unix))


def first_login(conn, uid, day):
    r = conn.execute('SELECT first_unix FROM login_days WHERE uid=? AND day=?', (uid, day)).fetchone()
    return r[0] if r else None


def lottery_state(conn, uid):
    conn.execute('INSERT OR IGNORE INTO lottery_state(uid) VALUES(?)', (uid,))
    return dict(conn.execute('SELECT * FROM lottery_state WHERE uid=?', (uid,)).fetchone())


def save_lottery_state(conn, uid, luck, day, draws):
    conn.execute('UPDATE lottery_state SET luck=?, day=?, draws=? WHERE uid=?', (luck, day, draws, uid))


def day_games(conn, uid, day):
    """今天的 (对局数, 胜场, 排位胜场)。"""
    return conn.execute('SELECT COUNT(*), COALESCE(SUM(win),0), COALESCE(SUM(CASE WHEN mode=11 AND win=1 THEN 1 ELSE 0 END),0) FROM local_rewards WHERE uid=? AND day=?', (uid, day)).fetchone()


def kills_since(conn, uid, start):
    return conn.execute('SELECT COALESCE(SUM(kill),0) FROM battle_records WHERE uid=? AND unix>=?', (uid, start)).fetchone()[0]


def day_mode_wins(conn, uid, day, mode):
    return conn.execute('SELECT COUNT(*) FROM local_rewards WHERE uid=? AND day=? AND mode=? AND win=1', (uid, day, mode)).fetchone()[0]


def acti_on_day(conn, uid, day):
    return conn.execute('SELECT COALESCE(SUM(acti),0) FROM daily_claims WHERE uid=? AND day=?', (uid, day)).fetchone()[0]


def acti_in_week(conn, uid, week):
    return conn.execute('SELECT COALESCE(SUM(acti),0) FROM daily_claims WHERE uid=? AND week=?', (uid, week)).fetchone()[0]


def daily_claimed_ids(conn, uid, day):
    return {r[0] for r in conn.execute('SELECT task_id FROM daily_claims WHERE uid=? AND day=?', (uid, day))}


def daily_claimed(conn, uid, day, task_id):
    return conn.execute('SELECT 1 FROM daily_claims WHERE uid=? AND day=? AND task_id=?', (uid, day, task_id)).fetchone() is not None


def claim_daily(conn, uid, day, week, task_id, acti):
    conn.execute('INSERT INTO daily_claims VALUES(?,?,?,?,?)', (uid, day, week, task_id, acti))


def week_boxes_claimed(conn, uid, week):
    return {r[0] for r in conn.execute('SELECT box_id FROM weekly_box_claims WHERE uid=? AND week=?', (uid, week))}


def week_box_claimed(conn, uid, week, box_id):
    return conn.execute('SELECT 1 FROM weekly_box_claims WHERE uid=? AND week=? AND box_id=?', (uid, week, box_id)).fetchone() is not None


def claim_week_box(conn, uid, week, box_id):
    conn.execute('INSERT INTO weekly_box_claims VALUES(?,?,?)', (uid, week, box_id))


MONEY_COLUMNS = {1: 'silver', 2: 'gold', 10: 'diamonds'}


def add_money(conn, uid, kind, num):
    """加银币/金币/钻石并返回新余额。"""
    col = MONEY_COLUMNS[kind]
    conn.execute(f'UPDATE accounts SET {col}={col}+? WHERE uid=?', (num, uid))
    return conn.execute(f'SELECT {col} FROM accounts WHERE uid=?', (uid,)).fetchone()[0]


# ===================== 荣耀之路 / 签到（Features3）=====================
def ranked_wins_games(conn, uid):
    """匹配+排位（mode 11/13）的 (胜场, 总场次)。"""
    return conn.execute('SELECT COALESCE(SUM(win),0), COUNT(*) FROM local_rewards WHERE uid=? AND mode IN (11, 13)', (uid,)).fetchone()


def glory_claimed_ids(conn, uid):
    return [r[0] for r in conn.execute('SELECT id FROM glory_claims WHERE uid=?', (uid,))]


def glory_claimed(conn, uid, gid):
    return conn.execute('SELECT 1 FROM glory_claims WHERE uid=? AND id=?', (uid, gid)).fetchone() is not None


def claim_glory(conn, uid, gid):
    conn.execute('INSERT INTO glory_claims VALUES(?,?)', (uid, gid))


def sign_total(conn, uid):
    return conn.execute('SELECT COUNT(*) FROM sign_days WHERE uid=?', (uid,)).fetchone()[0]


def signed_on(conn, uid, day):
    return conn.execute('SELECT 1 FROM sign_days WHERE uid=? AND day=?', (uid, day)).fetchone() is not None


def sign(conn, uid, day):
    conn.execute('INSERT INTO sign_days VALUES(?,?)', (uid, day))


def sign_total_claimed_ids(conn, uid):
    return {x[0] for x in conn.execute('SELECT id FROM sign_total_claims WHERE uid=?', (uid,))}


def sign_total_claimed(conn, uid, sid):
    return conn.execute('SELECT 1 FROM sign_total_claims WHERE uid=? AND id=?', (uid, sid)).fetchone() is not None


def claim_sign_total(conn, uid, sid):
    conn.execute('INSERT INTO sign_total_claims VALUES(?,?)', (uid, sid))


def ensure_f3_tables(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS glory_claims(uid TEXT, id INTEGER, PRIMARY KEY(uid, id));
        CREATE TABLE IF NOT EXISTS sign_days(uid TEXT, day TEXT, PRIMARY KEY(uid, day));
        CREATE TABLE IF NOT EXISTS sign_total_claims(uid TEXT, id INTEGER, PRIMARY KEY(uid, id));''')
