"""账号建号与登录补发规则（从 1010_server_v1.py 拆出，行为保持一致）。

全部函数只接收调用方的 sqlite3 连接，不自行开连接、不提交；每批补发都有自己的“已发放”标记表，
重复登录不会重复发。规则常量集中在本文件顶部，改规则只改这里。"""
import json
import random
import re
import time

ACCOUNT_RE = re.compile(r'^[A-Za-z0-9_]{2,16}$')

# —— 养成号（新号）规则 ——
NEW_ACCOUNT_GROWTH = True   # True：从现在起新建的账号都是养成号；老账号保持全解锁
GROWTH_START = {   # 按《1011_养成与左侧功能最终计划_v1》第 1 节
    'silver': 50000, 'gold': 20000, 'diamonds': 3000,
    'limited_random': 3,           # 46 个限定英雄里随机送 3 个；91 个可购买英雄（IsBuy=0）全部送
    'items': [(13, 35, 5000)],     # 友情点 5000；另外照发 v1“5 名英雄升满”材料包
    'coupons': 1,                  # 限定英雄 5 折券
}
LEGACY_GROWTH_DIAMONDS = 1000      # 旧版养成号（没有 growth_start_v2 记录）补到计划值

# —— 特殊补给（仅名单内账号，用户指定；新养成号不发）——
SPECIAL_REFILL_UIDS = {'local-wq'}
SPECIAL_REFILL_COLUMNS = {'diamonds': 10000, 'gold': 50000, 'silver': 200000}
SPECIAL_REFILL_ITEMS = [(43, 0, 3000), (13, 220, 3000), (13, 11, 300), (13, 202, 2000), (13, 12, 200), (13, 200, 100), (13, 201, 5), (13, 561, 2000)]
# 后续追加的材料补给（每批一次，仅特殊补给名单）：(标记表, [(类型, ID, 数量)])
MATERIAL_REFILLS = [
    ('material_refill_v3', [(43, 0, 5000), (22, 0, 3000), (13, 19, 3000), (41, 0, 3000), (13, 220, 5000),
                            (13, 11, 500), (13, 202, 5000), (13, 12, 300), (13, 200, 300), (13, 205, 2000), (13, 206, 500)]),
]
EVENT_REFILL_NUM = 500             # 轮换活动道具各 500
EVENT_REFILL_IDS = (95, 121)       # 轮换池：Id 95–121
EVENT_REFILL_EXTRA = (47, 60, 62, 64, 65, 67, 71, 85, 87)

# —— 每个账号一次性的材料批次（老号/养成号都发 v1；v2–v6 只发老号）——
STARTER_HEROES = 5
STARTER_ITEMS = [(22, 0, 1258), (13, 19, 1258), (41, 0, 1258), (13, 205, 1258), (13, 206, 180), (13, 220, 6000)]
STARTER_SILVER = 12580 + 11810 + 22000
STARTER_V2_ITEMS = [(43, 0, 6000 * STARTER_HEROES), (13, 211, 20000), (13, 20, 20000), (13, 18, 2000), (13, 21, 20000),
                    (13, 210, 100), (13, 203, 50), (13, 204, 50), (13, 213, 100), (13, 214, 100), (13, 215, 100),
                    (13, 216, 100), (13, 217, 100), (13, 218, 100), (4, 0, 20000), (25, 0, 10), (27, 0, 100),
                    (13, 35, 50000)]  # 13:35 友情币（客户端 ShopFriendPanel 读 getItemValue(13,35)）
STARTER_V2_COLUMNS = {'gold': 200000, 'diamonds': 50000, 'friendship': 50000}
STARTER_V3_ITEMS = [(13, 38, 3000)] + [(12, i, 300) for i in range(1, 9)]          # 通行币 + 英雄/皮肤碎片
STARTER_V4_ITEMS = [(13, 12, 200)]                                                  # 专属抽英雄宝石
STARTER_V5_ITEMS = [(13, 200, 50), (13, 11, 50), (13, 202, 2000)]                   # 融合券、晶石、星屑
STARTER_V6_ITEMS = [(13, 11, 3000), (13, 200, 1000), (13, 202, 20000), (13, 20, 20000)]  # 资源补足

# —— 赛季 ——
RANK_SKIN_WINS = 10                # 排位每赢 10 局：1 颗赛季宝珠 / 按顺序解锁一款排位皮肤
SEASON_ORB = (13, 201)
SEASON_AWARD1 = {'score': 1375, 'wins': 10}     # 黄金V 以上且胜 10 场 → 赛季宝珠 ×1
SEASON_AWARD2_RANK = 21                          # 钻石V 以上 → 金币（按 RankAwardBaseVo）

