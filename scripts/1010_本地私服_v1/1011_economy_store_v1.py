"""经济/成长数据访问层：账号余额、背包道具、商店购买计数、请求幂等记录、英雄星级/觉醒/熟练度、天赋、皮肤所有权。
只用调用方连接，不开连接、不提交；建表语句与拆分前一致。"""
import json

MONEY_COLUMNS = {1: 'silver', 2: 'gold', 10: 'diamonds'}

SCHEMA = '''
            CREATE TABLE IF NOT EXISTS native_items(
              uid TEXT NOT NULL REFERENCES accounts(uid), item_type INTEGER NOT NULL,
              item_id INTEGER NOT NULL, count INTEGER NOT NULL CHECK(count>=0),
              PRIMARY KEY(uid,item_type,item_id));
            CREATE TABLE IF NOT EXISTS shop_counts(
              uid TEXT NOT NULL REFERENCES accounts(uid), store_id INTEGER NOT NULL,
              period TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=0),
              PRIMARY KEY(uid,store_id,period));
            '''
TALENT_SCHEMA = '''
        CREATE TABLE IF NOT EXISTS hero_talents(uid TEXT NOT NULL, hero_id INTEGER NOT NULL, tid INTEGER NOT NULL,
          level INTEGER NOT NULL CHECK(level>=0), PRIMARY KEY(uid,hero_id,tid));
        CREATE TABLE IF NOT EXISTS hero_talent_use(uid TEXT NOT NULL, hero_id INTEGER NOT NULL, slot INTEGER NOT NULL,
          tid INTEGER NOT NULL, PRIMARY KEY(uid,hero_id,slot));'''


def ensure_schema(conn):
    conn.executescript(SCHEMA)


def ensure_talent_schema(conn):
    conn.executescript(TALENT_SCHEMA)


# —— 账号 ——
def account(conn, uid):
    return conn.execute('SELECT * FROM accounts WHERE uid=?', (uid,)).fetchone()


def silver(conn, uid):
    return conn.execute('SELECT silver FROM accounts WHERE uid=?', (uid,)).fetchone()


def charge(conn, uid, silver_, gold, diamonds, rank_score):
    conn.execute('UPDATE accounts SET silver=silver-?,gold=gold-?,diamonds=diamonds-?,rank_score=rank_score-? WHERE uid=?',
                 (silver_, gold, diamonds, rank_score, uid))


def sub_money(conn, uid, kind, num):
    col = MONEY_COLUMNS[kind]
    conn.execute(f'UPDATE accounts SET {col}={col}-? WHERE uid=?', (num, uid))


def add_diamonds(conn, uid, num):
    conn.execute('UPDATE accounts SET diamonds=diamonds+? WHERE uid=?', (num, uid))
    return conn.execute('SELECT diamonds FROM accounts WHERE uid=?', (uid,)).fetchone()[0]


# —— 道具 ——
def items(conn, uid, item_type=None):
    if item_type is None:
        return [dict(row) for row in conn.execute('SELECT * FROM native_items WHERE uid=?', (uid,))]
    return [dict(row) for row in conn.execute('SELECT * FROM native_items WHERE uid=? AND item_type=?', (uid, item_type))]


def item_count(conn, uid, kind, item):
    row = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid, kind, item)).fetchone()
    return row['count'] if row else 0


def add_item(conn, uid, kind, item, num):
    conn.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count', (uid, kind, item, num))
    return conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid, kind, item)).fetchone()[0]


def sub_item(conn, uid, kind, item, num):
    conn.execute('UPDATE native_items SET count=count-? WHERE uid=? AND item_type=? AND item_id=?', (num, uid, kind, item))


def star_items(conn, uid, hero_id):
    """升星相关道具：该英雄碎片、通用碎片、激活石。"""
    return [dict(row) for row in conn.execute('SELECT * FROM native_items WHERE uid=? AND ((item_type=22 AND item_id IN (?,0)) OR (item_type=13 AND item_id=19))', (uid, hero_id))]


