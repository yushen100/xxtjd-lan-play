"""经济/成长数据层拆分前后等价性。旧模块 = drafts/1011_经济数据层基线_v1/1010_economy_v1.py。
商城（银币/金币/钻石/军功/道具支付、宝箱、重试同一请求号、冲突请求号、余额不足）、升星（单级/快速/通用碎片）、
觉醒、天赋（信息/升级/装配/重置）、好友商店、熟练度、重复英雄/皮肤发放（老号与养成号），比较返回和全部表。"""
import importlib.util
import json
import random
import sqlite3
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / 'drafts/1011_经济数据层基线_v1/1010_economy_v1.py'


def load(path, name):
    sp = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(sp)
    sys.modules[name] = m
    sp.loader.exec_module(m)
    return m


server = load(HERE / '1010_server_v1.py', 'eeq_server')
T = server.TABLES
folder = ROOT / 'drafts/1011_经济数据层等价验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
real_time = time.time
time.time = lambda: 1791900000.0
try:
    seed = folder / 'seed.sqlite3'
    server.DB = seed
    server.record = lambda **kw: None
    random.seed(1)
    server.init_db()
    server.economy = server.economy_module.Economy(seed, T['StoreBaseVo'])
    old_uid = server.state('10001')['uid']
    with server.connect_db() as c:   # 10001 作为老号（不在养成号名单）
        c.execute('DELETE FROM growth_accounts WHERE uid=?', (old_uid,))
    new_uid = server.state('newbie')['uid']
    with server.connect_db() as c:
        for u in (old_uid, new_uid):
            c.execute('UPDATE accounts SET silver=9000000, gold=900000, diamonds=900000, rank_score=900000 WHERE uid=?', (u,))
            for k, i in [(22, 0), (13, 19), (41, 0), (43, 0), (13, 35), (13, 211), (13, 220)] + [(22, h) for h in range(1, 140)] + [(41, h) for h in range(1, 140)] + [(43, h) for h in range(1, 140)]:
                c.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=excluded.count', (u, k, i, 900000))
        hero_old = c.execute('SELECT hero_id FROM heroes WHERE uid=? ORDER BY hero_id LIMIT 1', (old_uid,)).fetchone()[0]
    stores = T['StoreBaseVo']
    pick = lambda f: next((r['Id'] for r in stores if f(r)), None)
    plan = [
        ('silver', pick(lambda r: r['Silver1'] and r['ItemType'] in (13, 22, 43) and not r['IsBox']), 1),
        ('gold', pick(lambda r: r['Gold1'] and r['ItemType'] in (13, 22, 43, 41) and not r['IsBox']), 2),
        ('diam', pick(lambda r: r['Diam1'] and r['ItemType'] in (13, 22, 43, 16, 37) and not r['IsBox']), 10),
        ('rank', pick(lambda r: r['RankScore'] and not r['IsBox']), 14),
        ('box', pick(lambda r: r['ItemType'] == 11 and r['IsBox'] and (r['Diam1'] or r['Silver1'] or r['Gold1'])), None),
        ('item', pick(lambda r: r['CostItemNum'] and not (r['Silver1'] or r['Gold1'] or r['Diam1'])), None),
    ]
    valid = {k['HeroId'] for k in T['SkinIdBaseVo'] if k['Type'] == 1}
    limited = next(h['Id'] for h in T['HeroBaseVo'] if h['IsBuy'] in (3, 5, 6) and h['Diam'] and h['Id'] in valid)
    skin = next(k['Id'] for k in T['SkinIdBaseVo'] if k['Type'] not in (1, 4, 6, 7, 8, 9) and k['IsOpen'] == 1)

    def run(path, label):
        db = folder / f'{label}.sqlite3'
        with sqlite3.connect(seed) as s, sqlite3.connect(db) as d:
            s.backup(d)
        mod = load(path, f'eeq_econ_{label}')
        e = mod.Economy(db, stores)
        e.talent_rows, e.gift_rows, e.hero_rows = T['TalentBaseVo'], T['GiftBaseVo'], T['HeroBaseVo']
        e.is_growth = lambda u: u == new_uid
        e.valid_heroes = {k['HeroId'] for k in T['SkinIdBaseVo'] if k['Type'] == 1}
        e.default_skin = {k['HeroId']: k['Id'] for k in T['SkinIdBaseVo'] if k['Type'] == 1}
        out = []

        def call(name, fn, *a):
            random.seed(len(out) + 7)
            try:
                r = fn(*a)
            except ValueError as exc:
                r = 'ERR:' + str(exc)
            out.append((name, json.loads(json.dumps(r, default=str, ensure_ascii=False))))

        for u in (old_uid, new_uid):
            for tag, sid, pay in plan:
                if sid is None:
                    continue
                row = next(r for r in stores if r['Id'] == sid)
                pay = pay or (10 if row['Diam1'] else 1 if row['Silver1'] else 2 if row['Gold1'] else 1)
                call(f'buy_{tag}_{u}', e.buy, u, sid, 2, pay, f'op-{tag}-{u}')
                call(f'retry_{tag}_{u}', e.buy, u, sid, 2, pay, f'op-{tag}-{u}')
                call(f'conflict_{tag}_{u}', e.buy, u, sid, 3, pay, f'op-{tag}-{u}')
            call(f'counts_{u}', e.counts, u)
            call(f'items_{u}', e.items, u, 13)
        call('bad_pay', e.buy, old_uid, plan[0][1], 1, 99, 'op-bad')
        call('star1', e.upgrade_star, old_uid, hero_old, False, False, 'op-s1', T['HeroStarBaseVo'])
        call('star1_retry', e.upgrade_star, old_uid, hero_old, False, False, 'op-s1', T['HeroStarBaseVo'])
        call('star_quick', e.upgrade_star, old_uid, hero_old, True, True, 'op-s2', T['HeroStarBaseVo'])
        call('star_none', e.upgrade_star, old_uid, 999999, True, True, 'op-s3', T['HeroStarBaseVo'])
        call('awake', e.awake_level_up, old_uid, hero_old, True, False, False, 'op-a1', T['ICCostBaseVo'])
        call('awake_quick', e.awake_level_up, old_uid, hero_old, True, True, False, 'op-a2', T['ICCostBaseVo'])
        call('awake_diam', e.awake_level_up, old_uid, hero_old, True, True, 5, 'op-a3', T['ICCostBaseVo'])
        tids = sorted({r['Tid'] for r in T['TalentBaseVo']})
        call('talent_info', e.talent_info, old_uid, hero_old)
        call('talent_up', e.talent_lv_up, old_uid, hero_old, tids[0], True, 'op-t1', T['TalentBaseVo'])
        call('talent_up2', e.talent_lv_up, old_uid, hero_old, tids[0], False, 'op-t2', T['TalentBaseVo'])
        groups = {}
        for r in T['TalentBaseVo']:
            groups.setdefault(r['Group'], r['Tid'])
        call('talent_use', e.talent_use, old_uid, hero_old, list(groups.values())[:5], T['TalentBaseVo'])
        call('talent_info2', e.talent_info, old_uid, hero_old)
        cost = next(r['stringValue'] for r in T['VariableBaseVo'] if r['Name'] == 'Talent')
        call('talent_clear', e.talent_clear, old_uid, hero_old, 'op-t3', cost)
        fs = T['FriendShopBaseVo'][0]['Id']
        call('friend_shop', e.friend_shop_buy, old_uid, fs, 2, T['FriendShopBaseVo'])
        call('prof_add', e.add_proficiency, old_uid, hero_old, 30); call('prof_reset', e.reset_proficiency, old_uid, hero_old); call('prof_add2', e.add_proficiency, old_uid, hero_old, 7)
        call('prof_bad', e.reset_proficiency, old_uid, 999999)

        def grant(u, k, i, n):
            with e.connection() as conn:
                return e._grant(conn, u, k, i, n)
        for u in (old_uid, new_uid):
            call(f'g_hero_{u}', grant, u, 5, limited, 1); call(f'g_hero_dup_{u}', grant, u, 5, limited, 1)
            call(f'g_skin_{u}', grant, u, 6, skin, 1); call(f'g_skin_dup_{u}', grant, u, 6, skin, 1)
            call(f'g_bad_hero_{u}', grant, u, 5, 999999, 1); call(f'g_item_{u}', grant, u, 13, 19, 3)
        dump = {}
        with sqlite3.connect(db) as c:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                dump[t] = sorted(json.dumps(list(r), ensure_ascii=False, default=str) for r in c.execute(f'SELECT * FROM "{t}"'))
        return out, dump

    old_out, old_db = run(BASE, 'old')
    new_out, new_db = run(HERE / '1010_economy_v1.py', 'new')
finally:
    time.time = real_time

bad_calls = [old_out[i][0] for i in range(min(len(old_out), len(new_out))) if old_out[i] != new_out[i]]
bad_tables = sorted(t for t in set(old_db) | set(new_db) if old_db.get(t) != new_db.get(t))
errs = [n for n, r in new_out if isinstance(r, str) and r.startswith('ERR:')]
result = {'passed': not bad_calls and not bad_tables and len(old_out) == len(new_out), 'calls': len(new_out),
          'call_mismatch': bad_calls, 'table_mismatch': bad_tables, 'errors': errs, 'tables_compared': len(new_db),
          'baseline': str(BASE.relative_to(ROOT))}
(ROOT / 'content/1011_经济数据层等价验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert result['passed'], '经济数据层拆分前后结果不一致'
assert len(new_out) - len(errs) >= 40, '成功调用太少，场景没覆盖到'
