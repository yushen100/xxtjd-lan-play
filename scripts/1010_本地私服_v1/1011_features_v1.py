"""左侧功能（第一批）：专属抽英雄（3244/3245）、专属英雄兑换（3360）、日常任务（3061/3062/3063）。
规则来源：
- 抽英雄 = 专属抽奖：奖池 ExclusiveLotteryBaseVo（Chance 加权）；消耗宝石 13:12，原价单抽 VariableBaseVo.HeroLuckCost=10、十连 HeroLuckCost10=85；
  幸运值每抽 +10，达到 ExclusiveLuckPoint=300 时必出专属徽章 13:43 并清零。
  本地折扣（用户确认"免费或打折"）：每天第一次单抽免费，其余 5 折。
- 专属兑换：ExclusiveExchangeBaseVo，消耗专属徽章 ExchangeCost 个，换指定英雄。
- 日常任务：DailyTaskBaseVo；进度按服务器记录计算（local_rewards/battle_records/登录时间），奖励 TaskAward(类型)/TypeId/Num，活跃度 Acti；
  周活跃宝箱 WeekAwardBaseVo。暂不支持的任务类型（军队红包/捐献、花金币、发言、能源币消耗）进度恒为 0。
"""
import datetime
import json
import random
import time
from contextlib import nullcontext
import importlib.util
from pathlib import Path

_store_spec = importlib.util.spec_from_file_location('features_store', Path(__file__).with_name('1011_features_store_v1.py'))
store = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(store)   # 活动/兑换/宝箱/星卡等的 SQL 都在这里

DISCOUNT = 0.5          # 抽奖折扣
DAILY_FREE_DRAWS = 1    # 每天免费单抽次数


def today():
    return datetime.date.today().isoformat()


def this_week():
    return '%d-W%02d' % datetime.date.today().isocalendar()[:2]


