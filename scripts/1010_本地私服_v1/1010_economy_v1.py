"""本地商城事务。价格来自原生表；周期边界是本地自定义规则。"""
import datetime
import json
import math
import importlib.util
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_spec = importlib.util.spec_from_file_location('economy_store', Path(__file__).with_name('1011_economy_store_v1.py'))
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)   # 经济/成长的 SQL 全部在这里


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
            store.ensure_schema(conn)

    @contextmanager
    def connection(self):
        """一次操作一个连接：成功提交、异常回滚、结束关闭（原实现不关闭连接）。"""
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                yield conn
        finally:
            conn.close()

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
            return store.items(conn, uid, item_type)

    def counts(self, uid):
        with self.connection() as conn:
            out = []
            for key, row in self.catalog.items():
                n = store.shop_count(conn, uid, key, self.period(row['LimitedBuyType']))
                if n is not None:
                    out.append({'id': key, 'buyNum': n})
            return out

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
            result = store.operation(conn, uid, operation_id)
            if result:
                if result['store_id'] != store_id or result['quantity'] != quantity:
                    raise ValueError('LOCAL_REQUEST_ID_CONFLICT')
                # 重试只同步当前库存和余额，不能重复通知新增奖励。
                user = store.account(conn, uid)
                result['balance'] = {key: user[key] for key in ('silver', 'gold', 'diamonds', 'rank_score')}
                result['added'] = 0
                for reward in result.get('rewards', []):
                    reward['added'] = 0
                    if reward['item_type'] not in (5, 6):
                        reward['count'] = store.item_count(conn, uid, reward['item_type'], reward['item_id'])
                result['item_count'] = result['rewards'][0]['count'] if result.get('rewards') else 0
                return result
            user = store.account(conn, uid)
            if user is None:
                raise ValueError('LOCAL_LOGIN_REQUIRED')
            if any(user[key] < value for key, value in costs.items()):
                raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
            period = self.period(product['LimitedBuyType'])
            bought = store.shop_count(conn, uid, store_id, period) or 0
            if not NO_BUY_LIMIT and product['LimitedBuyTimes'] and bought + quantity > product['LimitedBuyTimes']:
                raise ValueError('LOCAL_LIMIT_REACHED')
            pay_keys = self._pay(conn, uid, item_costs) if item_costs else []
            store.charge(conn, uid, costs['silver'], costs['gold'], costs['diamonds'], costs['rank_score'])
            amount = product['ItemNum'] * quantity
            rewards = []
            if is_box:
                for _ in range(amount):
                    rewards.append(self._grant(conn, uid, *self._roll_gift(product['ItemId'], GROWTH_BOX_WEIGHT if self.is_growth(uid) else None)))
            else:
                rewards.append(self._grant(conn, uid, item_type, product['ItemId'], amount))
            store.add_shop_count(conn, uid, store_id, period, quantity)
            rewards += [dict(r, added=0) for r in self._snapshot(conn, uid, pay_keys)]   # 让客户端刷新被扣的道具数量
            result = {'store_id': store_id, 'quantity': quantity, 'item_type': item_type, 'item_id': product['ItemId'],
                      'added': amount, 'rewards': rewards, 'item_count': rewards[0]['count'], 'balance': {key: user[key] - value for key, value in costs.items()}}
            store.save_operation(conn, uid, operation_id, result)
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
        store.ensure_skin_table(conn)
        if kind == 5 and getattr(self, 'valid_heroes', None) is not None and item not in self.valid_heroes:
            kind, item, num = 43, 0, 100 * num   # 客户端没有皮肤数据的英雄（会导致英雄界面报错）：本地改发万能玄玉
        if kind == 5:
            if store.owns_hero(conn, uid, item):
                limited = next((h for h in getattr(self, 'hero_rows', []) if h['Id'] == item and h['IsBuy'] != 0 and h['Diam']), None)
                if limited and self.is_growth(uid):
                    price = 3688 if limited['IsBuy'] == 3 and limited['Diam'] == 3688 else (3488 if limited['IsBuy'] in (3, 5, 6) else limited['Diam'])
                    refund = int(price * GROWTH_DUP_HERO_REFUND) * num
                    return {'item_type': 10, 'item_id': 0, 'added': refund, 'count': store.add_diamonds(conn, uid, refund)}
                kind, num = 22, 5 * num
            else:
                store.insert_hero(conn, uid, item, (getattr(self, 'default_skin', {}) or {}).get(item, 0))
                return {'item_type': 5, 'item_id': item, 'added': num, 'count': 1}
        if kind == 6:
            if not store.add_skin(conn, uid, item):
                kind, item, num = 43, 0, 100  # 本地规则：重复皮肤转为万能玄玉×100
            else:
                return {'item_type': 6, 'item_id': item, 'added': 1, 'count': 1}
        count = store.add_item(conn, uid, kind, item, num)
        return {'item_type': kind, 'item_id': item, 'added': num, 'count': count}

    def upgrade_star(self, uid, hero_id, universal, quick, operation_id, star_table):
        """按原生升星界面的成本表扣费；通用碎片只补英雄碎片缺口。"""
        if not operation_id:
            raise ValueError('LOCAL_INVALID_REQUEST')
        rules = {row['Id']: row for row in star_table}
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = store.operation(conn, uid, operation_id)
            signature = [hero_id, bool(universal), bool(quick)]
            if old:
                if old.get('star_request') != signature:
                    raise ValueError('LOCAL_REQUEST_ID_CONFLICT')
            else:
                hero = store.hero(conn, uid, hero_id)
                if hero is None:
                    raise ValueError('LOCAL_HERO_UNAVAILABLE')
                current = hero['star']
                target = min(50, current + (10 if quick else 1))
                if target == current:
                    raise ValueError('LOCAL_STAR_MAX')
                def affordable(n):
                    rows = [rules[level] for level in range(current+1, current+n+1)]
                    need_s = sum(r['HeroSui'] for r in rows); act = sum(r['ActNum'] for r in rows)
                    cnt = lambda kind, item: store.item_count(conn, uid, kind, item)
                    own = min(need_s, cnt(22, hero_id)); miss = need_s - own
                    bal = store.silver(conn, uid)['silver']
                    return bal >= sum(r['Silver'] for r in rows) and cnt(13,19) >= act and (not miss or (universal and cnt(22,0) >= miss))
                if quick:
                    # 本地规则：快速升星升到材料够用的最高级（最多10级），一级都不够才报材料不足。
                    while target > current + 1 and not affordable(target - current):
                        target -= 1
                steps = [rules[level] for level in range(current+1, target+1)]
                silver = sum(row['Silver'] for row in steps)
                shards = sum(row['HeroSui'] for row in steps)
                activation = sum(row['ActNum'] for row in steps)
                count = lambda kind, item: store.item_count(conn, uid, kind, item)
                own_shards = min(shards, count(22, hero_id))
                missing = shards-own_shards
                balance = store.silver(conn, uid)
                if balance is None or balance['silver'] < silver:
                    raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
                if count(13,19) < activation or (missing and (not universal or count(22,0) < missing)):
                    raise ValueError('LOCAL_MATERIAL_INSUFFICIENT')
                for kind,item,amount in [(22,hero_id,own_shards),(22,0,missing),(13,19,activation)]:
                    if amount:
                        store.sub_item(conn, uid, kind, item, amount)
                store.sub_money(conn, uid, 1, silver)
                store.set_star(conn, uid, hero_id, target)
                store.save_operation(conn, uid, operation_id, {'star_request': signature})
            hero = store.hero(conn, uid, hero_id)
            silver = store.silver(conn, uid)[0]
            items = store.star_items(conn, uid, hero_id)
            return {'hero':hero,'silver':silver,'items':items}

    # ---- 成长：觉醒（芯片）、天赋、熟练度。成本读原生表；无法从客户端确定的规则在注释中标明为本地规则。----
    def _count(self, conn, uid, kind, item):
        return store.item_count(conn, uid, kind, item)

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
        user = store.account(conn, uid)
        for (kind, item), num in merged.items():
            have = user[money[kind]] if kind in money else self._count(conn, uid, kind, item)
            if have < num:
                raise ValueError('LOCAL_BALANCE_INSUFFICIENT' if kind in money else 'LOCAL_MATERIAL_INSUFFICIENT')
        for (kind, item), num in merged.items():
            if kind in money:
                store.sub_money(conn, uid, kind, num)
            else:
                store.sub_item(conn, uid, kind, item, num)
        return list(merged)

    def _snapshot(self, conn, uid, keys):
        money = {1: 'silver', 2: 'gold', 10: 'diamonds'}
        user = store.account(conn, uid)
        out = []
        for kind, item in keys:
            out.append({'item_type': kind, 'item_id': item,
                        'count': user[money[kind]] if kind in money else self._count(conn, uid, kind, item)})
        return out

    def _replay(self, conn, uid, operation_id, signature):
        stored = store.operation(conn, uid, operation_id)
        if stored:
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
                hero = store.hero(conn, uid, hero_id)
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
                store.add_awake(conn, uid, hero_id, levels)
                store.save_operation(conn, uid, operation_id, {'request': signature, 'keys': sorted(keys)})
            stored = self._replay(conn, uid, operation_id, signature)
            hero = store.hero(conn, uid, hero_id)
            return {'hero': hero, 'items': self._snapshot(conn, uid, [tuple(k) for k in stored['keys']])}

    def _talent_tables(self, conn):
        store.ensure_talent_schema(conn)

    def talent_info(self, uid, hero_id):
        with self.connection() as conn:
            self._talent_tables(conn)
            levels = {r['Tid']: 1 for r in self.talent_rows}
            levels.update(store.talent_levels(conn, uid, hero_id))
            use = store.talent_use(conn, uid, hero_id)
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
                if not store.owns_hero(conn, uid, hero_id):
                    raise ValueError('LOCAL_HERO_UNAVAILABLE')
                level = store.talent_level(conn, uid, hero_id, tid)
                level = 1 if level is None else level
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
                store.set_talent_level(conn, uid, hero_id, tid, level + 1)
                store.save_operation(conn, uid, operation_id, {'request': signature, 'keys': sorted(keys)})
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
            store.clear_talent_use(conn, uid, hero_id)
            for slot, t in enumerate(sorted(tids, key=lambda x: groups[x])):
                store.set_talent_slot(conn, uid, hero_id, slot, t)

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
                store.clear_talent_use(conn, uid, hero_id)
                store.save_operation(conn, uid, operation_id, {'request': signature, 'keys': sorted(keys)})
            stored = self._replay(conn, uid, operation_id, signature)
            return {'items': self._snapshot(conn, uid, [tuple(k) for k in stored['keys']])}

    def reset_proficiency(self, uid, hero_id):
        """熟练度重置：原生请求只带英雄，成本未知，本地免费置0。"""
        with self.connection() as conn:
            if not store.reset_proficiency(conn, uid, hero_id):
                raise ValueError('LOCAL_HERO_UNAVAILABLE')

    def add_proficiency(self, uid, hero_id, amount):
        """对局结算后增加出战英雄熟练度（本地规则：每局固定值）。"""
        with self.connection() as conn:
            store.add_proficiency(conn, uid, hero_id, amount)
