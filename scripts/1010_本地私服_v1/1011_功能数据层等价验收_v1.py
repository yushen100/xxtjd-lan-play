"""左侧功能数据层拆分前后等价性：碎片融合、快捷兑换、玉石结晶、星卡（含跨天）、10钻升星、战斗宝箱、
限时活动（信息/兑换/任务/抽奖）、材料兑换。旧模块 = drafts/1011_功能数据层基线_v1/1011_features_v1.py。
同一存档、同一组调用、固定时间和随机数，比较每次返回和全部数据表。"""
import importlib.util
import json
import random
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / 'drafts/1011_功能数据层基线_v1/1011_features_v1.py'
DAY1 = 1791900000.0          # 2026-10-14 前后
DAY2 = DAY1 + 86400


def load(path, name):
    sp = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(sp)
    sys.modules[name] = m
    sp.loader.exec_module(m)
    return m


server = load(HERE / '1010_server_v1.py', 'feq_server')
T = server.TABLES
folder = ROOT / 'drafts/1011_功能数据层等价验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
clock = [DAY1]
real_time = time.time
time.time = lambda: clock[0]
import datetime as _dt
real_date = _dt.date


class FixedDate(real_date):
    @classmethod
    def today(cls):
        return real_date.fromtimestamp(clock[0])


_dt.date = FixedDate

