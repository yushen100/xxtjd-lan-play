"""结算业务：只依赖存储、奖励规则和明确回调，不依赖连接或房间适配器。"""
import importlib.util
import json
import sqlite3
from pathlib import Path
storage_spec = importlib.util.spec_from_file_location('storage', Path(__file__).with_name('1011_storage_v1.py'))
storage = importlib.util.module_from_spec(storage_spec)
storage_spec.loader.exec_module(storage)
REWARDS_spec = importlib.util.spec_from_file_location('REWARDS', Path(__file__).with_name('1010_rewards_v1.py'))
REWARDS = importlib.util.module_from_spec(REWARDS_spec)
REWARDS_spec.loader.exec_module(REWARDS)

REWARD_GAMES_PER_HOUR = 20

class SettlementService:
    def __init__(self, db, tables, hero, seat, play_mode, reward_rules=None):
        self.db, self.tables = db, tables
        self.store = storage.SQLiteStore(db)
        self.hero, self.seat, self.play_mode = hero, seat, play_mode
        self.reward_rules = reward_rules if reward_rules is not None else REWARDS
        self.crosscheck = self.features2 = self.passport = None
        self.mailer = None   # (conn, uid, sender, title, content, items)；未注入时用本模块的默认写法
        self.last_reward = None

    def configure(self, *, crosscheck=None, features2=None, passport=None, mailer=None):
        # 可选业务明确注入，测试替换依赖后仍使用同一事务。
        self.crosscheck, self.features2, self.passport = crosscheck, features2, passport
        if mailer is not None:
            self.mailer = mailer

    def settle(self, room, uid, req):
        """按原生表结算（每人每局一次）：
        - 一般模式：FightAwardBaseVo[等级, 名次] 银币；当天首胜用 FirSilver。银币周上限 VariableBaseVo.SilverUp。
        - 保卫之战：DefAwardBaseVo[单人1/组队2, 坚持波数] 银币+出战英雄碎片+活跃道具，每日 DefAwardSolo/Dou 次。
        - 军功：胜 +20、负 -10（不低于0）——官网未公开公式，本地规则。"""
        import datetime
        day = datetime.date.today().isoformat()
        week = '%d-W%d' % datetime.date.today().isocalendar()[:2]
        var = {v['Name']: v for v in self.tables['VariableBaseVo']}
        with self.store.connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS local_rewards(room_id INTEGER, uid TEXT, silver INTEGER, rank_score INTEGER, detail TEXT, day TEXT, week TEXT, mode INTEGER, win INTEGER, PRIMARY KEY(room_id, uid));""")
            done = conn.execute('SELECT silver, rank_score, detail FROM local_rewards WHERE room_id=? AND uid=?', (room.room_id, uid)).fetchone()
            if done:
                import json as _j
                return {'silver': done[0], 'rank_score': done[1], 'items': [tuple(x) for x in _j.loads(done[2])]}
            # 防刷：多人交叉核对（按同局所有真人战报的共识修正本人上报）
            if getattr(self, 'crosscheck', None):
                try:
                    self.crosscheck.before_settle(conn, room.room_id, uid, req, [m for m in room.members if not m.startswith("local-bot-")])
                except Exception as exc:
                    self.crosscheck.log(room=room.room_id, uid=uid, stage="核对失败，回滚", error=repr(exc))
                    raise
            # 防刷：每队只认一个 MVP（同队第一个上报的为准），其余按普通表现结算
            if getattr(req, 'isMvp', False):
                conn.execute('CREATE TABLE IF NOT EXISTS room_mvps(room_id INTEGER, team INTEGER, uid TEXT, PRIMARY KEY(room_id, team))')
                team = self.seat(room, uid)[0]
                conn.execute('INSERT OR IGNORE INTO room_mvps VALUES(?,?,?)', (room.room_id, team, uid))
                owner = conn.execute('SELECT uid FROM room_mvps WHERE room_id=? AND team=?', (room.room_id, team)).fetchone()[0]
                if owner != uid:
                    req.isMvp = False
            mode = self.play_mode(room)
            hero = self.hero(uid)[0]
            silver, items = 0, []
            if True:
                # 战绩：本地规则——匹配、开房间、PVE（含保卫之战）全部入库（原版房间与PVE不计）。
                import time as _t
                conn.execute("""CREATE TABLE IF NOT EXISTS battle_records(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, room_id INTEGER,
                    battle_type INTEGER, hero INTEGER, skin INTEGER, kill INTEGER, dead INTEGER, assist INTEGER, win INTEGER, mvp INTEGER,
                    multi INTEGER, triple INTEGER, quadra INTEGER, penta INTEGER, shuchu INTEGER, tuijin INTEGER, shengcun INTEGER,
                    zhiyuan INTEGER, unix INTEGER, record BLOB, UNIQUE(uid, room_id))""")
                conn.execute('INSERT OR IGNORE INTO battle_records(uid,room_id,battle_type,hero,skin,kill,dead,assist,win,mvp,multi,triple,quadra,penta,shuchu,tuijin,shengcun,zhiyuan,unix,record) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (uid, room.room_id, mode, hero, req.skinId or self.hero(uid)[1], req.killNum, req.beKilledNum, req.assistNum,
                              int(req.isWin), int(req.isMvp), req.multiKillNum, int(req.isTripleKill), int(req.isQuadraKill), int(req.isPentaKill),
                              req.shuchu, req.tuijin, req.shengcun, req.zhiyuan, int(_t.time()),
                              req.record.SerializeToString() if req.HasField('record') else b''))
            # 本地奖励规则见 1010_rewards_v1.py（按模式分组、表现分、输赢、英雄价格、成长系数）。
            hero_row = next((h for h in self.tables['HeroBaseVo'] if h['Id'] == hero), None)
            try:
                star, awake = conn.execute('SELECT star, awake FROM heroes WHERE uid=? AND hero_id=?', (uid, hero)).fetchone() or (0, 0)
            except sqlite3.OperationalError:
                star, awake = 0, 0
            tal_rows = self.tables.get('TalentBaseVo', [])
            max_lv = {}
            for r in tal_rows:
                max_lv[r['Tid']] = max(max_lv.get(r['Tid'], 1), r['Lv'])
            try:
                have = dict(conn.execute('SELECT tid, level FROM hero_talents WHERE uid=? AND hero_id=?', (uid, hero)).fetchall())
            except sqlite3.OperationalError:
                have = {}
            # 每个英雄只有 5 个天赋槽（每组选 1 个）：按“已点天赋 / 5 槽满级”算，而不是除以全部 20 个天赋
            groups = {r['Group'] for r in tal_rows} or {1}
            full = max(max_lv.values() or [2]) - 1
            best = sorted((min(lv, max_lv.get(t, lv)) - 1 for t, lv in have.items()), reverse=True)[:len(groups)]
            talent_ratio = min(1.0, sum(best) / (len(groups) * full)) if full > 0 else 1.0
            calc = self.reward_rules.compute(room.mode, mode, req, hero_row, star, awake, talent_ratio)
            # 防刷：每小时最多 20 局有奖励（超出只记战绩，货币/材料为 0）
            try:
                recent = conn.execute('SELECT COUNT(*) FROM battle_records WHERE uid=? AND unix>=?', (uid, int(_t.time()) - 3600)).fetchone()[0]
            except sqlite3.OperationalError:
                recent = 0
            disputed = bool(getattr(self, 'crosscheck', None) and self.crosscheck.is_disputed(conn, room.room_id))
            if recent > REWARD_GAMES_PER_HOUR or disputed:
                calc['reward'] = {k: 0 for k in calc['reward']}
                calc['capped'] = True
            reward = calc['reward']
            if getattr(self, 'crosscheck', None):
                try:
                    params = {'room_mode': room.mode, 'mode': mode, 'hero': hero, 'star': star, 'awake': awake,
                              'talent_ratio': talent_ratio, 'capped': bool(calc.get('capped'))}
                    self.crosscheck.save(conn, room.room_id, uid, req, params, dict(reward))
                    if not disputed:   # 争议局已整体扣回，不再逐项复查（避免重复邮件）
                        self.crosscheck.recheck_others(conn, room.room_id, uid, self.recompute_reward, self.deduct_reward, self.mail)
                except Exception as exc:
                    self.crosscheck.log(room=room.room_id, uid=uid, stage='核对出错，回滚', error=repr(exc))
                    raise
            silver = reward.pop('silver', 0)
            gold, diamonds = reward.pop('gold', 0), reward.pop('diamonds', 0)
            try:
                conn.execute('UPDATE accounts SET gold=gold+?, diamonds=diamonds+? WHERE uid=?', (gold, diamonds, uid))
                user = conn.execute('SELECT gold, diamonds FROM accounts WHERE uid=?', (uid,)).fetchone()
                items += [(2, 0, user[0]), (10, 0, user[1])]
            except sqlite3.OperationalError:
                pass  # 测试库无金币/钻石列
            conn.execute('CREATE TABLE IF NOT EXISTS native_items(uid TEXT NOT NULL, item_type INTEGER NOT NULL, item_id INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(uid,item_type,item_id))')
            for (kind, item), num in reward.items():
                if num <= 0:
                    continue
                conn.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count', (uid, kind, item, num))
                items.append((kind, item, conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid, kind, item)).fetchone()[0]))
            self.last_reward = calc
            delta = 0 if disputed else (20 if req.isWin else -10)   # 争议局不加不减军功
            try:
                have = conn.execute('SELECT rank_score FROM accounts WHERE uid=?', (uid,)).fetchone()
            except sqlite3.OperationalError:
                have = None  # 测试库无军功列
            delta = max(-(have[0] if have else 0), delta) if have else 0
            conn.execute('UPDATE accounts SET silver=silver+? WHERE uid=?', (silver, uid))
            if have:
                conn.execute('UPDATE accounts SET rank_score=rank_score+? WHERE uid=?', (delta, uid))
            import json as _j
            conn.execute('INSERT INTO local_rewards VALUES(?,?,?,?,?,?,?,?,?)', (room.room_id, uid, silver, delta, _j.dumps(items), day, week, 0 if disputed else room.mode, 0 if disputed else int(bool(req.isWin))))   # 争议局：mode=0、win=0，不计入荣耀之路/赛季段位/宝珠
            # 额外奖励与货币同事务提交，重复请求在前面的唯一记录检查处返回。
            if not calc.get('capped') and not disputed:
                box = self.features2.add_box(uid, bool(req.isWin), conn=conn, room_id=room.room_id) if getattr(self, 'features2', None) else None
                gain = self.passport.add_battle_exp(uid, conn=conn) if getattr(self, 'passport', None) else 0
                conn.execute('CREATE TABLE IF NOT EXISTS room_extras(room_id INTEGER, uid TEXT, box_id INTEGER, box_type INTEGER, exp INTEGER, PRIMARY KEY(room_id, uid))')
                conn.execute('INSERT INTO room_extras VALUES(?,?,?,?,?)', (room.room_id, uid, box[0] if box else 0, box[1] if box else 0, gain or 0))
            return {'silver': silver, 'rank_score': delta, 'items': items}


    def recompute_reward(self, p, req_like):
        if p.get('capped'):
            return {}
        hero_row = next((h for h in self.tables['HeroBaseVo'] if h['Id'] == p['hero']), None)
        return self.reward_rules.compute(p['room_mode'], p['mode'], req_like, hero_row, p['star'], p['awake'], p['talent_ratio'])['reward']


    def deduct_reward(self, conn, uid, diff):
        """扣除现有余额；已经花掉的部分明确记录，留给房主核对。"""
        conn.execute('CREATE TABLE IF NOT EXISTS reward_recovery_pending(uid TEXT, asset TEXT, amount INTEGER, PRIMARY KEY(uid,asset))')
        cols = {'silver': 'silver', 'gold': 'gold', 'diamonds': 'diamonds'}
        for k, v in diff.items():
            if v <= 0:
                continue
            if k in cols:
                current = conn.execute(f'SELECT {cols[k]} FROM accounts WHERE uid=?', (uid,)).fetchone()
                balance = current[0] if current else 0
                conn.execute(f'UPDATE accounts SET {cols[k]}=MAX(0,{cols[k]}-?) WHERE uid=?', (v, uid))
            elif isinstance(k, tuple):
                current = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid, *k)).fetchone()
                balance = current[0] if current else 0
                conn.execute('UPDATE native_items SET count=MAX(0,count-?) WHERE uid=? AND item_type=? AND item_id=?', (v, uid, *k))
            else:
                balance = 0
            if v > balance:
                conn.execute('INSERT INTO reward_recovery_pending VALUES(?,?,?) ON CONFLICT(uid,asset) DO UPDATE SET amount=amount+excluded.amount', (uid, json.dumps(k), v-balance))


    def revoke_room_reward(self, conn, room_id, uid, reward):
        """争议局：扣回已发的货币/材料，并撤销本局军功变化。"""
        self.deduct_reward(conn, uid, reward)
        got = conn.execute('SELECT rank_score FROM local_rewards WHERE room_id=? AND uid=?', (room_id, uid)).fetchone()
        if got and got[0]:
            try:
                conn.execute('UPDATE accounts SET rank_score=MAX(0, rank_score-?) WHERE uid=?', (got[0], uid))
            except sqlite3.OperationalError:
                pass
        conn.execute('UPDATE local_rewards SET silver=0, rank_score=0, mode=0, win=0 WHERE room_id=? AND uid=?', (room_id, uid))   # 不再计入荣耀之路/赛季/宝珠
        # 扣回本局发的战斗宝箱（还在列表里就删；已放进槽位就清掉同类型的一个槽位）和通行证经验
        try:
            ex = conn.execute('SELECT box_id, box_type, exp FROM room_extras WHERE room_id=? AND uid=?', (room_id, uid)).fetchone()
        except sqlite3.OperationalError:
            ex = None
        if ex:
            box_id, box_type, exp = ex
            if box_id:
                if not conn.execute('DELETE FROM battle_boxes WHERE id=? AND uid=?', (box_id, uid)).rowcount:
                    origin = conn.execute('SELECT slot,gains FROM box_origins WHERE box_id=? AND uid=? AND room_id=?', (box_id, uid, room_id)).fetchone() if conn.execute("SELECT 1 FROM sqlite_master WHERE name='box_origins'").fetchone() else None
                    if origin and origin[0]:
                        conn.execute('DELETE FROM box_slots WHERE uid=? AND slot=?', (uid, origin[0]))
                    elif origin and origin[1]:
                        for item in json.loads(origin[1]):
                            kind, item_id = item['item_type'], item['item_id']
                            key = {1:'silver',2:'gold',10:'diamonds'}.get(kind, (kind,item_id))
                            self.deduct_reward(conn, uid, {key:item['added']})
                    else:
                        self.deduct_reward(conn, uid, {f'待核对宝箱:{box_id}':1})
            if exp:
                conn.execute('UPDATE passport SET exp=MAX(0, exp-?), week_exp=MAX(0, week_exp-?) WHERE uid=?', (exp, exp, uid))
            conn.execute('DELETE FROM room_extras WHERE room_id=? AND uid=?', (room_id, uid))


    def mail(self, conn, uid, title, content):
        if self.mailer is not None:   # 统一走服务端的发邮件实现（与登录补发、赛季结算同一处）
            return self.mailer(conn, uid, '系统', title, content, [])
        conn.execute("""CREATE TABLE IF NOT EXISTS mails(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, sender TEXT, title TEXT,
            content TEXT, ctime INTEGER, is_read INTEGER DEFAULT 0, items TEXT DEFAULT '[]', claimed INTEGER DEFAULT 0)""")
        import time as _tm
        conn.execute('INSERT INTO mails(uid,sender,title,content,ctime) VALUES(?,?,?,?,?)', (uid, '系统', title, content, int(_tm.time())))


