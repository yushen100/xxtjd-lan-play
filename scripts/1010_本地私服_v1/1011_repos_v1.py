"""大厅数据访问层：商店、活动、邮件、官职、战绩用到的 SQL 集中在这里。

约定：所有函数接收调用方传入的 sqlite3 连接，不自行开连接、不提交事务，
事务边界由大厅处理器（with connect_db()）决定，因此行为与拆分前一致。"""
import json
import sqlite3

MONEY_COLUMNS = {1: 'silver', 2: 'gold', 10: 'diamonds'}


def _money_col(pay):
    col = MONEY_COLUMNS.get(pay)
    if col is None:          # 白名单，防止列名拼接注入
        raise ValueError('LOCAL_PRICE_UNSUPPORTED')
    return col


def ensure_schema(conn, create_sql):
    """执行服务端定义的建表语句（CREATE TABLE IF NOT EXISTS）。"""
    conn.execute(create_sql)


# —— 账号余额 ——
def balance(conn, uid, pay):
    return conn.execute(f'SELECT {_money_col(pay)} FROM accounts WHERE uid=?', (uid,)).fetchone()[0]


def add_balance(conn, uid, pay, delta):
    col = _money_col(pay)
    conn.execute(f'UPDATE accounts SET {col}={col}+? WHERE uid=?', (delta, uid))


def account_name(conn, uid):
    row = conn.execute('SELECT name FROM accounts WHERE uid=?', (uid,)).fetchone()
    return row['name'] if row else None


def real_players_except(conn, account):
    return [dict(r) for r in conn.execute(
        "SELECT uid,name FROM accounts WHERE account<>? AND uid LIKE 'local-%' AND uid NOT LIKE 'local-bot%'", (account,))]


# —— 英雄 / 皮肤 / 折扣券（商店）——
def owns_hero(conn, uid, hero_id):
    return conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone() is not None


def hero_rows(conn, uid):
    return conn.execute('SELECT hero_id,proficiency,skin_id,star,awake FROM heroes WHERE uid=? ORDER BY hero_id', (uid,)).fetchall()


def _skins_table(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')


def owns_skin(conn, uid, skin_id):
    _skins_table(conn)
    return conn.execute('SELECT 1 FROM owned_skins WHERE uid=? AND skin_id=?', (uid, skin_id)).fetchone() is not None


def add_skin(conn, uid, skin_id):
    """返回 True 表示新获得；已拥有返回 False。"""
    _skins_table(conn)
    return conn.execute('INSERT OR IGNORE INTO owned_skins VALUES(?,?)', (uid, skin_id)).rowcount > 0


def use_hero_coupon(conn, uid):
    """有 5 折券则扣一张并返回 True。"""
    conn.execute('CREATE TABLE IF NOT EXISTS hero_coupons(uid TEXT PRIMARY KEY, num INTEGER)')
    cp = conn.execute('SELECT num FROM hero_coupons WHERE uid=?', (uid,)).fetchone()
    if cp and cp[0] > 0:
        conn.execute('UPDATE hero_coupons SET num=num-1 WHERE uid=?', (uid,))
        return True
    return False


# —— 邮件 ——
def mail_list(conn, uid, limit=100):
    return [dict(m, items=json.loads(m['items'])) for m in
            conn.execute('SELECT * FROM mails WHERE uid=? ORDER BY id DESC LIMIT ?', (uid, limit))]


def mail_get(conn, uid, mail_id):
    m = conn.execute('SELECT * FROM mails WHERE uid=? AND id=?', (uid, mail_id)).fetchone()
    return dict(m, items=json.loads(m['items'])) if m else None


def mail_mark_read(conn, uid, mail_id):
    conn.execute('UPDATE mails SET is_read=1 WHERE uid=? AND id=?', (uid, mail_id))


def mail_mark_claimed(conn, mail_id):
    conn.execute('UPDATE mails SET claimed=1, is_read=1 WHERE id=?', (mail_id,))


def mail_delete(conn, uid, mail_id):
    conn.execute('DELETE FROM mails WHERE uid=? AND id=?', (uid, mail_id))


# —— 赛季商店 ——
def _season_table(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS season_shop_buys(uid TEXT, id INTEGER, num INTEGER, PRIMARY KEY(uid,id))')


def season_buys(conn, uid):
    _season_table(conn)
    return [(r[0], r[1]) for r in conn.execute('SELECT id, num FROM season_shop_buys WHERE uid=?', (uid,))]


def season_bought(conn, uid, item_id):
    _season_table(conn)
    return conn.execute('SELECT 1 FROM season_shop_buys WHERE uid=? AND id=?', (uid, item_id)).fetchone() is not None


def season_record_buy(conn, uid, item_id):
    conn.execute('INSERT INTO season_shop_buys VALUES(?,?,1)', (uid, item_id))


# —— 对局奖励统计 ——
def pve_rewards_on(conn, uid, day):
    try:
        return conn.execute("SELECT COUNT(*) FROM local_rewards WHERE uid=? AND day=? AND mode=12 AND silver>0", (uid, day)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def reward_games(conn, uid):
    try:
        return conn.execute('SELECT COUNT(*) FROM local_rewards WHERE uid=?', (uid,)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


# —— 气泡 ——
def equip_bubble(conn, uid, bubble_type, hero_id, bubble_id):
    conn.execute('INSERT OR REPLACE INTO bubble_equip VALUES(?,?,?,?)', (uid, bubble_type, hero_id, bubble_id))


# —— 官职 ——
def official_get(conn, uid, default_oid):
    conn.execute('CREATE TABLE IF NOT EXISTS officials(uid TEXT PRIMARY KEY, oid INTEGER, spent INTEGER DEFAULT 0, awarded INTEGER DEFAULT 0)')
    conn.execute('INSERT OR IGNORE INTO officials(uid,oid,awarded) VALUES(?,?,?)', (uid, default_oid, default_oid))
    return dict(conn.execute('SELECT * FROM officials WHERE uid=?', (uid,)).fetchone())


def official_promote(conn, uid, spend):
    conn.execute('UPDATE officials SET oid=oid+1, spent=spent+? WHERE uid=?', (spend, uid))


def official_mark_awarded(conn, uid, week):
    conn.execute('UPDATE officials SET awarded=? WHERE uid=?', (week, uid))


# —— 战绩 ——
def hero_fight_stats(conn, uid):
    """{英雄: (使用次数, 胜场)}，来自逐局战绩表。"""
    try:
        return {h: (n, w or 0) for h, n, w in conn.execute('SELECT hero, COUNT(*), SUM(win) FROM battle_records WHERE uid=? GROUP BY hero', (uid,))}
    except sqlite3.OperationalError:
        return {}


def battle_records(conn, uid, battle_type):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM battle_records WHERE uid=? AND (?=0 OR battle_type=?) ORDER BY id DESC', (uid, battle_type, battle_type))]


def battle_stats(conn, uid):
    return list(conn.execute('SELECT battle_type, COUNT(*), SUM(win) FROM battle_records WHERE uid=? GROUP BY battle_type', (uid,)))


def battle_record_blob(conn, record_id):
    row = conn.execute('SELECT record FROM battle_records WHERE id=?', (record_id,)).fetchone()
    return row[0] if row else None