try:
    seed_db = folder / 'seed.sqlite3'
    server.DB = seed_db
    server.record = lambda **kw: None
    random.seed(1)
    server.init_db()
    server.economy = server.economy_module.Economy(seed_db, T['StoreBaseVo'])
    uid = server.state('10001')['uid']
    fuse_id = next(r['Id'] for r in T['ChipFuseBaseVo'] if r['Luck'])
    easy_id = next(iter(r['Id'] for r in T['EasyConvertBaseVo']))
    astro_id = next(iter(r['Id'] for r in T['AstrologyBaseVo']))
    with server.connect_db() as c:
        c.execute('UPDATE accounts SET diamonds=900000, silver=9000000, gold=900000 WHERE uid=?', (uid,))
        need = {(13, 11), (13, 20), (13, 202), (13, 200), (13, 12), (13, 43)} | {(12, r['ChipId']) for r in T['ChipFuseBaseVo']}
        need |= {(r['CostType'], r['CostItemId']) for r in T['EasyConvertBaseVo'] if r['CostType'] not in (1, 2, 10)}
        need |= {(r['CostType'], r['Num']) for r in T['AstrologyBaseVo'] if r['CostType'] not in (1, 2, 10)}
        for ev in T['EventTypeBaseVo']:
            for part in str(ev['Item'] or '').split(';'):
                if part.count(',') == 2:
                    k, i, _ = (int(x) for x in part.split(','))
                    need.add((k, i))
        for r in T['EventTaskBaseVo']:
            if r['MonType'] not in (0, 1, 2, 10):
                need.add((r['MonType'], r['MonId']))
        for k, i in need:
            c.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=excluded.count', (uid, k, i, 5000000))
        # 本周有战绩，活动任务（打 N 局）有进度
        c.execute(server.BATTLE_RECORD_SQL)
        for n in range(6):
            c.execute('INSERT INTO battle_records(uid,room_id,battle_type,hero,skin,kill,dead,assist,win,mvp,multi,triple,quadra,penta,shuchu,tuijin,shengcun,zhiyuan,unix) VALUES(?,?,1,1,0,1,1,1,?,?,0,0,0,0,0,0,0,0,?)',
                      (uid, 7000 + n, n % 2, n % 3 == 0, int(DAY1) - 3600))
        c.execute("""CREATE TABLE IF NOT EXISTS local_rewards(room_id INTEGER, uid TEXT, silver INTEGER, rank_score INTEGER, detail TEXT,
                     day TEXT, week TEXT, mode INTEGER, win INTEGER, PRIMARY KEY(room_id, uid))""")
        for n in range(40):   # 今天 40 局匹配/排位（荣耀之路能领多档、日常任务有进度）
            c.execute("INSERT INTO local_rewards VALUES(?,?,0,0,'[]',?,'w',?,?)",
                      (7100 + n, uid, FixedDate.today().isoformat(), (11, 13, 23)[n % 3], int(n % 4 != 0)))

    def run(path, label):
        db = folder / f'{label}.sqlite3'
        with sqlite3.connect(seed_db) as src, sqlite3.connect(db) as dst:   # 用备份接口复制，含 WAL 中未落盘的写入
            src.backup(dst)
        fmod = load(path, f'feq_features_{label}')
        econ = server.economy_module.Economy(db, T['StoreBaseVo'])
        f = fmod.Features(econ, T); f2 = fmod.Features2(f); f3 = fmod.Features3(f2)
        ev = fmod.Events(f3); ex = fmod.Exchange(f2)
        out = []
        clock[0] = DAY1

        def call(name, fn, *a):
            random.seed(hash(name) % 100000 + len(out))
            try:
                r = fn(*a)
            except ValueError as exc:
                r = 'ERR:' + str(exc)
            out.append((name, json.loads(json.dumps(r, default=str, ensure_ascii=False))))

        call('login', f.on_login, uid)
        call('fuse_info', f2.fuse_info, uid)
        call('fuse1', f2.fuse, uid, fuse_id, 1); call('fuse10', f2.fuse, uid, fuse_id, 10)
        call('easy', f2.easy_convert, uid, easy_id, 2)
        call('crystal_free', f2.crystal_draw, uid, False); call('crystal_paid', f2.crystal_draw, uid, False); call('crystal10', f2.crystal_draw, uid, True)
        call('astro_info', f2.astro_info, uid); call('astro_set', f2.astro_set, uid, 'abc')
        for n in range(4):
            call(f'astro_award{n}', f2.astro_award, uid)
        call('astro_ex', f2.astro_exchange, uid, astro_id)
        call('onepay_info', f2.onepay_info, uid); call('onepay1', f2.onepay_buy, uid, 0); call('onepay2', f2.onepay_buy, uid, 1)
        for n in range(6):
            call(f'add_box{n}', f2.add_box, uid, n % 2 == 0, None, 8000 + n)
        call('box_info', f2.box_info, uid)
        boxes = f2.box_info(uid)['boxes']
        call('into_slot', f2.box_into_slot, uid, boxes[0][0]); call('into_slot_bad', f2.box_into_slot, uid, 999999)
        call('box_award_early', f2.box_award, uid, 1); call('box_award_empty', f2.box_award, uid, 1)
        call('box_delete', f2.box_delete, uid, boxes[1][0]); call('box_info2', f2.box_info, uid)
        weeks = ev.week_events()
        call('week', lambda: weeks)
        for key, eid, _, _ in weeks[:3]:
            call(f'ev_info{eid}', ev.info, uid, key)
            for t in ev.tasks.get(eid, [])[:6]:
                call(f'ev_take{t["Id"]}', ev.take, uid, key, t['Id'])
            call(f'ev_draw{eid}', ev.draw, uid, key, False); call(f'ev_draw10_{eid}', ev.draw, uid, key, True)
            call(f'ev_info2_{eid}', ev.info, uid, key)
        call('ev_closed', ev.info, uid, -12345)
        call('ex_actions', ex.actions); call('ex_cond', ex.conditions, uid)
        call('ex_take1', ex.take, uid, 1); call('ex_take1b', ex.take, uid, 1); call('ex_bad', ex.take, uid, 999)
        # 专属抽英雄 / 日常任务 / 周活跃宝箱 / 荣耀之路 / 签到（第二轮拆分）
        call('lot_info', f.lottery_info, uid)
        ltype = f.pool[0]['LotteryType']
        call('lot_free', f.lottery_draw, uid, ltype, 1); call('lot_paid', f.lottery_draw, uid, ltype, 1); call('lot10', f.lottery_draw, uid, ltype, 10)
        call('lot_bad', f.lottery_draw, uid, -1, 1)
        call('excl', f.exclusive_exchange, uid, next(iter(f.exchange)))
        call('daily_list', f.daily_list, uid)
        for tid in list(f.daily)[:12]:
            call(f'daily{tid}', f.daily_reward, uid, tid)
        call('daily_again', f.daily_reward, uid, list(f.daily)[0])
        for bid in f.week_box:
            call(f'wbox{bid}', f.weekly_box, uid, bid)
        call('daily_list2', f.daily_list, uid)
        call('glory_info', f3.glory_info, uid)
        for r in f3.glory[:6]:
            call(f'glory{r["Id"]}', f3.glory_reward, uid, r['Id'])
        call('glory_again', f3.glory_reward, uid, f3.glory[0]['Id'])
        call('sign_list', f3.sign_list, uid); call('sign', f3.sign_reward, uid); call('sign_again', f3.sign_reward, uid)
        call('sign_total', f3.sign_total_reward, uid, f3.sign_total[0]['Id'])
        clock[0] = DAY2   # 跨天：星卡免费次数重置、星屑清零；10钻升星可再领
        call('astro_info_d2', f2.astro_info, uid); call('astro_award_d2', f2.astro_award, uid)
        call('onepay_d2', f2.onepay_buy, uid, 0); call('ex_cond_d2', ex.conditions, uid)
        call('lot_info_d2', f.lottery_info, uid); call('lot_free_d2', f.lottery_draw, uid, ltype, 1)
        call('sign_d2', f3.sign_reward, uid); call('sign_list_d2', f3.sign_list, uid); call('daily_list_d2', f.daily_list, uid)
        dump = {}
        with sqlite3.connect(db) as c:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                dump[t] = sorted(json.dumps(list(r), ensure_ascii=False, default=str) for r in c.execute(f'SELECT * FROM "{t}"'))
        return out, dump

    old_out, old_db = run(BASE, 'old')
    new_out, new_db = run(HERE / '1011_features_v1.py', 'new')
finally:
    time.time = real_time
    _dt.date = real_date

bad_calls = [old_out[i][0] for i in range(min(len(old_out), len(new_out))) if old_out[i] != new_out[i]]
bad_tables = sorted(t for t in set(old_db) | set(new_db) if old_db.get(t) != new_db.get(t))
errs = [n for n, r in new_out if isinstance(r, str) and r.startswith('ERR:')]
result = {'passed': not bad_calls and not bad_tables and len(old_out) == len(new_out), 'calls': len(new_out),
          'call_mismatch': bad_calls, 'table_mismatch': bad_tables, 'expected_errors': errs,
          'tables_compared': len(new_db), 'baseline': str(BASE.relative_to(ROOT))}
(ROOT / 'content/1011_功能数据层等价验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert result['passed'], '左侧功能数据层拆分前后结果不一致'
assert len(new_out) - len(errs) >= 60, '成功的调用太少，场景没有覆盖到'
