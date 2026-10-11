"""好友助力 / 搭档 / 搭档任务的数据访问层。只用调用方连接，不开连接、不提交；建表语句与拆分前一致。"""
import datetime
import sqlite3

SCHEMA = '''
        CREATE TABLE IF NOT EXISTS friend_hero_set(uid TEXT PRIMARY KEY, hero_id INTEGER);
        CREATE TABLE IF NOT EXISTS friend_borrow(uid TEXT, friend_uid TEXT, hero_id INTEGER, day TEXT, used INTEGER DEFAULT 0,
            borrow_time INTEGER, PRIMARY KEY(uid, friend_uid, day));
        CREATE TABLE IF NOT EXISTS friend_partner(uid TEXT PRIMARY KEY, partner TEXT);
        CREATE TABLE IF NOT EXISTS friend_partner_apply(uid TEXT, from_uid TEXT, time INTEGER, PRIMARY KEY(uid, from_uid));
        CREATE TABLE IF NOT EXISTS friend_task_claims(uid TEXT, day TEXT, id INTEGER, PRIMARY KEY(uid, day, id));
        CREATE TABLE IF NOT EXISTS daily_counters(uid TEXT, day TEXT, key TEXT, n INTEGER, PRIMARY KEY(uid, day, key));'''

MONEY_COLUMNS = {1: 'silver', 2: 'gold', 10: 'diamonds'}


def ensure_schema(conn):
    conn.executescript(SCHEMA)


def bump(conn, uid, key, n=1):
    conn.execute('CREATE TABLE IF NOT EXISTS daily_counters(uid TEXT, day TEXT, key TEXT, n INTEGER, PRIMARY KEY(uid, day, key))')
    conn.execute('INSERT INTO daily_counters VALUES(?,?,?,?) ON CONFLICT(uid,day,key) DO UPDATE SET n=n+excluded.n',
                 (uid, datetime.date.today().isoformat(), key, n))


def counter(conn, uid, day, key):
    return (conn.execute('SELECT n FROM daily_counters WHERE uid=? AND day=? AND key=?', (uid, day, key)).fetchone() or [0])[0]


# —— 账号 / 英雄 ——
def account_name(conn, uid):
    return conn.execute('SELECT name FROM accounts WHERE uid=?', (uid,)).fetchone()[0]


def account_exists(conn, uid):
    return conn.execute('SELECT 1 FROM accounts WHERE uid=?', (uid,)).fetchone() is not None


def real_players_except(conn, uid):
    return conn.execute("SELECT uid, name FROM accounts WHERE uid!=? AND uid NOT LIKE 'local-bot-%'", (uid,)).fetchall()


def owns_hero(conn, uid, hero_id):
    return conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone() is not None


