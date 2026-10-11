"""通行证数据访问层：进度、商店限购、任务完成次数、本周战绩统计。只用调用方连接，不开连接、不提交。"""
MONEY_COLUMNS = {1: 'silver', 2: 'gold', 10: 'diamonds'}


def ensure_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS passport(uid TEXT PRIMARY KEY, grade INTEGER DEFAULT 0, exp INTEGER DEFAULT 0,
            taken_normal INTEGER DEFAULT 0, taken_good INTEGER DEFAULT 0, week TEXT DEFAULT '', week_exp INTEGER DEFAULT 0)''')
    conn.execute('CREATE TABLE IF NOT EXISTS passport_shop(uid TEXT, id INTEGER, num INTEGER, PRIMARY KEY(uid,id))')


def row(conn, uid):
    conn.execute('INSERT OR IGNORE INTO passport(uid) VALUES(?)', (uid,))
    return dict(conn.execute('SELECT * FROM passport WHERE uid=?', (uid,)).fetchone())


def add_week_exp(conn, uid, gain, week, week_exp):
    conn.execute('UPDATE passport SET exp=exp+?, week=?, week_exp=? WHERE uid=?', (gain, week, week_exp, uid))


def add_exp(conn, uid, exp):
    conn.execute('UPDATE passport SET exp=exp+? WHERE uid=?', (exp, uid))


def set_grade(conn, uid, grade, add_exp_, exp_cap):
    conn.execute('UPDATE passport SET grade=?, exp=MIN(exp+?, ?) WHERE uid=?', (grade, add_exp_, exp_cap, uid))


def set_taken(conn, uid, normal, good):
    conn.execute('UPDATE passport SET taken_normal=?, taken_good=? WHERE uid=?', (normal, good, uid))


def add_money(conn, uid, kind, num):
    col = MONEY_COLUMNS[kind]
    conn.execute(f'UPDATE accounts SET {col}={col}+? WHERE uid=?', (num, uid))
    return conn.execute(f'SELECT {col} FROM accounts WHERE uid=?', (uid,)).fetchone()[0]


def shop_bought(conn, uid):
    return dict(conn.execute('SELECT id, num FROM passport_shop WHERE uid=?', (uid,)).fetchall())


def shop_bought_one(conn, uid, sid):
    return (conn.execute('SELECT num FROM passport_shop WHERE uid=? AND id=?', (uid, sid)).fetchone() or [0])[0]


def shop_record(conn, uid, sid, num):
    conn.execute('INSERT INTO passport_shop VALUES(?,?,?) ON CONFLICT(uid,id) DO UPDATE SET num=num+excluded.num', (uid, sid, num))


def ensure_task_table(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS passport_task_done(uid TEXT, week TEXT, id INTEGER, num INTEGER, PRIMARY KEY(uid,week,id))')


def task_done(conn, uid, week, tid):
    return (conn.execute('SELECT num FROM passport_task_done WHERE uid=? AND week=? AND id=?', (uid, week, tid)).fetchone() or [0])[0]


def set_task_done(conn, uid, week, tid, num):
    conn.execute('INSERT INTO passport_task_done VALUES(?,?,?,?) ON CONFLICT(uid,week,id) DO UPDATE SET num=excluded.num', (uid, week, tid, num))


WEEK_STATS_SQL = {   # 本周（周一 0 点起）的逐局战绩统计
    'games': 'SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=?',
    'wins': 'SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=? AND win=1',
    'kills': 'SELECT SUM(kill) FROM battle_records WHERE uid=? AND unix>=?',
    'rank_wins': 'SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=? AND win=1 AND battle_type IN (11, 22)',
}


def week_stat(conn, uid, since, kind):
    return (conn.execute(WEEK_STATS_SQL[kind], (uid, since)).fetchone() or [0])[0] or 0
