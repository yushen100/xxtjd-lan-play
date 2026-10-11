"""本地商城事务。价格来自原生表；周期边界是本地自定义规则。"""
import datetime
import json
import math
import sqlite3
from pathlib import Path


NO_BUY_LIMIT = True  # 本地规则（用户指定）：取消商店日/周限购
GROWTH_BOX_WEIGHT = 0.10   # 养成号宝箱：每个英雄/皮肤条目 10%（计划：老号 25%、新号 10%）
GROWTH_DUP_HERO_REFUND = 0.30   # 养成号重复获得限定英雄：返还原价 30% 钻石（计划）
ITEM_COST_DISCOUNT = 0.5  # 道具支付的商品（能源商店等）本地五折


class Economy:
    def __init__(self, db, catalog):
        self.db = Path(db)
        self.catalog = {row['Id']: row for row in catalog}
        self.talent_rows = []  # 由服务端注入 TalentBaseVo，用于默认1级天赋列表
        with self.connection() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS native_items(
              uid TEXT NOT NULL REFERENCES accounts(uid), item_type INTEGER NOT NULL,
              item_id INTEGER NOT NULL, count INTEGER NOT NULL CHECK(count>=0),
              PRIMARY KEY(uid,item_type,item_id));
            CREATE TABLE IF NOT EXISTS shop_counts(
              uid TEXT NOT NULL REFERENCES accounts(uid), store_id INTEGER NOT NULL,
              period TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=0),
              PRIMARY KEY(uid,store_id,period));
            ''')

    def connection(self):
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    @staticmethod
    def period(kind):
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
        # 自定义周期：1为日，2为周，其他为永久；不能当成官方规则。
        if kind == 1:
            return str(today)
        if kind == 2:
            iso = today.isocalendar()
            return f'{iso.year}-W{iso.week}'
        return 'lifetime'

    def items(self, uid, item_type=None):
        with self.connection() as conn:
            if item_type is None:
                return [dict(row) for row in conn.execute('SELECT * FROM native_items WHERE uid=?', (uid,))]
            return [dict(row) for row in conn.execute('SELECT * FROM native_items WHERE uid=? AND item_type=?', (uid, item_type))]

    def counts(self, uid):
        with self.connection() as conn:
            return [{'id': key, 'buyNum': conn.execute(
                'SELECT count FROM shop_counts WHERE uid=? AND store_id=? AND period=?',
                (uid, key, self.period(row['LimitedBuyType']))).fetchone()[0]}
                for key, row in self.catalog.items()
                if conn.execute('SELECT count FROM shop_counts WHERE uid=? AND store_id=? AND period=?',
                                (uid, key, self.period(row['LimitedBuyType']))).fetchone()]

    def buy(self, uid, store_id, quantity, item_type, operation_id):
        if not operation_id or quantity < 1 or quantity > 100:
            raise ValueError('LOCAL_INVALID_REQUEST')
        product = self.catalog.get(store_id)
        if product is None:
            raise ValueError(f'LOCAL_UNKNOWN_PRODUCT:{store_id}')
        # 原生 storeBuyRequest.itemType 是支付货币（1银币/2金币/10钻石/14军功），不是商品类型。
        pay = item_type
        price_key = {1: 'Silver1', 2: 'Gold1', 10: 'Diam1', 14: 'RankScore'}.get(pay)
        item_cost = product['CostItemNum'] and not (price_key and product[price_key])
        if not item_cost and (price_key is None or not product[price_key]):
            raise ValueError(f'LOCAL_PAY_TYPE_UNSUPPORTED:{store_id}:{pay}')
        item_type = product['ItemType']
        is_box = item_type == 11 and product['IsBox']
        if not is_box and item_type not in (6, 13, 16, 22, 25, 26, 27, 28, 34, 37, 41, 43):
            raise ValueError('LOCAL_PRODUCT_UNSUPPORTED')
        # MinRankId：本地军衔固定为最高，条件总是满足。
        costs = {'silver': 0, 'gold': 0, 'diamonds': 0, 'rank_score': 0}
        item_costs = []
        if item_cost:   # 能源商店等：用道具（如能源币 13:211）支付，本地打 ITEM_COST_DISCOUNT 折
            item_costs = [(product['CostItemType'], product['CostItemId'], max(1, math.ceil(product['CostItemNum'] * quantity * ITEM_COST_DISCOUNT)))]
        else:
            costs[{1: 'silver', 2: 'gold', 10: 'diamonds', 14: 'rank_score'}[pay]] = product[price_key] * quantity
        if any(value < 0 for value in costs.values()) or not (any(costs.values()) or item_costs):
            raise ValueError('LOCAL_PRICE_UNSUPPORTED')
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT result FROM operations WHERE uid=? AND operation_id=?', (uid, operation_id)).fetchone()
            if old:
                result = json.loads(old['result'])
                if result['store_id'] != store_id or result['quantity'] != quantity:
                    raise ValueError('LOCAL_REQUEST_ID_CONFLICT')
                # 重试只同步当前库存和余额，不能重复通知新增奖励。
                user = conn.execute('SELECT * FROM accounts WHERE uid=?', (uid,)).fetchone()
                result['balance'] = {key: user[key] for key in ('silver', 'gold', 'diamonds', 'rank_score')}
                result['added'] = 0
                for reward in result.get('rewards', []):
                    reward['added'] = 0
                    if reward['item_type'] not in (5, 6):
                        row = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?',
                                           (uid, reward['item_type'], reward['item_id'])).fetchone()
                        reward['count'] = row[0] if row else 0
                result['item_count'] = result['rewards'][0]['count'] if result.get('rewards') else 0
                return result
            user = conn.execute('SELECT * FROM accounts WHERE uid=?', (uid,)).fetchone()
            if user is None:
                raise ValueError('LOCAL_LOGIN_REQUIRED')
            if any(user[key] < value for key, value in costs.items()):
                raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
            period = self.period(product['LimitedBuyType'])
            count = conn.execute('SELECT count FROM shop_counts WHERE uid=? AND store_id=? AND period=?', (uid, store_id, period)).fetchone()
            bought = count['count'] if count else 0
            if not NO_BUY_LIMIT and product['LimitedBuyTimes'] and bought + quantity > product['LimitedBuyTimes']:
                raise ValueError('LOCAL_LIMIT_REACHED')
            pay_keys = self._pay(conn, uid, item_costs) if item_costs else []
            conn.execute('UPDATE accounts SET silver=silver-?,gold=gold-?,diamonds=diamonds-?,rank_score=rank_score-? WHERE uid=?',
                         (costs['silver'], costs['gold'], costs['diamonds'], costs['rank_score'], uid))
            amount = product['ItemNum'] * quantity
            rewards = []
            if is_box:
                for _ in range(amount):
                    rewards.append(self._grant(conn, uid, *self._roll_gift(product['ItemId'], GROWTH_BOX_WEIGHT if self.is_growth(uid) else None)))
            else:
                rewards.append(self._grant(conn, uid, item_type, product['ItemId'], amount))
            conn.execute('INSERT INTO shop_counts VALUES(?,?,?,?) ON CONFLICT(uid,store_id,period) DO UPDATE SET count=count+excluded.count',
                         (uid, store_id, period, quantity))
            rewards += [dict(r, added=0) for r in self._snapshot(conn, uid, pay_keys)]   # 让客户端刷新被扣的道具数量
            result = {'store_id': store_id, 'quantity': quantity, 'item_type': item_type, 'item_id': product['ItemId'],
                      'added': amount, 'rewards': rewards, 'item_count': rewards[0]['count'], 'balance': {key: user[key] - value for key, value in costs.items()}}
            conn.execute('INSERT INTO operations VALUES(?,?,?)', (uid, operation_id, json.dumps(result)))
            return result

    HERO_SKIN_BOX_WEIGHT = 0.25  # 用户要求：宝箱里每个英雄/皮肤条目概率调到25%，其余按原表比例分剩余。
    gift_rows = []

    is_growth = staticmethod(lambda uid: False)   # 由服务器注入

    def _roll_gift(self, gift_id, weight=None):
        import random
        rows = [g for g in self.gift_rows if g['Id'] == gift_id]
        if not rows:
            raise ValueError(f'LOCAL_GIFT_UNKNOWN:{gift_id}')
        special = [g for g in rows if g['ItemType'] in (5, 6)]
        rest = [g for g in rows if g['ItemType'] not in (5, 6)]
        w = self.HERO_SKIN_BOX_WEIGHT if weight is None else weight
        share = min(1.0, w * len(special))
        total_rest = sum(g['Pr'] for g in rest) or 1
        weights = [w if share < 1 else 1 / len(special) for _ in special]
        weights += [(1 - share) * g['Pr'] / total_rest for g in rest]
        pick = random.choices(special + rest, weights=weights)[0]
        return pick['ItemType'], pick['ItemId'], pick['ItemNum']

    def _grant(self, conn, uid, kind, item, num):
        """发放奖励。英雄已拥有时按本地规则转为该英雄碎片×5；皮肤记入 owned_skins。"""
        conn.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')
        if kind == 5 and getattr(self, 'valid_heroes', None) is not None and item not in self.valid_heroes:
            kind, item, num = 43, 0, 100 * num   # 客户端没有皮肤数据的英雄（会导致英雄界面报错）：本地改发万能玄玉
        if kind == 5:
            if conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, item)).fetchone():
                limited = next((h for h in getattr(self, 'hero_rows', []) if h['Id'] == item and h['IsBuy'] != 0 and h['Diam']), None)
                if limited and self.is_growth(uid):
                    price = 3688 if limited['IsBuy'] == 3 and limited['Diam'] == 3688 else (3488 if limited['IsBuy'] in (3, 5, 6) else limited['Diam'])
                    refund = int(price * GROWTH_DUP_HERO_REFUND) * num
                    conn.execute('UPDATE accounts SET diamonds=diamonds+? WHERE uid=?', (refund, uid))
                    return {'item_type': 10, 'item_id': 0, 'added': refund,
                            'count': conn.execute('SELECT diamonds FROM accounts WHERE uid=?', (uid,)).fetchone()[0]}
                kind, num = 22, 5 * num
            else:
                conn.execute('INSERT INTO heroes(uid,hero_id,skin_id) VALUES(?,?,?)', (uid, item, (getattr(self, 'default_skin', {}) or {}).get(item, 0)))
                return {'item_type': 5, 'item_id': item, 'added': num, 'count': 1}
        if kind == 6:
            if not conn.execute('INSERT OR IGNORE INTO owned_skins VALUES(?,?)', (uid, item)).rowcount:
                kind, item, num = 43, 0, 100  # 本地规则：重复皮肤转为万能玄玉×100
            else:
                return {'item_type': 6, 'item_id': item, 'added': 1, 'count': 1}
        conn.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count', (uid, kind, item, num))
        count = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid, kind, item)).fetchone()[0]
        return {'item_type': kind, 'item_id': item, 'added': num, 'count': count}

    def upgrade_star(self, uid, hero_id, universal, quick, operation_id, star_table):
        """按原生升星界面的成本表扣费；通用碎片只补英雄碎片缺口。"""
        if not operation_id:
            raise ValueError('LOCAL_INVALID_REQUEST')
        rules = {row['Id']: row for row in star_table}
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT result FROM operations WHERE uid=? AND operation_id=?', (uid, operation_id)).fetchone()
            signature = [hero_id, bool(universal), bool(quick)]
            if old:
                if json.loads(old['result']).get('star_request') != signature:
                    raise ValueError('LOCAL_REQUEST_ID_CONFLICT')
            else:
                hero = conn.execute('SELECT star FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()
                if hero is None:
                    raise ValueError('LOCAL_HERO_UNAVAILABLE')
                current = hero['star']
                target = min(50, current + (10 if quick else 1))
                if target == current:
                    raise ValueError('LOCAL_STAR_MAX')
                def affordable(n):
                    rows = [rules[level] for level in range(current+1, current+n+1)]
                    need_s = sum(r['HeroSui'] for r in rows); act = sum(r['ActNum'] for r in rows)
                    def cnt(kind, item):
                        row = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid,kind,item)).fetchone()
                        return row['count'] if row else 0
                    own = min(need_s, cnt(22, hero_id)); miss = need_s - own
                    bal = conn.execute('SELECT silver FROM accounts WHERE uid=?',(uid,)).fetchone()['silver']
                    return bal >= sum(r['Silver'] for r in rows) and cnt(13,19) >= act and (not miss or (universal and cnt(22,0) >= miss))
                if quick:
                    # 本地规则：快速升星升到材料够用的最高级（最多10级），一级都不够才报材料不足。
                    while target > current + 1 and not affordable(target - current):
                        target -= 1
                steps = [rules[level] for level in range(current+1, target+1)]
                silver = sum(row['Silver'] for row in steps)
                shards = sum(row['HeroSui'] for row in steps)
                activation = sum(row['ActNum'] for row in steps)
                def count(kind, item):
                    row = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid,kind,item)).fetchone()
                    return row['count'] if row else 0
                own_shards = min(shards, count(22, hero_id))
                missing = shards-own_shards
                balance = conn.execute('SELECT silver FROM accounts WHERE uid=?',(uid,)).fetchone()
                if balance is None or balance['silver'] < silver:
                    raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
                if count(13,19) < activation or (missing and (not universal or count(22,0) < missing)):
                    raise ValueError('LOCAL_MATERIAL_INSUFFICIENT')
                for kind,item,amount in [(22,hero_id,own_shards),(22,0,missing),(13,19,activation)]:
                    if amount:
                        conn.execute('UPDATE native_items SET count=count-? WHERE uid=? AND item_type=? AND item_id=?',(amount,uid,kind,item))
                conn.execute('UPDATE accounts SET silver=silver-? WHERE uid=?',(silver,uid))
                conn.execute('UPDATE heroes SET star=? WHERE uid=? AND hero_id=?',(target,uid,hero_id))
                conn.execute('INSERT INTO operations VALUES(?,?,?)',(uid,operation_id,json.dumps({'star_request':signature})))
            hero = dict(conn.execute('SELECT * FROM heroes WHERE uid=? AND hero_id=?',(uid,hero_id)).fetchone())
            silver = conn.execute('SELECT silver FROM accounts WHERE uid=?',(uid,)).fetchone()[0]
            items = [dict(row) for row in conn.execute('SELECT * FROM native_items WHERE uid=? AND ((item_type=22 AND item_id IN (?,0)) OR (item_type=13 AND item_id=19))',(uid,hero_id))]
            return {'hero':hero,'silver':silver,'items':items}

    # ---- 成长：觉醒（芯片）、天赋、熟练度。成本读原生表；无法从客户端确定的规则在注释中标明为本地规则。----
    def _count(self, conn, uid, kind, item):
        row = conn.execute('SELECT count FROM native_items WHERE uid=? AND item_type=? AND item_id=?', (uid, kind, item)).fetchone()
        return row['count'] if row else 0

    def friend_shop_buy(self, uid, shop_id, num, rows):
        """好友商店：FriendShopBaseVo.Price×数量，扣友情币 (13,35)；发 Type/TypeId×ItemNum。限购暂不记录（本地规则）。"""
        row = next((r for r in rows if r['Id'] == shop_id), None)
        num = max(1, num or 1)
        if row is None:
            raise ValueError('LOCAL_UNKNOWN_PRODUCT')
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            keys = self._pay(conn, uid, [(13, 35, row['Price'] * num)])
            got = self._grant(conn, uid, row['Type'], row['TypeId'], row['ItemNum'] * num)
            items = self._snapshot(conn, uid, keys)
            return {'items': items, 'added': got}

    def _pay(self, conn, uid, costs):
        """costs: [(item_type, item_id, num)]，类型1/2/10为银币/金币/钻石。先全部校验再扣，失败抛错由调用方回滚。"""
        money = {1: 'silver', 2: 'gold', 10: 'diamonds'}
        merged = {}
        for kind, item, num in costs:
            if num:
                key = (kind, 0 if kind in money else item)
                merged[key] = merged.get(key, 0) + num
        user = conn.execute('SELECT * FROM accounts WHERE uid=?', (uid,)).fetchone()
        for (kind, item), num in merged.items():
            have = user[money[kind]] if kind in money else self._count(conn, uid, kind, item)
            if have < num:
                raise ValueError('LOCAL_BALANCE_INSUFFICIENT' if kind in money else 'LOCAL_MATERIAL_INSUFFICIENT')
        for (kind, item), num in merged.items():
            if kind in money:
                conn.execute(f'UPDATE accounts SET {money[kind]}={money[kind]}-? WHERE uid=?', (num, uid))
            else:
                conn.execute('UPDATE native_items SET count=count-? WHERE uid=? AND item_type=? AND item_id=?', (num, uid, kind, item))
        return list(merged)

    def _snapshot(self, conn, uid, keys):
        money = {1: 'silver', 2: 'gold', 10: 'diamonds'}
        user = conn.execute('SELECT * FROM accounts WHERE uid=?', (uid,)).fetchone()
        out = []
        for kind, item in keys:
            out.append({'item_type': kind, 'item_id': item,
                        'count': user[money[kind]] if kind in money else self._count(conn, uid, kind, item)})
        return out

    def _replay(self, conn, uid, operation_id, signature):
        old = conn.execute('SELECT result FROM operations WHERE uid=? AND operation_id=?', (uid, operation_id)).fetchone()
        if old:
            stored = json.loads(old['result'])
            if stored.get('request') != signature:
                raise ValueError('LOCAL_REQUEST_ID_CONFLICT')
            return stored
        return None

    def awake_level_up(self, uid, hero_id, universal, quick, use_diam, operation_id, cost_table):
        """芯片（觉醒）升级：每级按 ICCostBaseVo[ICLv=当前级]，类型41且物品0时替换为英雄专属；
        勾选通用时用 (41,0) 补专属缺口。钻石代付比例未知，本地不支持。快速升级为本地规则：一次最多10级、只升到材料够用的级数。"""
        if not operation_id:
            raise ValueError('LOCAL_INVALID_REQUEST')
        if use_diam:
            raise ValueError('LOCAL_DIAM_PAY_UNSUPPORTED')
        rules = {row['ICLv']: row for row in cost_table}
        signature = ['awake', hero_id, bool(universal), bool(quick)]
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not self._replay(conn, uid, operation_id, signature):
                hero = conn.execute('SELECT awake FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()
                if hero is None:
                    raise ValueError('LOCAL_HERO_UNAVAILABLE')
                current = hero['awake']
                if current >= 50:
                    raise ValueError('LOCAL_AWAKE_MAX')
                levels, keys = 0, set()
                for level in range(current, min(50, current + (10 if quick else 1))):
                    row = rules[level]
                    costs = []
                    for n in (1, 2, 3):
                        kind, item, num = row[f'CostType{n}'], row[f'CostItemId{n}'], row[f'CostNum{n}']
                        if kind == 41 and item == 0:
                            own = self._count(conn, uid, 41, hero_id)
                            take = min(own, num)
                            costs.append((41, hero_id, take))
                            if num - take:
                                if not universal:
                                    costs.append((41, hero_id, num))  # 触发材料不足
                                else:
                                    costs.append((41, 0, num - take))
                        else:
                            costs.append((kind, item, num))
                    conn.execute('SAVEPOINT step')
                    try:
                        keys.update(self._pay(conn, uid, costs))
                    except ValueError:
                        conn.execute('ROLLBACK TO step')
                        if levels == 0:
                            raise
                        break
                    conn.execute('RELEASE step')
                    levels += 1
                conn.execute('UPDATE heroes SET awake=awake+? WHERE uid=? AND hero_id=?', (levels, uid, hero_id))
                conn.execute('INSERT INTO operations VALUES(?,?,?)', (uid, operation_id, json.dumps({'request': signature, 'keys': sorted(keys)})))
            stored = self._replay(conn, uid, operation_id, signature)
            hero = dict(conn.execute('SELECT * FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone())
            return {'hero': hero, 'items': self._snapshot(conn, uid, [tuple(k) for k in stored['keys']])}

    def _talent_tables(self, conn):
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS hero_talents(uid TEXT NOT NULL, hero_id INTEGER NOT NULL, tid INTEGER NOT NULL,
          level INTEGER NOT NULL CHECK(level>=0), PRIMARY KEY(uid,hero_id,tid));
        CREATE TABLE IF NOT EXISTS hero_talent_use(uid TEXT NOT NULL, hero_id INTEGER NOT NULL, slot INTEGER NOT NULL,
          tid INTEGER NOT NULL, PRIMARY KEY(uid,hero_id,slot));''')

    def talent_info(self, uid, hero_id):
        with self.connection() as conn:
            self._talent_tables(conn)
            levels = {r['Tid']: 1 for r in self.talent_rows}
            levels.update({r['tid']: r['level'] for r in conn.execute('SELECT tid,level FROM hero_talents WHERE uid=? AND hero_id=?', (uid, hero_id))})
            use = [r['tid'] for r in conn.execute('SELECT tid FROM hero_talent_use WHERE uid=? AND hero_id=? ORDER BY slot', (uid, hero_id))]
            return levels, use

    def talent_lv_up(self, uid, hero_id, tid, use_chip, operation_id, talent_table):
        """天赋升级：按 TalentBaseVo(Tid, Lv=当前级) 扣 Type1/Type2 成本；勾选通用芯片且 Type2 材料不足时改扣 (43,0)×GeneCostNum（本地推定）。"""
        if not operation_id:
            raise ValueError('LOCAL_INVALID_REQUEST')
        signature = ['talent_up', hero_id, tid, bool(use_chip)]
        rows = {(r['Tid'], r['Lv']): r for r in talent_table}
        with self.connection() as conn:
            self._talent_tables(conn)
            conn.execute('BEGIN IMMEDIATE')
            if not self._replay(conn, uid, operation_id, signature):
                if conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone() is None:
                    raise ValueError('LOCAL_HERO_UNAVAILABLE')
                row = conn.execute('SELECT level FROM hero_talents WHERE uid=? AND hero_id=? AND tid=?', (uid, hero_id, tid)).fetchone()
                level = row['level'] if row else 1
                # 表中 Lv=L 行记录从 L 级升到 L+1 级的成本；没有 L+1 行即满级。
                rule = rows.get((tid, level))
                if rule is None:
                    raise ValueError('LOCAL_TALENT_UNKNOWN')
                if (tid, level + 1) not in rows:
                    raise ValueError('LOCAL_TALENT_MAX')
                # 原版界面三项：银币(Type1) + 天赋基因(Type2) + 英雄玄玉(43,英雄)×GeneCostNum；勾选“万能玄玉抵扣”时用 (43,0) 补英雄玄玉缺口。
                costs = [(rule['Type1'], rule['TypeId1'], rule['CostNum1']), (rule['Type2'], rule['TypeId2'], rule['CostNum2'])]
                gene = rule['GeneCostNum']
                own = min(gene, self._count(conn, uid, 43, hero_id))
                if own:
                    costs.append((43, hero_id, own))
                if gene - own:
                    costs.append((43, 0 if use_chip else hero_id, gene - own if use_chip else gene))
                keys = self._pay(conn, uid, costs)
                conn.execute('INSERT INTO hero_talents VALUES(?,?,?,?) ON CONFLICT(uid,hero_id,tid) DO UPDATE SET level=excluded.level',
                             (uid, hero_id, tid, level + 1))
                conn.execute('INSERT INTO operations VALUES(?,?,?)', (uid, operation_id, json.dumps({'request': signature, 'keys': sorted(keys)})))
            stored = self._replay(conn, uid, operation_id, signature)
            return {'items': self._snapshot(conn, uid, [tuple(k) for k in stored['keys']])}

    def talent_use(self, uid, hero_id, tids, talent_table):
        """装配天赋：每层(Group 1-5)选一个；原生界面全部天赋默认1级可选。"""
        groups = {r['Tid']: r['Group'] for r in talent_table}
        if len(tids) > 5 or len({groups.get(t) for t in tids}) != len(tids) or any(t not in groups for t in tids):
            raise ValueError('LOCAL_TALENT_INVALID')
        with self.connection() as conn:
            self._talent_tables(conn)
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM hero_talent_use WHERE uid=? AND hero_id=?', (uid, hero_id))
            for slot, t in enumerate(sorted(tids, key=lambda x: groups[x])):
                conn.execute('INSERT INTO hero_talent_use VALUES(?,?,?,?)', (uid, hero_id, slot, t))

    def talent_clear(self, uid, hero_id, operation_id, reset_cost):
        """重置天赋：按 VariableBaseVo Talent 的成本串扣费（全部条目），只清空装配、保留天赋等级（与原生客户端收到响应后的处理一致）。"""
        if not operation_id:
            raise ValueError('LOCAL_INVALID_REQUEST')
        signature = ['talent_clear', hero_id]
        costs = [tuple(int(x) for x in part.split(',')) for part in reset_cost.split(';') if part]
        with self.connection() as conn:
            self._talent_tables(conn)
            conn.execute('BEGIN IMMEDIATE')
            if not self._replay(conn, uid, operation_id, signature):
                keys = self._pay(conn, uid, costs)
                conn.execute('DELETE FROM hero_talent_use WHERE uid=? AND hero_id=?', (uid, hero_id))
                conn.execute('INSERT INTO operations VALUES(?,?,?)', (uid, operation_id, json.dumps({'request': signature, 'keys': sorted(keys)})))
            stored = self._replay(conn, uid, operation_id, signature)
            return {'items': self._snapshot(conn, uid, [tuple(k) for k in stored['keys']])}

    def reset_proficiency(self, uid, hero_id):
        """熟练度重置：原生请求只带英雄，成本未知，本地免费置0。"""
        with self.connection() as conn:
            if not conn.execute('UPDATE heroes SET proficiency=0 WHERE uid=? AND hero_id=?', (uid, hero_id)).rowcount:
                raise ValueError('LOCAL_HERO_UNAVAILABLE')

    def add_proficiency(self, uid, hero_id, amount):
        """对局结算后增加出战英雄熟练度（本地规则：每局固定值）。"""
        with self.connection() as conn:
            conn.execute('UPDATE heroes SET proficiency=proficiency+? WHERE uid=? AND hero_id=?', (amount, uid, hero_id))
