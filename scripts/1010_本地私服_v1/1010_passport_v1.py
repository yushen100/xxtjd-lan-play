"""通行证（本地实现，数据全部取自原版表）。
- 期数：PassportTimeBaseVo 中最后一期（本地固定为当前期），等级表用 PassportLvBaseVo 第 1 套（Id 1xxxx）。
- 档位：0 普通 / 1 优秀 / 2 完美。晋升钻石取 VariableBaseVo.PassGradeCost2（238,488），完美额外 +PassPerfectLv 级。
- 购买等级：每级 PassportLvBaseVo.Price 钻石。
- 领奖：普通轨道=Grade0 行；优秀/完美可再领 Grade1 行。
- 通行证商店：PassportShopBaseVo（Term 0 与当期），通行币 13:38 支付，按 LimitedBuyTimes 限购（本期内）。
- 经验：本地规则——每局结算 +200，周上限 PassLimitedExp。"""
import importlib.util
import time
from contextlib import nullcontext
from pathlib import Path

_spec = importlib.util.spec_from_file_location('passport_store', Path(__file__).with_name('1011_passport_store_v1.py'))
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)   # 通行证的 SQL 全部在这里

EXP_PER_LV = 1000
EXP_PER_BATTLE = 1000  # 每局一级


class Passport:
    def __init__(self, economy, tables):
        self.e = economy
        self.t = tables
        var = {v['Name']: v for v in tables['VariableBaseVo']}
        self.grade_cost = [int(x) for x in var['PassGradeCost2']['stringValue'].split(',')]
        self.perfect_lv = var['PassPerfectLv']['intValue']
        self.week_cap = 10 ** 9  # 本地规则：取消每周经验上限（原表 PassLimitedExp=7000）
        self.term = tables['PassportTimeBaseVo'][-1]['Id']
        self.lv_rows = {(r['Grade'], r['Lv']): r for r in tables['PassportLvBaseVo'] if r['Id'] // 10000 == 1}
        self.max_lv = max(lv for _, lv in self.lv_rows)
        self.shop = {r['Id']: r for r in tables['PassportShopBaseVo']}   # 本地规则：历期通行证商品（含往期通行证皮肤）全部常驻

    def _table(self, conn):
        store.ensure_tables(conn)

    def _row(self, conn, uid):
        self._table(conn)
        return store.row(conn, uid)

    def level(self, row):
        return min(self.max_lv, 1 + row['exp'] // EXP_PER_LV)

    def info(self, uid):
        with self.e.connection() as conn:
            r = self._row(conn, uid)
            end = int(time.mktime(time.strptime(self.t['PassportTimeBaseVo'][-1]['EndTime'], '%Y|%m|%d|%H|%M|%S')))
            return {'termId': self.term, 'grade': r['grade'], 'lv': self.level(r), 'exp': r['exp'] % EXP_PER_LV, 'endTime': end,
                    'awardNormal': r['taken_normal'], 'awardGood': r['taken_good'], 'awardPerfect': r['taken_good'] if r['grade'] >= 2 else 0}

    def add_battle_exp(self, uid, conn=None):
        week = time.strftime('%Y-W%W')
        with (self.e.connection() if conn is None else nullcontext(conn)) as conn:
            r = self._row(conn, uid)
            used = r['week_exp'] if r['week'] == week else 0
            gain = max(0, min(EXP_PER_BATTLE, self.week_cap - used))
            store.add_week_exp(conn, uid, gain, week, used + gain)
            return gain

    def buy_level(self, uid, num):
        num = max(1, num or 1)
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            r = self._row(conn, uid)
            lv = self.level(r)
            num = min(num, self.max_lv - lv)
            if num <= 0:
                raise ValueError('LOCAL_PASSPORT_MAX')
            price = sum(self.lv_rows[(0, l)]['Price'] for l in range(lv + 1, lv + num + 1))
            keys = self.e._pay(conn, uid, [(10, 0, price)])
            store.add_exp(conn, uid, num * EXP_PER_LV)
            return {'items': self.e._snapshot(conn, uid, keys), 'lv': lv + num}

    def promote(self, uid):
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            r = self._row(conn, uid)
            if r['grade'] >= 2:
                raise ValueError('LOCAL_PASSPORT_GRADE_MAX')
            # 原版：普通→优秀 / 普通→完美 价格分别为两档；已是优秀再升完美按差价（本地规则）。
            target = 2 if r['grade'] == 1 else 1
            cost = self.grade_cost[target - 1] - (self.grade_cost[0] if r['grade'] == 1 else 0)
            keys = self.e._pay(conn, uid, [(10, 0, cost)])
            add = self.perfect_lv * EXP_PER_LV if target == 2 else 0
            store.set_grade(conn, uid, target, add, (self.max_lv - 1) * EXP_PER_LV)
            r = self._row(conn, uid)
            return {'items': self.e._snapshot(conn, uid, keys), 'lv': self.level(r), 'grade': target}

    def take_all(self, uid):
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            r = self._row(conn, uid)
            lv, got = self.level(r), []
            for l in range(r['taken_normal'] + 1, lv + 1):
                got += self._give_row(conn, uid, self.lv_rows[(0, l)])
            good = r['taken_good']
            if r['grade'] >= 1:
                for l in range(good + 1, lv + 1):
                    got += self._give_row(conn, uid, self.lv_rows[(1, l)])
                good = lv
            store.set_taken(conn, uid, lv, good)
            return {'added': got, 'normal': lv, 'good': good, 'perfect': good if r['grade'] >= 2 else 0}

    def _give_row(self, conn, uid, row):
        out = []
        for k, i, n in ((row['ItemType'], row['ItemId'], row['ItemNum']), (row['ItemType1'], row['ItemId1'], row['ItemNum1'])):
            if k and n:
                if k in (1, 2, 10):
                    cnt = store.add_money(conn, uid, k, n)
                    out.append({'item_type': k, 'item_id': 0, 'added': n, 'count': cnt})
                else:
                    out.append(self.e._grant(conn, uid, k, i, n))
        return out

    def shop_info(self, uid):
        with self.e.connection() as conn:
            self._table(conn)
            bought = store.shop_bought(conn, uid)
            return [(sid, bought.get(sid, 0)) for sid in self.shop]

    def shop_buy(self, uid, sid, num):
        num = max(1, num or 1)
        row = self.shop.get(sid)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.e.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._table(conn)
            have = store.shop_bought_one(conn, uid, sid)
            if row['LimitedBuyTimes'] and have + num > row['LimitedBuyTimes']:
                raise ValueError('LOCAL_BUY_LIMIT')
            keys = self.e._pay(conn, uid, [(13, 38, row['Price'] * num)])
            got = self._give_row(conn, uid, {'ItemType': row['ItemType'], 'ItemId': row['ItemId'], 'ItemNum': row['ItemNum'] * num,
                                             'ItemType1': 0, 'ItemId1': 0, 'ItemNum1': 0})
            store.shop_record(conn, uid, sid, num)
            return {'items': self.e._snapshot(conn, uid, keys), 'added': got}


# —— 通行证任务（按本周战绩统计，完成即自动加经验）——
AUTO_DONE_TASK_TYPES = {6, 8}   # 消耗金币、发红包：本地统计不了，视为完成


def _week_start_unix():
    import datetime
    t = datetime.date.today()
    return int(time.mktime((t - datetime.timedelta(days=t.weekday())).timetuple()))


def _task_progress(self, conn, uid, ttype):
    ws = _week_start_unix()
    stat = {1: 'games', 3: 'wins', 5: 'kills', 4: 'rank_wins'}
    try:
        if ttype == 10:
            return 1
        if ttype in stat:
            return store.week_stat(conn, uid, ws, stat[ttype])
    except Exception:
        return 0
    return 10 ** 6 if ttype in AUTO_DONE_TASK_TYPES else 0


def task_list(self, uid, conn=None):
    """返回 [(id, finishNum, progress)], 本周任务经验；并把新完成的次数折算成经验加到通行证。"""
    week = time.strftime('%Y-W%W')
    with (self.e.connection() if conn is None else nullcontext(conn)) as conn:
        self._row(conn, uid)
        store.ensure_task_table(conn)
        out, week_exp, gain = [], 0, 0
        for r in self.t['PassportTaskBaseVo']:
            p = _task_progress(self, conn, uid, r['TaskType'])
            n = min(r['Repeat'], p // max(1, r['ConditionNum']))
            old = store.task_done(conn, uid, week, r['Id'])
            if n > old:
                gain += (n - old) * r['PassExp']
                store.set_task_done(conn, uid, week, r['Id'], n)
            week_exp += n * r['PassExp']
            shown = p if n >= r['Repeat'] else p - n * r['ConditionNum']
            out.append((r['Id'], n, min(max(0, shown), r['ConditionNum'])))
        if gain:
            store.add_exp(conn, uid, gain)
        return out, week_exp


Passport.task_list = task_list
_orig_add = Passport.add_battle_exp


def _add_battle_exp(self, uid, conn=None):
    # 任务经验也在同一事务内，故障交给调用方回滚。
    with (self.e.connection() if conn is None else nullcontext(conn)) as conn:
        g = _orig_add(self, uid, conn=conn)
        before = self._row(conn, uid)['exp']
        self.task_list(uid, conn=conn)
        return g + self._row(conn, uid)['exp'] - before


Passport.add_battle_exp = _add_battle_exp
