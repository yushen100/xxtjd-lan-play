"""好友助力英雄 + 搭档 + 搭档任务（本地实现，规则按原版界面说明）。
- 助力英雄：每人设置 1 个助力英雄；每天最多借 5 个（每日早 8:00 刷新），每个借来的英雄只能用一局。
  出战时用好友的星级/觉醒/天赋。借出方每被借一次 +20 友情点（本地规则）。
- 搭档：向好友发申请，对方同意后成为搭档（每人 1 个），可解除。
- 搭档任务：FriendTaskBaseVo，Round = 星期几（1-7），每天一组 5 个；完成后领奖。
数据库读写全部在 1011_friends_store_v1.py。"""
import datetime
import importlib.util
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_spec = importlib.util.spec_from_file_location('friends_store', Path(__file__).with_name('1011_friends_store_v1.py'))
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)

BORROW_PER_DAY = 5
LEND_REWARD_FRIENDSHIP = 20
FRIENDSHIP_ITEM = (13, 35)
TASK_COUNTER_KEYS = {62: 'astro', 63: 'box_open', 20: 'star_up', 57: 'chat_world', 65: 'use_friend_hero'}


def borrow_day(ts=None):
    t = datetime.datetime.fromtimestamp(ts or time.time()) - datetime.timedelta(hours=8)
    return t.strftime('%Y-%m-%d')


def bump(conn, uid, key, n=1):
    """每日计数器（星卡抽奖、开宝箱、升星、发言、使用助力英雄等），供搭档任务统计。"""
    store.bump(conn, uid, key, n)


