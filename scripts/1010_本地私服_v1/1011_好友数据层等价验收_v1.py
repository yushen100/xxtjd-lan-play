"""好友助力/搭档/搭档任务拆分前后等价性。旧模块 = drafts/1011_好友数据层基线_v1/1011_friends_v1.py。
三个账号、固定时间：设助力英雄、借英雄（含上限、重复、借自己、对方没设）、开局用掉、搭档申请/拒绝/同意/解除、
搭档任务列表和领奖（含条件未达成、重复领），比较每次返回和全部数据表。"""
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
BASE = ROOT / 'drafts/1011_好友数据层基线_v1/1011_friends_v1.py'
NOW = 1791900000.0


def load(path, name):
    sp = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(sp)
    sys.modules[name] = m
    sp.loader.exec_module(m)
    return m


server = load(HERE / '1010_server_v1.py', 'freq_server')
T = server.TABLES
folder = ROOT / 'drafts/1011_好友数据层等价验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
real_time, real_date = time.time, _dt.date
time.time = lambda: NOW


class FixedDate(real_date):
    @classmethod
    def today(cls):
        return real_date.fromtimestamp(NOW)


_dt.date = FixedDate
try:
    seed = folder / 'seed.sqlite3'
    server.DB = seed
    server.record = lambda **kw: None
    random.seed(1)
    server.init_db()
    server.economy = server.economy_module.Economy(seed, T['StoreBaseVo'])
    uids = [server.state(a)['uid'] for a in ('10001', '10002', '10003', '10004', '10005', '10006', '10007')]
    a, b, c3 = uids[:3]
    heroes = {}
    with server.connect_db() as c:
        for u in uids:
            heroes[u] = [r[0] for r in c.execute('SELECT hero_id FROM heroes WHERE uid=? ORDER BY hero_id LIMIT 2', (u,))]
        c.execute(server.BATTLE_RECORD_SQL)
        c.execute("""CREATE TABLE IF NOT EXISTS local_rewards(room_id INTEGER, uid TEXT, silver INTEGER, rank_score INTEGER, detail TEXT,
                     day TEXT, week TEXT, mode INTEGER, win INTEGER, PRIMARY KEY(room_id, uid))""")
        today0 = int(time.mktime(FixedDate.today().timetuple()))
        for n in range(4):   # a 和 b 今天同局 4 场（2 场排位），a 拿 2 次 MVP
            for u in (a, b):
                c.execute('INSERT INTO battle_records(uid,room_id,battle_type,hero,skin,kill,dead,assist,win,mvp,multi,triple,quadra,penta,shuchu,tuijin,shengcun,zhiyuan,unix) VALUES(?,?,?,1,0,1,1,1,1,?,0,0,0,0,0,0,0,0,?)',
                          (u, 9000 + n, 1 if n < 2 else 4, int(u == a and n % 2 == 0), today0 + 3600 + n))
                c.execute("INSERT INTO local_rewards VALUES(?,?,0,0,'[]',?,?,?,1)", (9000 + n, u, FixedDate.today().isoformat(), 'w', 11 if n >= 2 else 2))
    rnd = FixedDate.today().isoweekday()
    task_ids = [r['Id'] for r in sorted((r for r in T['FriendTaskBaseVo'] if r['Round'] == rnd), key=lambda r: r['Sort'])]

    def run(path, label):
        db = folder / f'{label}.sqlite3'
        with sqlite3.connect(seed) as s, sqlite3.connect(db) as d:
            s.backup(d)
        mod = load(path, f'freq_friends_{label}')
        econ = server.economy_module.Economy(db, T['StoreBaseVo'])
        fr = mod.Friends(db, T, econ)
        out = []

        def call(name, fn, *args):
            try:
                r = fn(*args)
            except ValueError as exc:
                r = 'ERR:' + str(exc)
            out.append((name, json.loads(json.dumps(r, default=str, ensure_ascii=False))))

        call('page0', fr.page, a)
        call('borrow_unset', fr.borrow, a, b)
        for u in uids:
            call(f'set_{u}', fr.set_hero, u, heroes[u][0])
        call('set_bad', fr.set_hero, a, 999999)
        call('borrow_self', fr.borrow, a, a)
        for u in uids[1:]:
            call(f'borrow_{u}', fr.borrow, a, u)            # 第 6 个超上限
        call('borrow_again', fr.borrow, a, b)
        call('unused', fr.borrowed_unused, a, heroes[b][0]); call('unused_from', fr.borrowed_unused, a, heroes[b][0], b)
        call('use', fr.use_borrowed, a, heroes[b][0], b); call('unused_after', fr.borrowed_unused, a, heroes[b][0], b)
        call('page_a', fr.page, a); call('page_b', fr.page, b)
        call('apply_self', fr.apply, a, a); call('apply_ghost', fr.apply, a, 'local-nobody')
        call('apply', fr.apply, a, b); call('apply_c', fr.apply, c3, b); call('list_b', fr.apply_list, b)
        call('reject_c', fr.reply, b, c3, False); call('agree_a', fr.reply, b, a, True)
        call('apply_again', fr.apply, a, c3); call('list_b2', fr.apply_list, b)
        call('tasks_a', fr.task_list, a); call('tasks_c', fr.task_list, c3)
        for t in task_ids:
            call(f'reward_a_{t}', fr.task_reward, a, t); call(f'reward_a2_{t}', fr.task_reward, a, t); call(f'reward_c_{t}', fr.task_reward, c3, t)
        call('reward_bad', fr.task_reward, a, -1)
        call('delete', fr.delete, a); call('tasks_a2', fr.task_list, a)
        dump = {}
        with sqlite3.connect(db) as c:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                dump[t] = sorted(json.dumps(list(r), ensure_ascii=False, default=str) for r in c.execute(f'SELECT * FROM "{t}"'))
        return out, dump

    old_out, old_db = run(BASE, 'old')
    new_out, new_db = run(HERE / '1011_friends_v1.py', 'new')
finally:
    time.time, _dt.date = real_time, real_date

bad_calls = [old_out[i][0] for i in range(min(len(old_out), len(new_out))) if old_out[i] != new_out[i]]
bad_tables = sorted(t for t in set(old_db) | set(new_db) if old_db.get(t) != new_db.get(t))
errs = [n for n, r in new_out if isinstance(r, str) and r.startswith('ERR:')]
result = {'passed': not bad_calls and not bad_tables and len(old_out) == len(new_out), 'calls': len(new_out),
          'call_mismatch': bad_calls, 'table_mismatch': bad_tables, 'errors': errs, 'tables_compared': len(new_db),
          'baseline': str(BASE.relative_to(ROOT))}
(ROOT / 'content/1011_好友数据层等价验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert result['passed'], '好友数据层拆分前后结果不一致'
assert len(new_out) - len(errs) >= 25, '成功调用太少，场景没覆盖到'