# —— 商店购买计数 ——
def shop_count(conn, uid, store_id, period):
    row = conn.execute('SELECT count FROM shop_counts WHERE uid=? AND store_id=? AND period=?', (uid, store_id, period)).fetchone()
    return row['count'] if row else None


def add_shop_count(conn, uid, store_id, period, quantity):
    conn.execute('INSERT INTO shop_counts VALUES(?,?,?,?) ON CONFLICT(uid,store_id,period) DO UPDATE SET count=count+excluded.count',
                 (uid, store_id, period, quantity))


# —— 请求幂等 ——
def operation(conn, uid, operation_id):
    old = conn.execute('SELECT result FROM operations WHERE uid=? AND operation_id=?', (uid, operation_id)).fetchone()
    return json.loads(old['result']) if old else None


def save_operation(conn, uid, operation_id, result):
    conn.execute('INSERT INTO operations VALUES(?,?,?)', (uid, operation_id, json.dumps(result)))


# —— 英雄 / 皮肤 ——
def hero(conn, uid, hero_id):
    row = conn.execute('SELECT * FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()
    return dict(row) if row else None


def owns_hero(conn, uid, hero_id):
    return conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone() is not None


def insert_hero(conn, uid, hero_id, skin_id):
    conn.execute('INSERT INTO heroes(uid,hero_id,skin_id) VALUES(?,?,?)', (uid, hero_id, skin_id))


def set_star(conn, uid, hero_id, star):
    conn.execute('UPDATE heroes SET star=? WHERE uid=? AND hero_id=?', (star, uid, hero_id))


def add_awake(conn, uid, hero_id, levels):
    conn.execute('UPDATE heroes SET awake=awake+? WHERE uid=? AND hero_id=?', (levels, uid, hero_id))


def reset_proficiency(conn, uid, hero_id):
    return conn.execute('UPDATE heroes SET proficiency=0 WHERE uid=? AND hero_id=?', (uid, hero_id)).rowcount


def add_proficiency(conn, uid, hero_id, amount):
    conn.execute('UPDATE heroes SET proficiency=proficiency+? WHERE uid=? AND hero_id=?', (amount, uid, hero_id))


def add_skin(conn, uid, skin_id):
    """新获得返回 True，已拥有返回 False。"""
    conn.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')
    return conn.execute('INSERT OR IGNORE INTO owned_skins VALUES(?,?)', (uid, skin_id)).rowcount > 0


def ensure_skin_table(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')


# —— 天赋 ——
def talent_levels(conn, uid, hero_id):
    return {r['tid']: r['level'] for r in conn.execute('SELECT tid,level FROM hero_talents WHERE uid=? AND hero_id=?', (uid, hero_id))}


def talent_level(conn, uid, hero_id, tid):
    row = conn.execute('SELECT level FROM hero_talents WHERE uid=? AND hero_id=? AND tid=?', (uid, hero_id, tid)).fetchone()
    return row['level'] if row else None


def set_talent_level(conn, uid, hero_id, tid, level):
    conn.execute('INSERT INTO hero_talents VALUES(?,?,?,?) ON CONFLICT(uid,hero_id,tid) DO UPDATE SET level=excluded.level', (uid, hero_id, tid, level))


def talent_use(conn, uid, hero_id):
    return [r['tid'] for r in conn.execute('SELECT tid FROM hero_talent_use WHERE uid=? AND hero_id=? ORDER BY slot', (uid, hero_id))]


def clear_talent_use(conn, uid, hero_id):
    conn.execute('DELETE FROM hero_talent_use WHERE uid=? AND hero_id=?', (uid, hero_id))


def set_talent_slot(conn, uid, hero_id, slot, tid):
    conn.execute('INSERT INTO hero_talent_use VALUES(?,?,?,?)', (uid, hero_id, slot, tid))
