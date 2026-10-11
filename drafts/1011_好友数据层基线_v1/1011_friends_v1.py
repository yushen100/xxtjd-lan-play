"""好友助力英雄 + 搭档 + 搭档任务（本地实现，规则按原版界面说明）。
- 助力英雄：每人设置 1 个助力英雄；每天最多借 5 个（每日早 8:00 刷新），每个借来的英雄只能用一局。
  出战时用好友的星级/觉醒/天赋。借出方每被借一次 +20 友情点（本地规则）。
- 搭档：向好友发申请，对方同意后成为搭档（每人 1 个），可解除。
- 搭档任务：FriendTaskBaseVo，Round = 星期几（1-7），每天一组 5 个；完成后领奖。"""
import datetime
import json
import sqlite3
import time

BORROW_PER_DAY = 5
LEND_REWARD_FRIENDSHIP = 20


def borrow_day(ts=None):
    t = datetime.datetime.fromtimestamp(ts or time.time()) - datetime.timedelta(hours=8)
    return t.strftime('%Y-%m-%d')


def bump(conn, uid, key, n=1):
    """每日计数器（星卡抽奖、开宝箱、升星、发言、使用助力英雄等），供搭档任务统计。"""
    conn.execute('CREATE TABLE IF NOT EXISTS daily_counters(uid TEXT, day TEXT, key TEXT, n INTEGER, PRIMARY KEY(uid, day, key))')
    conn.execute('INSERT INTO daily_counters VALUES(?,?,?,?) ON CONFLICT(uid,day,key) DO UPDATE SET n=n+excluded.n',
                 (uid, datetime.date.today().isoformat(), key, n))