MAIL_SQL = """CREATE TABLE IF NOT EXISTS mails(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, sender TEXT, title TEXT,
    content TEXT, ctime INTEGER, is_read INTEGER DEFAULT 0, items TEXT DEFAULT '[]', claimed INTEGER DEFAULT 0)"""
NATIVE_ITEMS_SQL = """CREATE TABLE IF NOT EXISTS native_items(uid TEXT NOT NULL, item_type INTEGER NOT NULL,
              item_id INTEGER NOT NULL, count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(uid,item_type,item_id))"""


def add_item(conn, uid, kind, item, num):
    conn.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count',
                 (uid, kind, item, num))


def send_mail(conn, uid, sender, title, content, items):
    conn.execute(MAIL_SQL)
    conn.execute('INSERT INTO mails(uid,sender,title,content,ctime,items) VALUES(?,?,?,?,?,?)',
                 (uid, sender, title, content, int(time.time()), json.dumps(items)))


def _once(conn, table, uid):
    """标记表里没有该账号时返回 True（调用方发完后再 _mark）。"""
    conn.execute(f'CREATE TABLE IF NOT EXISTS {table}(uid TEXT PRIMARY KEY, time INTEGER)')
    return not conn.execute(f'SELECT 1 FROM {table} WHERE uid=?', (uid,)).fetchone()


def _mark(conn, table, uid):
    conn.execute(f'INSERT INTO {table} VALUES(?,?)', (uid, int(time.time())))