def hero_growth(conn, uid, hero_id):
    return conn.execute('SELECT skin_id, proficiency, star, awake FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()


def hero_talents(conn, uid, hero_id):
    """(装备顺序的天赋 tid 列表, {tid: 等级})；天赋表还不存在时返回空。"""
    try:
        use = [r[0] for r in conn.execute('SELECT tid FROM hero_talent_use WHERE uid=? AND hero_id=? ORDER BY slot', (uid, hero_id))]
        levels = dict(conn.execute('SELECT tid, level FROM hero_talents WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchall())
    except sqlite3.OperationalError:
        use, levels = [], {}
    return use, levels


def add_money(conn, uid, kind, num):
    col = MONEY_COLUMNS[kind]
    conn.execute(f'UPDATE accounts SET {col}={col}+? WHERE uid=?', (num, uid))
    return conn.execute(f'SELECT {col} FROM accounts WHERE uid=?', (uid,)).fetchone()[0]


def add_item(conn, uid, kind, item, num):
    conn.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count',
                 (uid, kind, item, num))


# —— 助力英雄 ——
def helper_hero(conn, uid):
    r = conn.execute('SELECT hero_id FROM friend_hero_set WHERE uid=?', (uid,)).fetchone()
    return r[0] if r else 0


def set_helper_hero(conn, uid, hero_id):
    conn.execute('INSERT INTO friend_hero_set VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET hero_id=excluded.hero_id', (uid, hero_id))


def lent_count(conn, lender, day):
    return conn.execute('SELECT COUNT(*) FROM friend_borrow WHERE friend_uid=? AND day=?', (lender, day)).fetchone()[0]


def borrowed_count(conn, uid, day):
    return conn.execute('SELECT COUNT(*) FROM friend_borrow WHERE uid=? AND day=?', (uid, day)).fetchone()[0]


def has_borrowed_from(conn, uid, lender, day):
    return conn.execute('SELECT 1 FROM friend_borrow WHERE uid=? AND friend_uid=? AND day=?', (uid, lender, day)).fetchone() is not None


def borrowed_today(conn, uid, day):
    return conn.execute('SELECT * FROM friend_borrow WHERE uid=? AND day=? ORDER BY borrow_time', (uid, day)).fetchall()


def lend_log(conn, lender, limit=30):
    return conn.execute('SELECT b.*, a.name FROM friend_borrow b JOIN accounts a ON a.uid=b.uid WHERE b.friend_uid=? ORDER BY borrow_time DESC LIMIT ?',
                        (lender, limit)).fetchall()


def record_borrow(conn, uid, lender, hero_id, day, unix):
    conn.execute('INSERT INTO friend_borrow(uid,friend_uid,hero_id,day,borrow_time) VALUES(?,?,?,?,?)', (uid, lender, hero_id, day, unix))


def unused_lender(conn, uid, hero_id, day, lender=None):
    q = 'SELECT friend_uid FROM friend_borrow WHERE uid=? AND hero_id=? AND day=? AND used=0'
    args = [uid, hero_id, day]
    if lender:
        q += ' AND friend_uid=?'; args.append(lender)
    r = conn.execute(q, args).fetchone()
    return r[0] if r else None


def mark_borrow_used(conn, uid, lender, hero_id, day):
    conn.execute('UPDATE friend_borrow SET used=1 WHERE uid=? AND friend_uid=? AND hero_id=? AND day=?', (uid, lender, hero_id, day))


# —— 搭档 ——
def partner_of(conn, uid):
    r = conn.execute('SELECT partner FROM friend_partner WHERE uid=?', (uid,)).fetchone()
    return r[0] if r else None


def add_apply(conn, target, from_uid, unix):
    conn.execute('INSERT OR REPLACE INTO friend_partner_apply VALUES(?,?,?)', (target, from_uid, unix))


def applies_to(conn, uid):
    return [r[0] for r in conn.execute('SELECT from_uid FROM friend_partner_apply WHERE uid=? ORDER BY time DESC', (uid,))]


def remove_apply(conn, uid, from_uid):
    conn.execute('DELETE FROM friend_partner_apply WHERE uid=? AND from_uid=?', (uid, from_uid))


def make_partners(conn, a, b):
    conn.execute('INSERT OR REPLACE INTO friend_partner VALUES(?,?)', (a, b))
    conn.execute('INSERT OR REPLACE INTO friend_partner VALUES(?,?)', (b, a))
    conn.execute('DELETE FROM friend_partner_apply WHERE uid IN (?,?) OR from_uid IN (?,?)', (a, b, a, b))


def remove_partners(conn, uid, partner):
    conn.execute('DELETE FROM friend_partner WHERE uid IN (?,?)', (uid, partner or ''))


# —— 搭档任务 ——
def claimed_tasks(conn, uid, day):
    return {r[0] for r in conn.execute('SELECT id FROM friend_task_claims WHERE uid=? AND day=?', (uid, day))}


def task_claimed(conn, uid, day, tid):
    return conn.execute('SELECT 1 FROM friend_task_claims WHERE uid=? AND day=? AND id=?', (uid, day, tid)).fetchone() is not None


def claim_task(conn, uid, day, tid):
    conn.execute('INSERT INTO friend_task_claims VALUES(?,?,?)', (uid, day, tid))


def games_since(conn, uid, start, partner=None, rank_only=False, battle_type=0):
    """今天起的对局数；partner 给出时只算和搭档同局的；rank_only 只算排位；battle_type 只算该模式。"""
    q = 'SELECT COUNT(*) FROM battle_records b LEFT JOIN local_rewards l ON l.room_id=b.room_id AND l.uid=b.uid WHERE b.uid=? AND b.unix>=?'
    args = [uid, start]
    if partner:
        q += ' AND EXISTS(SELECT 1 FROM battle_records p WHERE p.room_id=b.room_id AND p.uid=?)'; args.append(partner)
    if rank_only:
        q += ' AND l.mode=11'
    elif battle_type:
        q += ' AND b.battle_type=?'; args.append(battle_type)
    return conn.execute(q, args).fetchone()[0]


def mvps_since(conn, uid, start):
    return conn.execute('SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=? AND mvp=1', (uid, start)).fetchone()[0]