class Friends:
    def __init__(self, db, tables, economy):
        self.db, self.t, self.e = db, tables, economy
        self.tasks = {r['Id']: r for r in tables['FriendTaskBaseVo']}

    def conn(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        c.executescript('''
        CREATE TABLE IF NOT EXISTS friend_hero_set(uid TEXT PRIMARY KEY, hero_id INTEGER);
        CREATE TABLE IF NOT EXISTS friend_borrow(uid TEXT, friend_uid TEXT, hero_id INTEGER, day TEXT, used INTEGER DEFAULT 0,
            borrow_time INTEGER, PRIMARY KEY(uid, friend_uid, day));
        CREATE TABLE IF NOT EXISTS friend_partner(uid TEXT PRIMARY KEY, partner TEXT);
        CREATE TABLE IF NOT EXISTS friend_partner_apply(uid TEXT, from_uid TEXT, time INTEGER, PRIMARY KEY(uid, from_uid));
        CREATE TABLE IF NOT EXISTS friend_task_claims(uid TEXT, day TEXT, id INTEGER, PRIMARY KEY(uid, day, id));
        CREATE TABLE IF NOT EXISTS daily_counters(uid TEXT, day TEXT, key TEXT, n INTEGER, PRIMARY KEY(uid, day, key));''')
        return c

    # ---------- 助力英雄 ----------
    def battle_hero(self, c, uid, hero_id):
        row = c.execute('SELECT skin_id, proficiency, star, awake FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()
        if not row:
            return None
        try:
            use = [r[0] for r in c.execute('SELECT tid FROM hero_talent_use WHERE uid=? AND hero_id=? ORDER BY slot', (uid, hero_id))]
            levels = dict(c.execute('SELECT tid, level FROM hero_talents WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchall())
        except sqlite3.OperationalError:
            use, levels = [], {}
        ids = []
        for tid in use:
            m = next((r['Id'] for r in self.t['TalentBaseVo'] if r['Tid'] == tid and r['Lv'] == levels.get(tid, 1)), None)
            if m:
                ids.append(m)
        return {'heroId': hero_id, 'skinId': row['skin_id'], 'proficiency': row['proficiency'], 'starLevel': row['star'],
                'awakeLevel': row['awake'], 'talentIdList': ids}

    def my_set(self, c, uid):
        r = c.execute('SELECT hero_id FROM friend_hero_set WHERE uid=?', (uid,)).fetchone()
        return r[0] if r else 0

    def friend_hero(self, c, uid, name):
        hid = self.my_set(c, uid)
        bh = self.battle_hero(c, uid, hid) if hid else None
        lent = c.execute('SELECT COUNT(*) FROM friend_borrow WHERE friend_uid=? AND day=?', (uid, borrow_day())).fetchone()[0]
        return {'uid': uid, 'name': name, 'hero': bh, 'borrowCount': lent}

    def set_hero(self, uid, hero_id):
        with self.conn() as c:
            if not c.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone():
                raise ValueError('LOCAL_HERO_UNAVAILABLE')
            c.execute('INSERT INTO friend_hero_set VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET hero_id=excluded.hero_id', (uid, hero_id))
            name = c.execute('SELECT name FROM accounts WHERE uid=?', (uid,)).fetchone()[0]
            return self.friend_hero(c, uid, name)

    def page(self, uid):
        """好友列表（每人的助力英雄）+ 我今天借到的英雄 + 借出记录。"""
        with self.conn() as c:
            friends = []
            for a in c.execute("SELECT uid, name FROM accounts WHERE uid!=? AND uid NOT LIKE 'local-bot-%'", (uid,)).fetchall():
                fh = self.friend_hero(c, a['uid'], a['name'])
                # 客户端按钮：0=已借完 1=已借 2=可借
                day = borrow_day()
                if c.execute('SELECT 1 FROM friend_borrow WHERE uid=? AND friend_uid=? AND day=?', (uid, a['uid'], day)).fetchone():
                    fh['state'] = 1
                elif c.execute('SELECT COUNT(*) FROM friend_borrow WHERE uid=? AND day=?', (uid, day)).fetchone()[0] >= BORROW_PER_DAY:
                    fh['state'] = 0
                else:
                    fh['state'] = 2
                if fh['hero']:
                    friends.append(fh)
            mine = []
            for b in c.execute('SELECT * FROM friend_borrow WHERE uid=? AND day=? ORDER BY borrow_time', (uid, borrow_day())):
                bh = self.battle_hero(c, b['friend_uid'], b['hero_id'])
                if bh:
                    bh['borrowUid'] = b['friend_uid']
                    mine.append((b['friend_uid'], bh, bool(b['used'])))
            logs = []
            for b in c.execute('SELECT b.*, a.name FROM friend_borrow b JOIN accounts a ON a.uid=b.uid WHERE b.friend_uid=? ORDER BY borrow_time DESC LIMIT 30', (uid,)):
                logs.append({'uid': b['uid'], 'heroId': b['hero_id'], 'borrowTime': b['borrow_time'], 'borrowDate': b['day'],
                             'borrowUid': uid, 'name': b['name']})
            me = self.friend_hero(c, uid, c.execute('SELECT name FROM accounts WHERE uid=?', (uid,)).fetchone()[0])
            return {'friends': friends, 'mine': mine, 'logs': logs, 'me': me}

    def borrow(self, uid, friend_uid):
        with self.conn() as c:
            day = borrow_day()
            if c.execute('SELECT COUNT(*) FROM friend_borrow WHERE uid=? AND day=?', (uid, day)).fetchone()[0] >= BORROW_PER_DAY:
                raise ValueError('LOCAL_LIMIT_REACHED')
            if c.execute('SELECT 1 FROM friend_borrow WHERE uid=? AND friend_uid=? AND day=?', (uid, friend_uid, day)).fetchone():
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            hid = self.my_set(c, friend_uid)
            if not hid or friend_uid == uid:
                raise ValueError('LOCAL_FRIEND_UNKNOWN')
            c.execute('INSERT INTO friend_borrow(uid,friend_uid,hero_id,day,borrow_time) VALUES(?,?,?,?,?)', (uid, friend_uid, hid, day, int(time.time())))
            c.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count',
                      (friend_uid, 13, 35, LEND_REWARD_FRIENDSHIP))
        return self.page(uid)

    def borrowed_unused(self, uid, hero_id, friend_uid=None):
        with self.conn() as c:
            q = 'SELECT friend_uid FROM friend_borrow WHERE uid=? AND hero_id=? AND day=? AND used=0'
            args = [uid, hero_id, borrow_day()]
            if friend_uid:
                q += ' AND friend_uid=?'; args.append(friend_uid)
            r = c.execute(q, args).fetchone()
            return r[0] if r else None

    def use_borrowed(self, uid, hero_id, friend_uid):
        with self.conn() as c:
            c.execute('UPDATE friend_borrow SET used=1 WHERE uid=? AND friend_uid=? AND hero_id=? AND day=?', (uid, friend_uid, hero_id, borrow_day()))
            bump(c, uid, 'use_friend_hero')

    # ---------- 搭档 ----------
    def partner(self, c, uid):
        r = c.execute('SELECT partner FROM friend_partner WHERE uid=?', (uid,)).fetchone()
        return r[0] if r else None

    def apply(self, uid, target):
        with self.conn() as c:
            if target == uid or not c.execute('SELECT 1 FROM accounts WHERE uid=?', (target,)).fetchone():
                raise ValueError('LOCAL_FRIEND_UNKNOWN')
            if self.partner(c, uid):
                raise ValueError('LOCAL_ALREADY_OWNED')
            c.execute('INSERT OR REPLACE INTO friend_partner_apply VALUES(?,?,?)', (target, uid, int(time.time())))

    def apply_list(self, uid):
        with self.conn() as c:
            return [r[0] for r in c.execute('SELECT from_uid FROM friend_partner_apply WHERE uid=? ORDER BY time DESC', (uid,))]

    def reply(self, uid, from_uid, agree):
        with self.conn() as c:
            c.execute('DELETE FROM friend_partner_apply WHERE uid=? AND from_uid=?', (uid, from_uid))
            if agree:
                if self.partner(c, uid) or self.partner(c, from_uid):
                    raise ValueError('LOCAL_ALREADY_OWNED')
                c.execute('INSERT OR REPLACE INTO friend_partner VALUES(?,?)', (uid, from_uid))
                c.execute('INSERT OR REPLACE INTO friend_partner VALUES(?,?)', (from_uid, uid))
                c.execute('DELETE FROM friend_partner_apply WHERE uid IN (?,?) OR from_uid IN (?,?)', (uid, from_uid, uid, from_uid))

    def delete(self, uid):
        with self.conn() as c:
            p = self.partner(c, uid)
            c.execute('DELETE FROM friend_partner WHERE uid IN (?,?)', (uid, p or ''))

    # ---------- 搭档任务 ----------
    def _progress(self, c, uid, row, partner):
        today = datetime.date.today()
        start = int(time.mktime(today.timetuple()))
        mode = int(float(row['ConNum2'] or 0))
        cnt = lambda key: (c.execute('SELECT n FROM daily_counters WHERE uid=? AND day=? AND key=?', (uid, today.isoformat(), key)).fetchone() or [0])[0]
        try:
            if row['TaskType'] == 1 or row['Condition'] == 8:
                q = 'SELECT COUNT(*) FROM battle_records b LEFT JOIN local_rewards l ON l.room_id=b.room_id AND l.uid=b.uid WHERE b.uid=? AND b.unix>=?'
                args = [uid, start]
                if row['TaskType'] == 1:
                    if not partner:
                        return 0
                    q += ' AND EXISTS(SELECT 1 FROM battle_records p WHERE p.room_id=b.room_id AND p.uid=?)'; args.append(partner)
                if mode == 11:
                    q += ' AND l.mode=11'
                elif mode:
                    q += ' AND b.battle_type=?'; args.append(mode)
                return c.execute(q, args).fetchone()[0]
            if row['Condition'] == 53:
                return c.execute('SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=? AND mvp=1', (uid, start)).fetchone()[0]
            key = {62: 'astro', 63: 'box_open', 20: 'star_up', 57: 'chat_world', 65: 'use_friend_hero'}.get(row['Condition'])
            return cnt(key) if key else 0
        except sqlite3.OperationalError:
            return 0

    def task_list(self, uid):
        rnd = datetime.date.today().isoweekday()
        with self.conn() as c:
            partner = self.partner(c, uid)
            claimed = {r[0] for r in c.execute('SELECT id FROM friend_task_claims WHERE uid=? AND day=?', (uid, datetime.date.today().isoformat()))}
            out = []
            for r in sorted((r for r in self.tasks.values() if r['Round'] == rnd), key=lambda r: r['Sort']):
                p = min(self._progress(c, uid, r, partner), r['ConNum'])
                out.append((r['Id'], p, 1 if r['Id'] in claimed else 0))
            return out, partner

    def task_reward(self, uid, tid):
        row = self.tasks.get(tid)
        if not row:
            raise ValueError('LOCAL_TASK_UNKNOWN')
        with self.conn() as c:
            day = datetime.date.today().isoformat()
            if c.execute('SELECT 1 FROM friend_task_claims WHERE uid=? AND day=? AND id=?', (uid, day, tid)).fetchone():
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            if self._progress(c, uid, row, self.partner(c, uid)) < row['ConNum']:
                raise ValueError('LOCAL_TASK_NOT_DONE')
            c.execute('INSERT INTO friend_task_claims VALUES(?,?,?)', (uid, day, tid))
            got = self.e._grant(c, uid, row['TaskAward'], 0, row['Num']) if row['TaskAward'] not in (1, 2, 10) else None
            if got is None:
                col = {1: 'silver', 2: 'gold', 10: 'diamonds'}[row['TaskAward']]
                c.execute(f'UPDATE accounts SET {col}={col}+? WHERE uid=?', (row['Num'], uid))
                got = {'item_type': row['TaskAward'], 'item_id': 0, 'added': row['Num'],
                       'count': c.execute(f'SELECT {col} FROM accounts WHERE uid=?', (uid,)).fetchone()[0]}
            return got