class Friends:
    def __init__(self, db, tables, economy):
        self.db, self.t, self.e = db, tables, economy
        self.tasks = {r['Id']: r for r in tables['FriendTaskBaseVo']}

    @contextmanager
    def conn(self):
        """一次操作一个连接：成功提交、异常回滚，结束时关闭（原实现不关闭连接）。"""
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            store.ensure_schema(c)
            with c:
                yield c
        finally:
            c.close()

    # ---------- 助力英雄 ----------
    def battle_hero(self, c, uid, hero_id):
        row = store.hero_growth(c, uid, hero_id)
        if not row:
            return None
        use, levels = store.hero_talents(c, uid, hero_id)
        ids = []
        for tid in use:
            m = next((r['Id'] for r in self.t['TalentBaseVo'] if r['Tid'] == tid and r['Lv'] == levels.get(tid, 1)), None)
            if m:
                ids.append(m)
        return {'heroId': hero_id, 'skinId': row['skin_id'], 'proficiency': row['proficiency'], 'starLevel': row['star'],
                'awakeLevel': row['awake'], 'talentIdList': ids}

    def my_set(self, c, uid):
        return store.helper_hero(c, uid)

    def friend_hero(self, c, uid, name):
        hid = self.my_set(c, uid)
        bh = self.battle_hero(c, uid, hid) if hid else None
        return {'uid': uid, 'name': name, 'hero': bh, 'borrowCount': store.lent_count(c, uid, borrow_day())}

    def set_hero(self, uid, hero_id):
        with self.conn() as c:
            if not store.owns_hero(c, uid, hero_id):
                raise ValueError('LOCAL_HERO_UNAVAILABLE')
            store.set_helper_hero(c, uid, hero_id)
            return self.friend_hero(c, uid, store.account_name(c, uid))

    def page(self, uid):
        """好友列表（每人的助力英雄）+ 我今天借到的英雄 + 借出记录。"""
        with self.conn() as c:
            friends = []
            day = borrow_day()
            for a in store.real_players_except(c, uid):
                fh = self.friend_hero(c, a['uid'], a['name'])
                # 客户端按钮：0=已借完 1=已借 2=可借
                if store.has_borrowed_from(c, uid, a['uid'], day):
                    fh['state'] = 1
                elif store.borrowed_count(c, uid, day) >= BORROW_PER_DAY:
                    fh['state'] = 0
                else:
                    fh['state'] = 2
                if fh['hero']:
                    friends.append(fh)
            mine = []
            for b in store.borrowed_today(c, uid, day):
                bh = self.battle_hero(c, b['friend_uid'], b['hero_id'])
                if bh:
                    bh['borrowUid'] = b['friend_uid']
                    mine.append((b['friend_uid'], bh, bool(b['used'])))
            logs = [{'uid': b['uid'], 'heroId': b['hero_id'], 'borrowTime': b['borrow_time'], 'borrowDate': b['day'],
                     'borrowUid': uid, 'name': b['name']} for b in store.lend_log(c, uid)]
            me = self.friend_hero(c, uid, store.account_name(c, uid))
            return {'friends': friends, 'mine': mine, 'logs': logs, 'me': me}

    def borrow(self, uid, friend_uid):
        with self.conn() as c:
            day = borrow_day()
            if store.borrowed_count(c, uid, day) >= BORROW_PER_DAY:
                raise ValueError('LOCAL_LIMIT_REACHED')
            if store.has_borrowed_from(c, uid, friend_uid, day):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            hid = self.my_set(c, friend_uid)
            if not hid or friend_uid == uid:
                raise ValueError('LOCAL_FRIEND_UNKNOWN')
            store.record_borrow(c, uid, friend_uid, hid, day, int(time.time()))
            store.add_item(c, friend_uid, *FRIENDSHIP_ITEM, LEND_REWARD_FRIENDSHIP)
        return self.page(uid)

    def borrowed_unused(self, uid, hero_id, friend_uid=None):
        with self.conn() as c:
            return store.unused_lender(c, uid, hero_id, borrow_day(), friend_uid)

    def use_borrowed(self, uid, hero_id, friend_uid):
        with self.conn() as c:
            store.mark_borrow_used(c, uid, friend_uid, hero_id, borrow_day())
            bump(c, uid, 'use_friend_hero')

    # ---------- 搭档 ----------
    def partner(self, c, uid):
        return store.partner_of(c, uid)

    def apply(self, uid, target):
        with self.conn() as c:
            if target == uid or not store.account_exists(c, target):
                raise ValueError('LOCAL_FRIEND_UNKNOWN')
            if self.partner(c, uid):
                raise ValueError('LOCAL_ALREADY_OWNED')
            store.add_apply(c, target, uid, int(time.time()))

    def apply_list(self, uid):
        with self.conn() as c:
            return store.applies_to(c, uid)

    def reply(self, uid, from_uid, agree):
        with self.conn() as c:
            store.remove_apply(c, uid, from_uid)
            if agree:
                if self.partner(c, uid) or self.partner(c, from_uid):
                    raise ValueError('LOCAL_ALREADY_OWNED')
                store.make_partners(c, uid, from_uid)

    def delete(self, uid):
        with self.conn() as c:
            store.remove_partners(c, uid, self.partner(c, uid))

    # ---------- 搭档任务 ----------
    def _progress(self, c, uid, row, partner):
        today = datetime.date.today()
        start = int(time.mktime(today.timetuple()))
        mode = int(float(row['ConNum2'] or 0))
        try:
            if row['TaskType'] == 1 or row['Condition'] == 8:
                if row['TaskType'] == 1 and not partner:
                    return 0
                return store.games_since(c, uid, start, partner if row['TaskType'] == 1 else None, mode == 11, mode)
            if row['Condition'] == 53:
                return store.mvps_since(c, uid, start)
            key = TASK_COUNTER_KEYS.get(row['Condition'])
            return store.counter(c, uid, today.isoformat(), key) if key else 0
        except sqlite3.OperationalError:
            return 0

    def task_list(self, uid):
        rnd = datetime.date.today().isoweekday()
        with self.conn() as c:
            partner = self.partner(c, uid)
            claimed = store.claimed_tasks(c, uid, datetime.date.today().isoformat())
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
            if store.task_claimed(c, uid, day, tid):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            if self._progress(c, uid, row, self.partner(c, uid)) < row['ConNum']:
                raise ValueError('LOCAL_TASK_NOT_DONE')
            store.claim_task(c, uid, day, tid)
            if row['TaskAward'] not in (1, 2, 10):
                return self.e._grant(c, uid, row['TaskAward'], 0, row['Num'])
            count = store.add_money(c, uid, row['TaskAward'], row['Num'])
            return {'item_type': row['TaskAward'], 'item_id': 0, 'added': row['Num'], 'count': count}