class AccountGrants:
    def __init__(self, tables, season_bounds, season_info):
        self.t = tables
        self.season_bounds = season_bounds
        self.season_info = season_info
        self._default_skin = {}
        for k in tables['SkinIdBaseVo']:
            if k['Type'] == 1 and k['HeroId'] not in self._default_skin:
                self._default_skin[k['HeroId']] = k['Id']

    # —— 英雄 ——
    def add_hero(self, conn, uid, hero_id):
        skin = self._default_skin.get(hero_id)
        if skin is None:
            return   # 客户端没有默认皮肤的英雄不发
        conn.execute('INSERT OR IGNORE INTO heroes(uid,hero_id) VALUES(?,?)', (uid, hero_id))
        conn.execute('UPDATE heroes SET skin_id=? WHERE uid=? AND hero_id=? AND skin_id=0', (skin, uid, hero_id))

    # —— 建号 ——
    def ensure_account(self, conn, account, name=None):
        """局域网规则：任意合法账号名首次登录即建号；新号为养成号，机器人与旧规则账号全解锁。"""
        if not ACCOUNT_RE.match(account or ''):
            raise ValueError('账号名只能是2-16位字母、数字或下划线')
        if conn.execute('SELECT 1 FROM accounts WHERE account=?', (account,)).fetchone():
            return
        uid = 'local-' + account
        growth = NEW_ACCOUNT_GROWTH and not uid.startswith('local-bot-')
        conn.execute('INSERT OR IGNORE INTO accounts(account,uid,name,silver,gold,diamonds,friendship) VALUES(?,?,?,?,?,?,?)',
                     (account, uid, name or ('玩家' + account),
                      GROWTH_START['silver'] if growth else 50000, GROWTH_START['gold'] if growth else 0,
                      GROWTH_START['diamonds'] if growth else 0, 0))
        if growth:
            # 养成号：只给起步英雄 + 少量材料；把老号的全解锁批次标记为已领，避免再发
            conn.execute('CREATE TABLE IF NOT EXISTS growth_accounts(uid TEXT PRIMARY KEY, time INTEGER)')
            conn.execute('INSERT OR IGNORE INTO growth_accounts VALUES(?,?)', (uid, int(time.time())))
            for v in ('_v2', '_v3', '_v4', '_v5', '_v6'):   # v1“5 名英雄升满”材料照发
                conn.execute(f'CREATE TABLE IF NOT EXISTS starter_grants{v}(uid TEXT PRIMARY KEY, time INTEGER)')
                conn.execute(f'INSERT OR IGNORE INTO starter_grants{v} VALUES(?,?)', (uid, int(time.time())))
            conn.execute(NATIVE_ITEMS_SQL)
            self.grant_growth_start(conn, uid)
            return
        # 老规则：初始开放本机配置中全部英雄（含没有默认皮肤的），进度独立保存。
        for hero in self.t['HeroBaseVo']:
            conn.execute('INSERT OR IGNORE INTO heroes(uid,hero_id) VALUES(?,?)', (uid, hero['Id']))
            skin = self._default_skin.get(hero['Id'])
            if skin is not None:
                conn.execute('UPDATE heroes SET skin_id=? WHERE uid=? AND hero_id=? AND skin_id=0', (skin, uid, hero['Id']))

    def grant_growth_start(self, conn, uid):
        """养成号开局（一次）：全部可购买英雄 + 随机 3 个限定英雄 + 默认皮肤 + 少量货币 + 5 折券。"""
        if not _once(conn, 'growth_start_v2', uid):
            return
        for h in self.t['HeroBaseVo']:
            if h['IsBuy'] == 0:
                self.add_hero(conn, uid, h['Id'])
        limited = [h['Id'] for h in self.t['HeroBaseVo'] if h['IsBuy'] != 0 and h['Id'] in self._default_skin]
        for hid in random.sample(limited, min(GROWTH_START['limited_random'], len(limited))):
            self.add_hero(conn, uid, hid)
        for kind, item, num in GROWTH_START['items']:
            add_item(conn, uid, kind, item, num)
        conn.execute('CREATE TABLE IF NOT EXISTS hero_coupons(uid TEXT PRIMARY KEY, num INTEGER)')
        conn.execute('INSERT INTO hero_coupons VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET num=num+excluded.num', (uid, GROWTH_START['coupons']))
        _mark(conn, 'growth_start_v2', uid)

    # —— 一次性批次 ——
    def grant_starter(self, conn, uid):
        if not _once(conn, 'starter_grants', uid):
            return
        conn.execute(NATIVE_ITEMS_SQL)
        for kind, item, num in STARTER_ITEMS:
            add_item(conn, uid, kind, item, num * STARTER_HEROES)
        conn.execute('UPDATE accounts SET silver=silver+? WHERE uid=?', (STARTER_SILVER * STARTER_HEROES, uid))
        _mark(conn, 'starter_grants', uid)

    def grant_starter_v2(self, conn, uid):
        if not _once(conn, 'starter_grants_v2', uid):
            return
        for kind, item, num in STARTER_V2_ITEMS:
            add_item(conn, uid, kind, item, num)
        conn.execute('UPDATE accounts SET gold=gold+?, diamonds=diamonds+?, friendship=friendship+? WHERE uid=?',
                     (STARTER_V2_COLUMNS['gold'], STARTER_V2_COLUMNS['diamonds'], STARTER_V2_COLUMNS['friendship'], uid))
        _mark(conn, 'starter_grants_v2', uid)

    def _batch(self, conn, uid, table, items):
        if not _once(conn, table, uid):
            return
        for kind, item, num in items:
            add_item(conn, uid, kind, item, num)
        _mark(conn, table, uid)

    # —— 特殊补给 ——
    def special_refill(self, conn, uid):
        conn.execute('CREATE TABLE IF NOT EXISTS growth_refill_v2(uid TEXT PRIMARY KEY, time INTEGER)')
        if uid in SPECIAL_REFILL_UIDS and not conn.execute('SELECT 1 FROM growth_refill_v2 WHERE uid=?', (uid,)).fetchone():
            c = SPECIAL_REFILL_COLUMNS
            conn.execute('UPDATE accounts SET diamonds=diamonds+?, gold=gold+?, silver=silver+? WHERE uid=?', (c['diamonds'], c['gold'], c['silver'], uid))
            for kind, item, num in SPECIAL_REFILL_ITEMS:
                add_item(conn, uid, kind, item, num)
            conn.execute('INSERT INTO growth_refill_v2 VALUES(?,?)', (uid, int(time.time())))
        if uid in SPECIAL_REFILL_UIDS:
            conn.execute('CREATE TABLE IF NOT EXISTS event_refill_v1(uid TEXT PRIMARY KEY, unix INTEGER)')
            if not conn.execute('SELECT 1 FROM event_refill_v1 WHERE uid=?', (uid,)).fetchone():
                for i in sorted(self.event_refill_items()):
                    add_item(conn, uid, 13, i, EVENT_REFILL_NUM)
                conn.execute('INSERT INTO event_refill_v1 VALUES(?,?)', (uid, int(time.time())))
        if uid in SPECIAL_REFILL_UIDS:
            for table, items in MATERIAL_REFILLS:
                self._batch(conn, uid, table, items)

    def event_refill_items(self):
        ids = set()
        for ev in self.t['EventTypeBaseVo']:
            eid = int(ev['Id'])
            if int(ev['DropType'] or 0) and ev['Item'] and (EVENT_REFILL_IDS[0] <= eid <= EVENT_REFILL_IDS[1] or eid in EVENT_REFILL_EXTRA):
                for part in str(ev['Item']).split(';'):
                    k, i, _ = (int(x) for x in part.split(','))
                    if k == 13:
                        ids.add(i)
        return ids

    # —— 赛季 ——
    def grant_season_orbs(self, conn, uid):
        """排位每赢 10 局给 1 颗赛季宝珠（累计胜场，只补差额）。"""
        try:
            wins = conn.execute('SELECT COUNT(*) FROM local_rewards WHERE uid=? AND mode=11 AND win=1', (uid,)).fetchone()[0]
        except Exception:   # 还没有对局奖励表
            return
        conn.execute('CREATE TABLE IF NOT EXISTS season_orb_grants(uid TEXT PRIMARY KEY, num INTEGER)')
        got = (conn.execute('SELECT num FROM season_orb_grants WHERE uid=?', (uid,)).fetchone() or [0])[0]
        due = wins // RANK_SKIN_WINS - got
        if due > 0:
            add_item(conn, uid, SEASON_ORB[0], SEASON_ORB[1], due)
            conn.execute('INSERT INTO season_orb_grants VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET num=num+?', (uid, due, due))

    def grant_rank_skins(self, conn, uid):
        """排位限定皮肤：按排位胜场自动发放（Type=4，按 Id 顺序）。当前未接入登录流程。"""
        try:
            wins = conn.execute('SELECT COUNT(*) FROM local_rewards WHERE uid=? AND mode=11 AND win=1', (uid,)).fetchone()[0]
        except Exception:
            return
        skins = sorted(k['Id'] for k in self.t['SkinIdBaseVo'] if k['Type'] == 4 and k['IsOpen'] == 1)
        conn.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')
        for sid in skins[:wins // RANK_SKIN_WINS]:
            conn.execute('INSERT OR IGNORE INTO owned_skins VALUES(?,?)', (uid, sid))

    def settle_last_season(self, conn, uid):
        """上个赛季结束奖励：登录时检查，没结算过就按上赛季最终段位发邮件。"""
        season, start, _ = self.season_bounds()
        last = self.season_info(uid, start - 3600)
        conn.execute('CREATE TABLE IF NOT EXISTS season_settled(uid TEXT, season INTEGER, PRIMARY KEY(uid, season))')
        if conn.execute('SELECT 1 FROM season_settled WHERE uid=? AND season=?', (uid, last['season'])).fetchone():
            return
        conn.execute('INSERT INTO season_settled VALUES(?,?)', (uid, last['season']))
        items = []
        if last['score'] >= SEASON_AWARD1['score'] and last['wins'] >= SEASON_AWARD1['wins']:
            items.append((SEASON_ORB[0], SEASON_ORB[1], 1))
        if last['rank_id'] >= SEASON_AWARD2_RANK:
            items.append((2, 0, last['gold']))
        if not items:
            return
        rank = next((r['Name'] for r in self.t['MAxRankBaseVo'] if r['Id'] == last['rank_id']), '')
        day = lambda ts: time.strftime('%Y-%m-%d', time.localtime(ts))
        lines = [f'第 {last["season"]} 赛季已结束（{day(last["start"])} 至 {day(last["end"])}）。',
                 f'最终段位：{rank}（段位分 {last["score"]}）；排位 {last["fights"]} 场，胜 {last["wins"]} 场，最高连胜 {last["streak"]}。',
                 '本赛季奖励：']
        if any(k == SEASON_ORB[0] for k, _, _ in items):
            lines.append(f'· 赛季奖励（黄金V以上且胜 {SEASON_AWARD1["wins"]} 场）：赛季宝珠×1，可在赛季商店兑换排位皮肤')
        if any(k == 2 for k, _, _ in items):
            lines.append(f'· 升段奖励（钻石V以上）：金币×{last["gold"]}')
        lines.append('新赛季段位已从黄铜V重新开始。请点击“领取”收取附件。')
        send_mail(conn, uid, '系统', f'第{last["season"]}赛季结算奖励', '\n'.join(lines), items)

    # —— 登录时的整套补发（顺序与拆分前一致）——
    def on_login(self, conn, uid):
        if uid.startswith('local-bot-'):
            return
        self.grant_starter(conn, uid)
        self.grant_season_orbs(conn, uid)
        self.settle_last_season(conn, uid)
        conn.execute('CREATE TABLE IF NOT EXISTS growth_accounts(uid TEXT PRIMARY KEY, time INTEGER)')
        if conn.execute('SELECT 1 FROM growth_accounts WHERE uid=?', (uid,)).fetchone():
            if not conn.execute("SELECT name FROM sqlite_master WHERE name='growth_start_v2'").fetchone() or \
                    not conn.execute('SELECT 1 FROM growth_start_v2 WHERE uid=?', (uid,)).fetchone():
                conn.execute('UPDATE accounts SET diamonds=diamonds+? WHERE uid=?', (LEGACY_GROWTH_DIAMONDS, uid))
            self.grant_growth_start(conn, uid)
            self.special_refill(conn, uid)
        self.grant_starter_v2(conn, uid)
        self._batch(conn, uid, 'starter_grants_v3', STARTER_V3_ITEMS)
        self._batch(conn, uid, 'starter_grants_v4', STARTER_V4_ITEMS)
        self._batch(conn, uid, 'starter_grants_v5', STARTER_V5_ITEMS)
        self._batch(conn, uid, 'starter_grants_v6', STARTER_V6_ITEMS)
