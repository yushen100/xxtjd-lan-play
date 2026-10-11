"""登录补发拆分前后等价性：同一组账号场景，分别用拆分前的服务端（drafts/1011_登录补发基线_v1/）和
现在的服务端跑 state()，比较返回内容和数据库全部表。时间、随机数固定。"""
import importlib.util
import json
import random
import sqlite3
import sys
import time
import types
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / 'drafts/1011_登录补发基线_v1/1010_server_v1.py'
FIXED = 1791900000.0    # 2026-10 某时刻，上个赛季为 2026-09


def load(source_path, name):
    """按服务端真实位置加载（__file__ 指向 scripts 目录，保证同目录模块按现状导入）。"""
    mod = types.ModuleType(name)
    mod.__file__ = str(HERE / '1010_server_v1.py')
    sys.modules[name] = mod
    exec(compile(Path(source_path).read_text(encoding='utf-8'), str(source_path), 'exec'), mod.__dict__)
    return mod


def scenario(server, db):
    server.DB = db
    server.record = lambda **kw: None
    random.seed(1)
    server.init_db()
    server.economy = server.economy_module.Economy(db, server.TABLES['StoreBaseVo'])
    out = []
    real_state = server.state
    calls = iter(range(1000))

    class S:   # 每次登录前固定随机种子，两边抽到的限定英雄一致
        @staticmethod
        def state(account):
            random.seed(100 + next(calls))
            return real_state(account)
    server = types.SimpleNamespace(**{**vars(server), 'state': S.state})
    # 老号（全解锁）首次登录 + 重复登录
    out.append(server.state('10001')); out.append(server.state('10001'))
    # 新养成号首次登录（随机 3 个限定英雄）+ 重复登录
    random.seed(7); out.append(server.state('newbie')); out.append(server.state('newbie'))
    # 特殊补给名单账号：只发一次
    random.seed(8); out.append(server.state('wq')); out.append(server.state('wq'))
    # 机器人账号：不补发
    with server.connect_db() as c:
        c.execute("INSERT INTO accounts(account,uid,name,silver,gold,diamonds,friendship) VALUES('bot_1','local-bot-1','本地机器人1',0,0,0,0)")
    out.append(server.state('bot_1'))
    # 旧版养成号（无 growth_start_v2 记录）：补 1000 钻 + 开局
    with server.connect_db() as c:
        c.execute("INSERT INTO accounts(account,uid,name,silver,gold,diamonds,friendship) VALUES('oldg','local-oldg','玩家oldg',0,0,0,0)")
        c.execute('CREATE TABLE IF NOT EXISTS growth_accounts(uid TEXT PRIMARY KEY, time INTEGER)')
        c.execute("INSERT INTO growth_accounts VALUES('local-oldg',1)")
        for v in ('', '_v2', '_v3', '_v4', '_v5', '_v6'):
            c.execute(f'CREATE TABLE IF NOT EXISTS starter_grants{v}(uid TEXT PRIMARY KEY, time INTEGER)')
    random.seed(9); out.append(server.state('oldg'))
    # 上赛季（2026-09）排位 30 胜：登录发赛季结算邮件 + 赛季宝珠
    with server.connect_db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS local_rewards(room_id INTEGER, uid TEXT, silver INTEGER, rank_score INTEGER, detail TEXT, day TEXT, week TEXT, mode INTEGER, win INTEGER)')
        for i in range(30):
            c.execute("INSERT INTO local_rewards VALUES(?,?,0,0,'[]','2026-09-15','2026-W37',11,1)", (5000 + i, 'local-10002'))
    out.append(server.state('10002')); out.append(server.state('10002'))
    try:
        server.state('bad name!')
        out.append('accepted')
    except ValueError as exc:
        out.append(str(exc))
    return out


def dump(db):
    res = {}
    with sqlite3.connect(db) as c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            res[t] = sorted(json.dumps(list(r), ensure_ascii=False, default=str) for r in c.execute(f'SELECT * FROM "{t}"'))
    return res


folder = ROOT / 'drafts/1011_登录补发等价验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
results = {}
real_time = time.time
time.time = lambda: FIXED
try:
    for label, src in (('old', BASE), ('new', HERE / '1010_server_v1.py')):
        server = load(src, f'login_eq_{label}')
        if label == 'new':   # 基线之后用户新加的材料补给批次不参与对比（单独验证）
            server.grants_module.MATERIAL_REFILLS[:] = []
        db = folder / f'{label}.sqlite3'
        results[label] = (json.loads(json.dumps(scenario(server, db), ensure_ascii=False, default=str)), dump(db))
finally:
    time.time = real_time
(old_s, old_db), (new_s, new_db) = results['old'], results['new']
bad_states = [i for i, (a, b) in enumerate(zip(old_s, new_s)) if a != b]
bad_tables = sorted(t for t in set(old_db) | set(new_db) if old_db.get(t) != new_db.get(t))
mails = [m for m in new_db.get('mails', [])]
result = {'passed': not bad_states and not bad_tables and len(old_s) == len(new_s), 'logins': len(new_s),
          'state_mismatch': bad_states, 'table_mismatch': bad_tables, 'tables_compared': len(new_db),
          'season_mails': len(mails), 'baseline': str(BASE.relative_to(ROOT))}
(ROOT / 'content/1011_登录补发等价验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert result['passed'], '登录补发拆分前后结果不一致'
assert result['season_mails'] >= 1, '场景没有覆盖赛季结算邮件'