class Features:
    def __init__(self, economy, tables):
        self.e = economy
        self.t = tables
        var = {v['Name']: v for v in tables['VariableBaseVo']}
        self.cost1 = var['HeroLuckCost']['intValue']
        self.cost10 = var['HeroLuckCost10']['intValue']
        self.luck_full = var['ExclusiveLuckPoint']['intValue']
        self.pool = tables['ExclusiveLotteryBaseVo']
        self.exchange = {r['Id']: r for r in tables['ExclusiveExchangeBaseVo']}
        self.daily = {r['Id']: r for r in tables['DailyTaskBaseVo']}
        self.week_box = {r['Id']: r for r in tables['WeekAwardBaseVo']}

    def _tables(self, conn):
        store.ensure_f1_tables(conn)

    # —— 登录：记录当天首次登录时间（在线时长任务用）——
    def on_login(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            store.note_login(conn, uid, today(), int(time.time()))

    # —— 专属抽奖 ——
    def _state(self, conn, uid):
        self._tables(conn)
        s = store.lottery_state(conn, uid)
        if s['day'] != today():
            s['day'], s['draws'] = today(), 0
        return s

    def lottery_info(self, uid):
        with self.e.connection() as conn:
            s = self._state(conn, uid)
            return {'list': [(r['Id'], r['ItemType'], r['ItemId'], r['ItemNum'], r['LotteryType']) for r in self.pool],
                    'value': s['luck'], 'draws': s['draws']}

    def lottery_draw(self, uid, lottery_type, multi):
        count = 10 if multi and multi >= 10 else 1
        rows = [r for r in self.pool if r['LotteryType'] == lottery_type]
        if not rows:
            raise ValueError('LOCAL_LOTTERY_UNKNOWN')
        special = [r for r in rows if r['IsSpecial']]
        normal = [r for r in rows if not r['IsSpecial']]
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            s = self._state(conn, uid)
            if count == 1 and s['draws'] < DAILY_FREE_DRAWS:
                price = 0
            else:
                price = int(round((self.cost10 if count == 10 else self.cost1) * DISCOUNT))
            keys = self.e._pay(conn, uid, [(13, 12, price)]) if price else []
            ids, got = [], []
            luck = s['luck']
            for _ in range(count):
                luck += 10
                if special and luck >= self.luck_full:
                    pick, luck = special[0], 0
                else:
                    pick = random.choices(rows, weights=[r['Chance'] for r in rows])[0]
                    if pick['IsSpecial']:
                        luck = 0
                ids.append(pick['Id'])
                got.append(self.e._grant(conn, uid, pick['ItemType'], pick['ItemId'], pick['ItemNum']))
            store.save_lottery_state(conn, uid, luck, s['day'], s['draws'] + count)
            return {'ids': ids, 'added': got, 'items': self.e._snapshot(conn, uid, keys), 'value': luck}

    def exclusive_exchange(self, uid, ex_id):
        row = self.exchange.get(ex_id)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            keys = self.e._pay(conn, uid, [(13, 43, row['ExchangeCost'])])
            got = self.e._grant(conn, uid, row['ItemType'], row['ItemId'], row['ItemNum'])
            return {'added': [got], 'items': self.e._snapshot(conn, uid, keys)}

    # —— 日常任务 ——
    def _progress(self, conn, uid):
        day = today()
        start = int(time.mktime(datetime.date.today().timetuple()))
        p = {10: 1}
        try:
            n, w, rw = store.day_games(conn, uid, day)
        except Exception:
            n = w = rw = 0
        p[1], p[2], p[3], p[4] = n, n, w, rw   # 组队对局暂按全部对局计（本地规则）
        try:
            p[5] = store.kills_since(conn, uid, start)
        except Exception:
            p[5] = 0
        first = store.first_login(conn, uid, day)
        p[11] = int((time.time() - first) // 60) if first is not None else 0
        try:
            p[13] = store.day_mode_wins(conn, uid, day, 23)
        except Exception:
            p[13] = 0
        return p

    def _acti(self, conn, uid):
        today_ac = store.acti_on_day(conn, uid, today())
        week_ac = store.acti_in_week(conn, uid, this_week())
        return today_ac, week_ac

    def daily_list(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            p = self._progress(conn, uid)
            claimed = store.daily_claimed_ids(conn, uid, today())
            boxes = store.week_boxes_claimed(conn, uid, this_week())
            _, week_ac = self._acti(conn, uid)
            tasks = [(tid, min(p.get(r['TaskType'], 0), r['ConditionNum']), tid in claimed) for tid, r in self.daily.items()]
            return {'tasks': tasks, 'week_ac': week_ac, 'boxes': [(bid, bid in boxes) for bid in self.week_box]}

    def _give(self, conn, uid, kind, item, num):
        if kind in (1, 2, 10):
            return {'item_type': kind, 'item_id': 0, 'added': num, 'count': store.add_money(conn, uid, kind, num)}
        return self.e._grant(conn, uid, kind, item, num)

    def daily_reward(self, uid, task_id):
        row = self.daily.get(task_id)
        if row is None:
            raise ValueError('LOCAL_TASK_UNKNOWN')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            if store.daily_claimed(conn, uid, today(), task_id):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            if self._progress(conn, uid).get(row['TaskType'], 0) < row['ConditionNum']:
                raise ValueError('LOCAL_TASK_NOT_DONE')
            got = self._give(conn, uid, row['TaskAward'], row['TypeId'], row['Num'])
            store.claim_daily(conn, uid, today(), this_week(), task_id, row['Acti'])
            today_ac, _ = self._acti(conn, uid)
            return {'added': [got], 'today_ac': today_ac}

    def weekly_box(self, uid, box_id):
        row = self.week_box.get(box_id)
        if row is None:
            raise ValueError('LOCAL_TASK_UNKNOWN')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            if store.week_box_claimed(conn, uid, this_week(), box_id):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            if self._acti(conn, uid)[1] < row['NeedActi']:
                raise ValueError('LOCAL_TASK_NOT_DONE')
            got = [self._give(conn, uid, row['BoxAward1'], 0, row['Num1']), self._give(conn, uid, row['BoxAward2'], 0, row['Num2'])]
            if random.randint(1, 100) <= row['GoldPr']:
                got.append(self._give(conn, uid, 2, 0, random.randint(row['GoldMin'], row['GoldMax'])))
            store.claim_week_box(conn, uid, this_week(), box_id)
            return {'added': got}


# ===================== 第二批：碎片融合、快捷兑换、玉石抽结晶、战斗宝箱、星卡(占星)、10钻升星 =====================
import math

FUSE_SILVER_FREE = True     # 普通融合免银币（用户确认"免费或打折"）
FUSE_TICKET_DISCOUNT = 0.5  # 特殊融合券 5 折
CRYSTAL_DISCOUNT = 0.5      # 玉石抽结晶 5 折
CRYSTAL_SINGLE, CRYSTAL_TEN = 10, 88   # 原价（界面显示十连 88 玉石；单抽价未确认，按 10）
ASTRO_DISCOUNT = 1.0        # 星卡兑换恢复原价（用户确认：原价 800 + 每日 0 点星屑清零）
ASTRO_DUST = (13, 202)      # 星屑
ASTRO_DUP_REFUND = 300      # 兑换已拥有英雄：星屑照扣，改返还 300 钻（约限定英雄价 30%）
ASTRO_BIG_RATE = 0.30
ASTRO_NUM_MULT = 5  # 本地规则：星卡普通格数量 ×5（大奖格不变）
BOX_DIAMOND_DISCOUNT = 0.5  # 宝箱钻石提前开启：按客户端显示价的一半扣       # 星卡翻牌大奖概率（原表约 0.05%）
ASTRO_FREE_PER_DAY = 3      # 星卡每天免费翻牌次数
BOX_TIME_FACTOR = 0.5       # 战斗宝箱开启时间减半
BOX_MAX = 5   # 与客户端“x/5”和 TreasureLimit=5 一致                 # 宝箱栏上限


def box_diamond_cost(sec):
    """与客户端 BattleBoxTreasureItem.redrawCountdown 一致的钻石价。"""
    if sec <= 300:
        return 1
    if sec <= 3600:
        return math.ceil(sec / 300 + 1)
    if sec <= 28800:
        return math.ceil((sec - 3600) * 0.003015873015873016 + 12)
    return math.ceil((sec - 28800) * 0.0027256944444444446 + 88)



def _bump(conn, uid, key):
    store.bump(conn, uid, key)


class Features2:
    def __init__(self, features):
        self.f = features
        self.e = features.e
        t = features.t
        self.t = t
        self.fuse_rows = {r['Id']: r for r in t['ChipFuseBaseVo']}
        self.drops = {}
        for r in t['DropBaseVo']:
            self.drops.setdefault(r['Type'], []).append(r)
        self.easy = {r['Id']: r for r in t['EasyConvertBaseVo']}
        self.gifts = {}
        for g in t['GiftBaseVo']:
            self.gifts.setdefault(g['Id'], []).append(g)
        self.crystal = t['CrystalBaseVo']
        var = {v['Name']: v for v in t['VariableBaseVo']}
        self.crystal_cd = var['Crystal']['intValue'] * 3600
        self.astro = {r['Id']: r for r in t['AstrologyBaseVo']}

    def _tables(self, conn):
        store.ensure_f2_tables(conn)

    def roll_drop(self, drop_id, only_special=False):
        """DropBaseVo：同一 Type 下按 ChanceGroup 分组，每组按 Chance 抽一条，数量在 Min..Max 之间。"""
        rows = self.drops.get(drop_id, [])
        if only_special:
            rows = [r for r in rows if r['ItemType'] in (5, 6)] or rows
        out = []
        groups = {}
        for r in rows:
            groups.setdefault(r['ChanceGroup'], []).append(r)
        for grp in groups.values():
            pick = random.choices(grp, weights=[max(r['Chance'], 0) or 1 for r in grp])[0]
            out.append((pick['ItemType'], pick['ItemId'], random.randint(pick['ItemNumMin'], max(pick['ItemNumMin'], pick['ItemNumMax'])), pick['Id']))
        return out

    def _give_all(self, conn, uid, items):
        return [self.f._give(conn, uid, k, i, n) for k, i, n in items if n]

    # —— 碎片融合 ——
    def fuse_info(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            have = store.fuse_luck_map(conn, uid)
            return [(r['ChipId'], have.get(fid, 0)) for fid, r in self.fuse_rows.items() if r['Luck']]

    def fuse(self, uid, fuse_id, times):
        row = self.fuse_rows.get(fuse_id)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        times = max(1, min(times or 1, 10))
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            costs = [(12, row['ChipId'], row['CostNum'] * times)]
            if row['CostType'] == 1:
                if not FUSE_SILVER_FREE:
                    costs.append((1, 0, row['CostNum1'] * times))
            else:
                costs.append((row['CostType'], row['CostItemId'], math.ceil(row['CostNum1'] * FUSE_TICKET_DISCOUNT) * times))
            keys = self.e._pay(conn, uid, costs)
            luck = store.fuse_luck(conn, uid, fuse_id)
            got = []
            for _ in range(times):
                special = False
                if row['Luck']:
                    luck += row['Luck']
                    if luck >= row['LuckSum']:
                        special, luck = True, 0
                for k, i, n, _ in self.roll_drop(row['DropId'], only_special=special):
                    if k in (5, 6) and row['Luck']:
                        luck = 0
                    got.append((k, i, n))
            if row['Luck']:
                store.set_fuse_luck(conn, uid, fuse_id, luck)
            added = self._give_all(conn, uid, got)
            return {'added': added, 'items': self.e._snapshot(conn, uid, keys), 'special': (row['ChipId'], luck)}

    # —— 快捷兑换 EasyConvert：花钻石得 GiftId 礼包 + 额外道具 ——
    def easy_convert(self, uid, cid, num):
        row = self.easy.get(cid)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        num = max(1, num or 1)
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            keys = self.e._pay(conn, uid, [(row['CostType'], row['CostItemId'], row['CostNum1'] * num)])
            items = [(g['ItemType'], g['ItemId'], g['ItemNum'] * num) for g in self.gifts.get(row['GiftId'], [])]
            items.append((row['ItemType'], row['ItemId'], num))
            return {'added': self._give_all(conn, uid, items), 'items': self.e._snapshot(conn, uid, keys)}

    # —— 玉石抽英雄结晶 CrystalDraw ——
    def crystal_draw(self, uid, multi):
        count = 10 if multi else 1
        now = int(time.time())
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            free_at = store.crystal_free_unix(conn, uid)
            keys = []
            if count == 1 and now >= free_at:
                free_at = now + self.crystal_cd
            else:
                price = math.ceil((CRYSTAL_TEN if count == 10 else CRYSTAL_SINGLE) * CRYSTAL_DISCOUNT)
                keys = self.e._pay(conn, uid, [(13, 20, price)])
            picks = random.choices(self.crystal, weights=[r['Pr'] for r in self.crystal], k=count)
            added = self._give_all(conn, uid, [(r['Type'], r['TypeId'], r['Num']) for r in picks])
            store.set_crystal_free_unix(conn, uid, free_at)
            return {'added': added, 'items': self.e._snapshot(conn, uid, keys), 'free_unix': free_at}

    # —— 星卡（占星）——
    def astro_info(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            s = store.astro_state(conn, uid)
            self._astro_new_day(conn, uid, s)
            nxt = int(time.mktime((datetime.date.today() + datetime.timedelta(days=1)).timetuple()))
            owned = store.owned_hero_ids(conn, uid)
            return {'client_info': s['client_info'], 'free': s['free'], 'next': nxt, 'ids': self._astro_pick(owned)}

    def _astro_pick(self, owned):
        """兑换区 3 个：优先未拥有的英雄（同英雄去重），每天按日期轮换顺序；不足 3 个再用已拥有的补。"""
        rng = random.Random(today())
        seen, fresh, rest = set(), [], []
        rows = list(self.astro.values()); rng.shuffle(rows)
        for r in rows:
            if r['ItemId'] in seen:
                continue
            seen.add(r['ItemId'])
            (rest if r['ItemType'] == 5 and r['ItemId'] in owned else fresh).append(r['Id'])
        return (fresh + rest)[:3]

    def _astro_new_day(self, conn, uid, s):
        """跨天：重置免费次数，星屑清零（与客户端“每日0点重置”一致）。"""
        if s['day'] != today():
            s['free'], s['day'] = ASTRO_FREE_PER_DAY, today()
            store.astro_reset_day(conn, uid, ASTRO_FREE_PER_DAY, s['day'], ASTRO_DUST)

    def astro_set(self, uid, info):
        with self.e.connection() as conn:
            self._tables(conn)
            store.astro_set_info(conn, uid, info or '')
        return info or ''

    ASTRO_DROP = 20000   # 星卡翻牌奖池：DropBaseVo Type=20000（12 格，含 1 个星屑大奖）；客户端按 GiftId 读同一组画格子

    def astro_cells(self):
        return [(r['Id'], r['ItemType'], r['ItemId'], r['ItemNumMin'] * (1 if r['IsSpecial'] else ASTRO_NUM_MULT)) for r in self.drops.get(self.ASTRO_DROP, [])]

    def astro_award(self, uid):
        """占星翻牌一次：每天 1 次免费，否则消耗 1 个晶石 13:11（本地规则）；按 Chance 从 12 格里抽一格发奖。"""
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            s = store.astro_state(conn, uid)
            self._astro_new_day(conn, uid, s)
            keys = []
            if s['free'] > 0:
                store.astro_use_free(conn, uid)
            else:
                keys = self.e._pay(conn, uid, [(13, 11, 1)])
            rows = self.drops.get(self.ASTRO_DROP, [])
            total = sum(r['Chance'] for r in rows if not r['IsSpecial']) or 1
            # 本地规则（用户要求提高概率）：大奖格固定 ASTRO_BIG_RATE，其余格按原表比例分剩下的概率
            weights = [ASTRO_BIG_RATE if r['IsSpecial'] else (1 - ASTRO_BIG_RATE) * r['Chance'] / total for r in rows]
            pick = random.choices(rows, weights=weights)[0]
            n = random.randint(pick['ItemNumMin'], max(pick['ItemNumMin'], pick['ItemNumMax'])) * (1 if pick['IsSpecial'] else ASTRO_NUM_MULT)
            added = self._give_all(conn, uid, [(pick['ItemType'], pick['ItemId'], n)])
            _bump(conn, uid, 'astro')
            return {'added': added, 'items': self.e._snapshot(conn, uid, keys)}

    def astro_exchange(self, uid, aid):
        row = self.astro.get(aid)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            self._astro_new_day(conn, uid, store.astro_state(conn, uid))
            keys = self.e._pay(conn, uid, [(row['CostType'], row['Num'], math.ceil(row['CostNum'] * ASTRO_DISCOUNT))])
            owned = row['ItemType'] == 5 and store.owns_hero(conn, uid, row['ItemId'])
            if owned:   # 老号已拥有：返还钻石
                store.add_diamonds(conn, uid, ASTRO_DUP_REFUND)
                keys = list(keys) + [(10, 0)]
                added = []
            else:
                added = self._give_all(conn, uid, [(row['ItemType'], row['ItemId'], row['ItemNum'])])
            return {'added': added, 'items': self.e._snapshot(conn, uid, keys)}


    # —— 10钻升星（一元购 OnePay）：每天 1 次。原价 10 钻，本地免费；奖励取 VariableBaseVo.OneYuan，两档用 | 分隔 ——
    def onepay_info(self, uid):
        with self.e.connection() as conn:
            return 0 if store.onepay_done(conn, uid, today()) else 1

    def onepay_buy(self, uid, index):
        var = {v['Name']: v for v in self.t['VariableBaseVo']}
        options = var['OneYuan']['stringValue'].split('|')
        if not 0 <= index < len(options):
            index = 0
        items = [tuple(int(x) for x in part.split(',')) for part in options[index].split(';') if part]
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if store.onepay_done(conn, uid, today()):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            added = self._give_all(conn, uid, items)
            store.onepay_mark(conn, uid, today())
            return {'added': added}


    # —— 战斗宝箱：每局结算得一个宝箱（胜按 ConditionType=1 的概率，负按 0），放进 4 个槽位计时，到时间领取 GiftId 礼包 ——
    def roll_gift_groups(self, gift_id):
        """GiftBaseVo 按 PrType 分组：组内多条按 Pr 加权抽一条；只有一条且 Pr<100 时按 Pr% 概率给。"""
        groups = {}
        for g in self.gifts.get(gift_id, []):
            groups.setdefault(g['PrType'], []).append(g)
        out = []
        for rows in groups.values():
            if len(rows) == 1:
                if random.randint(1, 100) <= rows[0]['Pr']:
                    out.append((rows[0]['ItemType'], rows[0]['ItemId'], rows[0]['ItemNum']))
            else:
                g = random.choices(rows, weights=[max(r['Pr'], 1) for r in rows])[0]
                out.append((g['ItemType'], g['ItemId'], g['ItemNum']))
        return out

    def _box_tables(self, conn):
        store.ensure_box_tables(conn)

    def add_box(self, uid, win, conn=None, room_id=None):
        rows = [r for r in self.t['BattleTreasureBaseVo'] if r['ConditionType'] == (1 if win else 0)]
        if not rows:
            return
        pick = random.choices(rows, weights=[r['Pr'] for r in rows])[0]
        with (self.e.connection() if conn is None else nullcontext(conn)) as conn:
            self._box_tables(conn)
            if store.box_count(conn, uid) < BOX_MAX:
                return store.add_box(conn, uid, pick['Id'], room_id), pick['Id']
        return None

    def box_info(self, uid):
        with self.e.connection() as conn:
            self._box_tables(conn)
            boxes = store.boxes(conn, uid)
            slots = store.box_slots(conn, uid)
            p = self.f._progress(conn, uid)
            # 客户端显示“独自战斗 (3-次数)/3 场”；本地每局都给箱，固定发 2 → 显示 1/3（再打 1 场得箱）
            return {'times': [(0, 2), (1, p.get(3, 0))], 'boxes': boxes,
                    'slots': [(sid, *slots.get(sid, (0, 0))) for sid in range(1, 5)]}

    def box_into_slot(self, uid, box_id):
        row_by_id = {r['Id']: r for r in self.t['BattleTreasureBaseVo']}
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._box_tables(conn)
            kind = store.box_type(conn, uid, box_id)
            if kind is None:
                raise ValueError('LOCAL_BOX_UNKNOWN')
            used = store.busy_slots(conn, uid)
            free = [s for s in range(1, 5) if s not in used]
            if not free:
                raise ValueError('LOCAL_BOX_SLOT_FULL')
            wait = int(row_by_id[kind]['Time'] * 60 * BOX_TIME_FACTOR)
            store.put_box_in_slot(conn, uid, box_id, free[0], kind, int(time.time()) + wait)

    def box_award(self, uid, slot):
        row_by_id = {r['Id']: r for r in self.t['BattleTreasureBaseVo']}
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._box_tables(conn)
            s = store.slot_box(conn, uid, slot)
            if not s or not s[0]:
                raise ValueError('LOCAL_BOX_UNKNOWN')
            left = s[1] - time.time()
            keys = []
            if left > 0:  # 未到时间：按客户端公式算钻石价，本地打五折后扣钻提前开启
                keys = self.e._pay(conn, uid, [(10, 0, max(1, math.ceil(box_diamond_cost(left) * BOX_DIAMOND_DISCOUNT)))])
            _bump(conn, uid, 'box_open')
            items = self.roll_gift_groups(row_by_id[s[0]]['GiftId'])
            added = self._give_all(conn, uid, items)
            store.finish_slot(conn, uid, slot, json.dumps(added))
            return {'added': added, 'items': self.e._snapshot(conn, uid, keys)}

    def box_delete(self, uid, box_id):
        with self.e.connection() as conn:
            self._box_tables(conn)
            store.delete_box(conn, uid, box_id)


# —— 荣耀之路 + 每日签到（第二批）——
GLORY_WINS_PER_TIER = 3      # 用户规则：排位每赢 3 局解锁一档
GLORY_DIAMONDS = 300         # 每档额外 +300 钻
GLORY_BASE_SCORE = 1000
GLORY_WIN_MERIT, GLORY_LOSE_MERIT = 12, 4
RANKED_MODE = 11


class Features3:
    def __init__(self, f2):
        self.f2, self.e, self.t = f2, f2.e, f2.t
        self.glory = sorted(self.t['GloryRoadBaseVo'], key=lambda r: r['Id'])
        self.sign_days = sorted([r for r in self.t['SignBaseVo'] if r['SignType'] == 1], key=lambda r: r['SignDay'])
        self.sign_total = sorted([r for r in self.t['SignBaseVo'] if r['SignType'] == 2], key=lambda r: r['SignDay'])

    def _tables(self, conn):
        store.ensure_f3_tables(conn)

    # 荣耀之路
    def _glory_score(self, conn, uid):
        """荣耀之路军功 = 1000 + 匹配/排位每胜 12、每负 4（只增不减，即“历史最高”）。约 3 胜一档。"""
        try:
            wins, games = store.ranked_wins_games(conn, uid)
        except Exception:
            wins, games = 0, 0
        return GLORY_BASE_SCORE + GLORY_WIN_MERIT * wins + GLORY_LOSE_MERIT * (games - wins), wins

    def glory_info(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            score, wins = self._glory_score(conn, uid)
            claimed = store.glory_claimed_ids(conn, uid)
            opened = [r['Id'] for r in self.glory if r['AwardType'] == 2 and r['RankNum'] <= score]
            return {'score': score, 'claimed': claimed, 'opened': opened, 'wins': wins}

    def glory_reward(self, uid, gid):
        row = next((r for r in self.glory if r['Id'] == gid), None)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            score, _ = self._glory_score(conn, uid)
            if row['RankNum'] > score:
                raise ValueError('LOCAL_TASK_NOT_DONE')
            if store.glory_claimed(conn, uid, gid):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            items = [(10, 0, GLORY_DIAMONDS)]
            if row['AwardType'] == 1 and row['GiftId']:
                items += self.f2.roll_gift_groups(row['GiftId'])
            store.claim_glory(conn, uid, gid)
            return {'added': self.f2._give_all(conn, uid, items)}

    # 签到：7 天循环；state -1 未到 / 0 可领 / 1 已领
    def _sign_state(self, conn, uid):
        total = store.sign_total(conn, uid)
        signed_today = store.signed_on(conn, uid, today())
        done_in_cycle = total % 7 if not (signed_today and total % 7 == 0) else 7
        return total, signed_today, done_in_cycle

    def sign_list(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            total, signed_today, done = self._sign_state(conn, uid)
            days = []
            for i, r in enumerate(self.sign_days, 1):
                st = 1 if i <= done else (0 if i == done + 1 and not signed_today else -1)
                days.append((i, r['Type'], max(0, r['TypeId']), r['Num'], st))
            claimed = store.sign_total_claimed_ids(conn, uid)
            tot = [(r['Id'], r['SignDay'], r['GiftId'], 1 if r['Id'] in claimed else (0 if total >= r['SignDay'] else -1)) for r in self.sign_total]
            return {'all': total, 'days': days, 'total': tot}

    def sign_reward(self, uid):
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            total, signed_today, done = self._sign_state(conn, uid)
            if signed_today:
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            r = self.sign_days[done % 7]
            kind, item, num = r['Type'], max(0, r['TypeId']), r['Num']
            if kind == 18:            # 随机英雄类：本地替换为 300 钻
                kind, item, num = 10, 0, 300
            store.sign(conn, uid, today())
            return {'added': self.f2._give_all(conn, uid, [(kind, item, num)])}

    def sign_total_reward(self, uid, sid):
        row = next((r for r in self.sign_total if r['Id'] == sid), None)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            total = store.sign_total(conn, uid)
            if total < row['SignDay']:
                raise ValueError('LOCAL_TASK_NOT_DONE')
            if store.sign_total_claimed(conn, uid, sid):
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            store.claim_sign_total(conn, uid, sid)
            return {'added': self.f2._give_all(conn, uid, self.f2.roll_gift_groups(row['GiftId']))}


# —— 限时活动：每周轮换 5 个（第二批）——
EVENT_PER_WEEK = 5
EVENT_POOL_MAX_ID = 121          # 122 起（大展宏兔/兔飞猛进/龙腾四海/娃娃机/转盘/巳巳如意）本地客户端点不开，不进轮换
EVENT_UNSUPPORTED = {96, 105, 119, 120, 124, 125, 129, 130}   # 周年生日会/周年晚会/回归：客户端走独立 Anniversary 协议，本地未实现，打不开
EVENT_POOL_MIN_ID = 95            # 只用较新的活动（客户端资源更可能齐全）
EVENT_POOL_EXTRA = {47, 60, 62, 64, 65, 67, 71, 85, 87}   # 补入：只有这些老活动能换到的 13 款节日皮肤（节日皮肤原版只能活动获得）
EVENT_EXCHANGE_DISCOUNT = 0.5     # 活动兑换（英雄/皮肤）本地五折
EVENT_AUTO_DONE = {6, 7, 57, 58, 108, 4, 1, 51, 605, 49, 9, 50}   # 本地无法统计的条件：视为已完成（充值/消耗/祝福/军队/回归等）


def week_start(ts=None):
    t = datetime.date.fromtimestamp(ts or time.time())
    start = t - datetime.timedelta(days=t.weekday())
    return int(time.mktime(start.timetuple()))


class Events:
    def __init__(self, f3):
        self.f3, self.f2, self.f, self.e, self.t = f3, f3.f2, f3.f2.f, f3.e, f3.t
        self.events = {e['Id']: e for e in self.t['EventTypeBaseVo']}
        self.tasks = {}
        for r in self.t['EventTaskBaseVo']:
            self.tasks.setdefault(r['EventId'], []).append(r)
        ok = lambda e: e['Type'] == 1 and not e['IsClose'] and e['DropType'] and e['Id'] not in EVENT_UNSUPPORTED and e['Id'] <= EVENT_POOL_MAX_ID and e['Id'] < 1000 and any(t['Condition'] == 14 for t in self.tasks.get(e['Id'], []))
        # 原有新活动在前、补入的老活动接在后面，保证已排好的周次不变
        self.pool = sorted(e['Id'] for e in self.t['EventTypeBaseVo'] if ok(e) and e['Id'] >= EVENT_POOL_MIN_ID) + \
            sorted(e['Id'] for e in self.t['EventTypeBaseVo'] if ok(e) and e['Id'] in EVENT_POOL_EXTRA)

    def week_events(self, ts=None):
        """本周 5 个活动：按周序号在池里滑动，保证每周不同、可预测。"""
        start = week_start(ts)
        # 以 2026-10-05 这周（猛虎试炼 100 起的 5 个，玩家已在玩）为锚点顺序往后轮换，池子扩充也不会打乱已经开放的周
        anchor = int(time.mktime(datetime.date(2026, 10, 5).timetuple()))
        weeks = round((start - anchor) / (7 * 86400))
        n = (self.pool.index(100) if 100 in self.pool else 0) + weeks * EVENT_PER_WEEK
        picks = [self.pool[(n + i) % len(self.pool)] for i in range(min(EVENT_PER_WEEK, len(self.pool)))]
        return [(self.events[i]['param'], i, start, start + 7 * 86400 - 1) for i in picks]

    def _event_of_key(self, key):
        for k, eid, s, e in self.week_events():
            if k == key:
                return eid, s, e
        raise ValueError('LOCAL_EVENT_CLOSED')

    def _tables(self, conn):
        store.ensure_event_tables(conn)

    def _progress(self, conn, uid, cond, start):
        if cond in EVENT_AUTO_DONE:
            return 10 ** 6
        try:
            if cond in (8, 2):
                return store.battle_count_since(conn, uid, start, 'games')
            if cond == 22:
                return store.battle_count_since(conn, uid, start, 'wins')
            if cond == 53:
                return store.battle_count_since(conn, uid, start, 'mvps')
            if cond == 21:
                return self.f._acti(conn, uid)[0]
        except Exception:
            return 0
        return 0

    def info(self, uid, key):
        eid, start, _ = self._event_of_key(key)
        with self.e.connection() as conn:
            self._tables(conn)
            self.f._tables(conn)
            taken = store.event_taken_map(conn, uid, start)
            tasks = []
            for r in self.tasks.get(eid, []):
                p = 0 if r['Condition'] == 14 else self._progress(conn, uid, r['Condition'], start)
                tasks.append((r['Id'], min(p, max(1, r['ConNum']) * max(1, r['Limit'])), taken.get(r['Id'], 0)))
            items = []
            for part in (self.events[eid]['Item'] or '').split(';'):
                k, i, _ = (int(x) for x in part.split(','))
                items.append((k, i, self.e._count(conn, uid, k, i)))
            return {'tasks': tasks, 'items': items}

    def take(self, uid, key, task_id):
        eid, start, _ = self._event_of_key(key)
        row = next((r for r in self.tasks.get(eid, []) if r['Id'] == task_id), None)
        if row is None:
            raise ValueError('LOCAL_TASK_UNKNOWN')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            self.f._tables(conn)
            got = store.event_taken(conn, uid, task_id, start)
            if row['Limit'] > 0 and got >= row['Limit']:
                raise ValueError('LOCAL_ALREADY_CLAIMED')
            keys = []
            if row['Condition'] == 14:     # 兑换：扣活动道具/钻石（五折）
                if row['MonNum']:
                    keys = self.e._pay(conn, uid, [(row['MonType'], row['MonId'], max(1, math.ceil(row['MonNum'] * EVENT_EXCHANGE_DISCOUNT)))])
            else:
                need = max(1, row['ConNum']) * (got + 1)
                if self._progress(conn, uid, row['Condition'], start) < need:
                    raise ValueError('LOCAL_TASK_NOT_DONE')
            items = []
            for gid in str(row['GiftId']).replace('.0', '').split(','):
                if gid.strip().isdigit():
                    items += self.f2.roll_gift_groups(int(gid))
            store.event_take(conn, uid, task_id, start)
            return {'added': self.f2._give_all(conn, uid, items), 'items': self.e._snapshot(conn, uid, keys)}


def _event_draw(self, uid, key, multi):
    """活动抽奖：消耗活动第一个道具（每抽 n 个，十连 ×10），按 DropBaseVo(Type=DropType) 的 Chance 抽。"""
    eid, start, _ = self._event_of_key(key)
    ev = self.events[eid]
    drops = [r for r in self.t['DropBaseVo'] if r['Type'] == ev['DropType']] if ev['DropType'] else []
    if not drops:
        raise ValueError('LOCAL_EVENT_NO_DRAW')
    k, i, n = (int(x) for x in ev['Item'].split(';')[0].split(','))
    times = 10 if multi else 1
    with self.e.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        keys = self.e._pay(conn, uid, [(k, i, max(1, n) * times)])
        got, last = [], None
        for _ in range(times):
            last = random.choices(drops, weights=[max(1, r['Chance']) for r in drops])[0]
            got.append((last['ItemType'], last['ItemId'], random.randint(last['ItemNumMin'], max(last['ItemNumMin'], last['ItemNumMax']))))
        return {'added': self.f2._give_all(conn, uid, got), 'items': self.e._snapshot(conn, uid, keys), 'draw_id': last['Id']}


Events.draw = _event_draw


# —— 材料兑换（左侧“活动/材料兑换”入口 = LimitActionList；条件由服务器定义）——
EXCHANGE_ACT_ID = 1
EXCHANGE_DAILY_LIMIT = 50
# (subId, 消耗(类型,ID,数量), 获得[(类型,ID,数量)], 说明)
EXCHANGE_RECIPES = [
    (1, (1, 0, 2000), [(43, 0, 20)], '银币换万能玄玉'),
    (2, (1, 0, 5000), [(13, 200, 5)], '银币换碎片融合券'),
    (3, (2, 0, 500), [(13, 205, 10)], '金币换阵列载体'),
    (4, (2, 0, 1000), [(13, 220, 50)], '金币换天赋材料'),
    (5, (10, 0, 50), [(13, 11, 10)], '钻石换晶石'),
    (6, (10, 0, 100), [(13, 206, 5)], '钻石换晶圆'),
]


class Exchange:
    def __init__(self, f2):
        self.f2, self.e = f2, f2.e

    def _tables(self, conn):
        store.ensure_exchange_tables(conn)

    def actions(self):
        s = week_start()
        return [(EXCHANGE_ACT_ID, s, s + 7 * 86400 - 1, '材料兑换', '用银币/金币/钻石兑换养成材料，每项每天限 %d 次' % EXCHANGE_DAILY_LIMIT)]

    def conditions(self, uid):
        with self.e.connection() as conn:
            self._tables(conn)
            took = store.exchange_taken_map(conn, uid, today())
        return [(sub, cost, gain, tips, took.get(sub, 0)) for sub, cost, gain, tips in EXCHANGE_RECIPES]

    def take(self, uid, sub):
        rec = next((r for r in EXCHANGE_RECIPES if r[0] == sub), None)
        if rec is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._tables(conn)
            n = store.exchange_taken(conn, uid, sub, today())
            if n >= EXCHANGE_DAILY_LIMIT:
                raise ValueError('LOCAL_LIMIT_REACHED')
            keys = self.e._pay(conn, uid, [rec[1]])
            store.exchange_take(conn, uid, sub, today())
            return {'added': self.f2._give_all(conn, uid, rec[2]), 'items': self.e._snapshot(conn, uid, keys)}
