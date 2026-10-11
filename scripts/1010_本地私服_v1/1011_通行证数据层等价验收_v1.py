"""通行证数据层拆分前后等价性。旧模块 = drafts/1011_通行证数据层基线_v1/1010_passport_v1.py。
信息、对局经验（含任务经验）、买等级、晋升优秀/完美、一键领奖、通行证商店（限购）、任务列表，跨周后再算，比较返回和全部表。"""
import datetime as _dt
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
BASE = ROOT / 'drafts/1011_通行证数据层基线_v1/1010_passport_v1.py'
clock = [1791900000.0]


def load(path, name):
    sp = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(sp)
    sys.modules[name] = m
    sp.loader.exec_module(m)
    return m


server = load(HERE / '1010_server_v1.py', 'peq_server')
T = server.TABLES
folder = ROOT / 'drafts/1011_通行证数据层等价验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
real_time, real_date, real_strftime = time.time, _dt.date, time.strftime
time.time = lambda: clock[0]
time.strftime = lambda fmt, t=None: real_strftime(fmt, time.localtime(clock[0]) if t is None else t)


class FixedDate(real_date):
    @classmethod
    def today(cls):
        return real_date.fromtimestamp(clock[0])


_dt.date = FixedDate
try:
    seed = folder / 'seed.sqlite3'
    server.DB = seed
    server.record = lambda **kw: None
    random.seed(1)
    server.init_db()
    server.economy = server.economy_module.Economy(seed, T['StoreBaseVo'])
    uid = server.state('10001')['uid']
    week0 = int(time.mktime((FixedDate.today() - _dt.timedelta(days=FixedDate.today().weekday())).timetuple()))
    with server.connect_db() as c:
        c.execute('UPDATE accounts SET diamonds=900000 WHERE uid=?', (uid,))
        c.execute("INSERT INTO native_items VALUES(?,13,38,500000) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=excluded.count", (uid,))
        c.execute(server.BATTLE_RECORD_SQL)
        for n in range(12):
            c.execute('INSERT INTO battle_records(uid,room_id,battle_type,hero,skin,kill,dead,assist,win,mvp,multi,triple,quadra,penta,shuchu,tuijin,shengcun,zhiyuan,unix) VALUES(?,?,?,1,0,?,1,1,?,0,0,0,0,0,0,0,0,0,?)',
                      (uid, 6000 + n, (11, 22, 1)[n % 3], 3 + n, int(n % 4 != 0), week0 + 3600 * (n + 1)))
    shop_limited = next((r['Id'] for r in T['PassportShopBaseVo'] if r['LimitedBuyTimes']), None)
    shop_free = next((r['Id'] for r in T['PassportShopBaseVo'] if not r['LimitedBuyTimes']), None)

    def run(path, label):
        db = folder / f'{label}.sqlite3'
        with sqlite3.connect(seed) as s, sqlite3.connect(db) as d:
            s.backup(d)
        mod = load(path, f'peq_passport_{label}')
        econ = server.economy_module.Economy(db, T['StoreBaseVo'])
        p = mod.Passport(econ, T)
        out = []
        clock[0] = 1791900000.0

        def call(name, fn, *a):
            try:
                r = fn(*a)
            except ValueError as exc:
                r = 'ERR:' + str(exc)
            out.append((name, json.loads(json.dumps(r, default=str, ensure_ascii=False))))

        call('info0', p.info, uid); call('tasks0', p.task_list, uid)
        for n in range(3):
            call(f'battle{n}', p.add_battle_exp, uid)
        call('buy1', p.buy_level, uid, 1); call('buy5', p.buy_level, uid, 5)
        call('take1', p.take_all, uid)
        call('promote_good', p.promote, uid); call('take2', p.take_all, uid)
        call('promote_perfect', p.promote, uid); call('promote_max', p.promote, uid); call('take3', p.take_all, uid)
        call('buy_max', p.buy_level, uid, 999); call('buy_over', p.buy_level, uid, 1)
        call('shop0', p.shop_info, uid)
        if shop_limited:
            for n in range(3):
                call(f'shop_lim{n}', p.shop_buy, uid, shop_limited, 1)
        if shop_free:
            call('shop_free', p.shop_buy, uid, shop_free, 3)
        call('shop_bad', p.shop_buy, uid, -1, 1); call('shop1', p.shop_info, uid)
        call('info1', p.info, uid); call('tasks1', p.task_list, uid)
        clock[0] += 7 * 86400   # 下一周：任务次数和周经验重新统计
        call('tasks_w2', p.task_list, uid); call('battle_w2', p.add_battle_exp, uid); call('info_w2', p.info, uid)
        dump = {}
        with sqlite3.connect(db) as c:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                dump[t] = sorted(json.dumps(list(r), ensure_ascii=False, default=str) for r in c.execute(f'SELECT * FROM "{t}"'))
        return out, dump

    old_out, old_db = run(BASE, 'old')
    new_out, new_db = run(HERE / '1010_passport_v1.py', 'new')
finally:
    time.time, time.strftime, _dt.date = real_time, real_strftime, real_date

bad_calls = [old_out[i][0] for i in range(min(len(old_out), len(new_out))) if old_out[i] != new_out[i]]
bad_tables = sorted(t for t in set(old_db) | set(new_db) if old_db.get(t) != new_db.get(t))
errs = [n for n, r in new_out if isinstance(r, str) and r.startswith('ERR:')]
result = {'passed': not bad_calls and not bad_tables and len(old_out) == len(new_out), 'calls': len(new_out),
          'call_mismatch': bad_calls, 'table_mismatch': bad_tables, 'errors': errs, 'tables_compared': len(new_db),
          'baseline': str(BASE.relative_to(ROOT))}
(ROOT / 'content/1011_通行证数据层等价验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert result['passed'], '通行证数据层拆分前后结果不一致'
assert len(new_out) - len(errs) >= 18, '成功调用太少，场景没覆盖到'
