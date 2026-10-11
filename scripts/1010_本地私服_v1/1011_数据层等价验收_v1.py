"""数据访问层拆分前后等价性：同一份存档、同一组请求，旧大厅处理器与新处理器
的返回报文和数据库最终内容必须完全一致。基线文件：drafts/1011_数据层基线_v1/。"""
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
BASE = ROOT / 'drafts/1011_数据层基线_v1'
spec = importlib.util.spec_from_file_location('eq_server', HERE / '1010_server_v1.py')
server = importlib.util.module_from_spec(spec); sys.modules[spec.name] = server
spec.loader.exec_module(server)
folder = ROOT / 'drafts/1011_数据层等价验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
server.DB = folder / 'seed.sqlite3'
server.record = lambda **kw: None
server.init_db()
server.economy = server.economy_module.Economy(server.DB, server.TABLES['StoreBaseVo'])
server.bridge = server.rooms_module.NativeRooms(server.pb, server.DB, server.TABLES, lambda: True)
uid = server.state('10001')['uid']
friend = server.state('10002')['uid']
T = server.TABLES

# —— 准备存档：余额、折扣券、带物品的邮件、战绩 ——
silver_hero = next(h for h in T['HeroBaseVo'] if h['IsBuy'] == 0 and h['Silver'] > 0)
limited = next(h for h in T['HeroBaseVo'] if h['IsBuy'] in (3, 5, 6) and h['Diam'])
skin = next(k for k in T['SkinIdBaseVo'] if k['Type'] not in (1, 4, 6, 7, 8, 9) and k['Num'] > 0)
season_row = sorted(T['SeasonShopBaseVo'], key=lambda r: r['Term'])[0]

def seed():
    """装配完成后再写测试存档（装配过程会补发老号资源）。"""
    with server.connect_db() as c:
        c.execute('DELETE FROM heroes WHERE uid=? AND hero_id IN (?,?,?)', (uid, silver_hero['Id'], limited['Id'], skin['HeroId']))
        c.execute('UPDATE accounts SET diamonds=50000, silver=900000, gold=90000 WHERE uid=?', (uid,))
        c.execute('CREATE TABLE IF NOT EXISTS hero_coupons(uid TEXT PRIMARY KEY, num INTEGER)')
        c.execute('INSERT OR REPLACE INTO hero_coupons VALUES(?,1)', (uid,))
        server.send_mail(c, uid, '系统', '测试邮件', '带物品', [(1, 0, 1000), (6, skin['Id'], 1)])
        server.send_mail(c, uid, '系统', '测试邮件2', '带物品', [(10, 0, 5)])
        c.execute("INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=excluded.count",
                  (uid, server.SEASON_ORB[0], server.SEASON_ORB[1], 99999))


def wire(db):
    """按正式开服的方式装配经济、房间和功能模块，全部指向 db。"""
    server.DB = db
    server.economy = server.economy_module.Economy(db, T['StoreBaseVo'])
    b = server.rooms_module.NativeRooms(server.pb, db, T, lambda: True)
    load = lambda n, f: (lambda sp: (sp, importlib.util.module_from_spec(sp)))(importlib.util.spec_from_file_location(n, HERE / f))
    sp, fmod = load('eq_features', '1011_features_v1.py'); sp.loader.exec_module(fmod)
    sp, pmod = load('eq_passport', '1010_passport_v1.py'); sp.loader.exec_module(pmod)
    b.passport = pmod.Passport(server.economy, T)
    b.features = fmod.Features(server.economy, T)
    b.features2 = fmod.Features2(b.features)
    b.features3 = fmod.Features3(b.features2)
    b.events = fmod.Events(b.features3)
    sp, frmod = load('eq_friends', '1011_friends_v1.py'); sp.loader.exec_module(frmod)
    b.friends = frmod.Friends(db, T, server.economy)
    b.friends_mod = frmod
    server.bridge = b
    return server.lobby_services()

def F(path, value):
    def fill(m):
        obj = m.request
        for p in path[:-1]:
            obj = getattr(obj, p)
        if isinstance(value, list):
            getattr(obj, path[-1]).extend(value)
        else:
            setattr(obj, path[-1], value)
    return fill

STEPS = [
    (3003, [F(['heroBuyRequest', 'heroId'], silver_hero['Id']), F(['heroBuyRequest', 'itemType'], 1)]),
    (3003, [F(['heroBuyRequest', 'heroId'], limited['Id']), F(['heroBuyRequest', 'itemType'], 10)]),
    (3003, [F(['heroBuyRequest', 'heroId'], limited['Id']), F(['heroBuyRequest', 'itemType'], 10)]),   # 重复购买被拒
    (3004, [F(['skinBuyRequest', 'skinId'], skin['Id'])]),                                              # 无英雄被拒
    (3043, []),
    (3044, [F(['mailReadRequest', 'mailId'], 1)]),
    (3045, [F(['mailRewardRequest', 'mailIdList'], [1, 2])]),
    (3046, [F(['mailDelRequest', 'mailIdList'], [2])]),
    (3086, [F(['skinBuyForFriendRequest', 'skinId'], skin['Id']), F(['skinBuyForFriendRequest', 'friendUid'], friend)]),
    (3086, [F(['skinBuyForFriendRequest', 'skinId'], skin['Id']), F(['skinBuyForFriendRequest', 'friendUid'], 'nobody')]),
    (3391, []),
    (3392, [F(['seasonShopBuyRequest', 'id'], season_row['Id'])]),
    (3392, [F(['seasonShopBuyRequest', 'id'], season_row['Id'])]),
    (3391, []),
    (3269, []),
    (3271, []),
    (3271, []),
    (3270, [F(['upOfficialRequest', 'isUseDiam'], True)]),
    (3053, []),
    (3051, []),
    (3052, []),
    (3243, []),
    (3021, []),
    (3050, [F(['userInfoBaseRequest', 'uid'], friend)]),
    (3055, [F(['chatSendRequest', 'chatType'], 0), F(['chatSendRequest', 'text'], '世界')]),
    (3055, [F(['chatSendRequest', 'chatType'], 2), F(['chatSendRequest', 'text'], '队伍')]),
    (3055, [F(['chatSendRequest', 'chatType'], 1), F(['chatSendRequest', 'text'], '私聊'), F(['chatSendRequest', 'privateUid'], friend)]),
]


class FakePeer:
    """在线好友的连接替身：记录收到的推送，第二个模拟断线。"""
    def __init__(self, log, broken=False):
        self.log, self.broken = log, broken

    def send_message(self, m):
        if self.broken:
            raise OSError('断线')
        self.log.append(m.SerializeToString(deterministic=True).hex())

def load_old(name):
    sp = importlib.util.spec_from_file_location('old_' + name, BASE / f'1011_lobby_{name}_v1.py')
    mod = importlib.util.module_from_spec(sp); sp.loader.exec_module(mod)
    return mod

OLD = {n: load_old(n) for n in ('account', 'friends', 'shop', 'activities', 'mail', 'growth')}

def handler(kind, old):
    fn = server.lobby_module.ROUTES[kind]
    if not old:
        return fn
    cat = fn.__module__.split('_', 1)[1]
    return getattr(OLD[cat], fn.__name__) if cat in OLD else fn

def dump(db):
    out = {}
    with sqlite3.connect(db) as c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            out[t] = sorted(json.dumps(list(r), ensure_ascii=False, default=lambda b: b.hex()) for r in c.execute(f'SELECT * FROM "{t}"'))
    return out

def run(old):
    db = folder / ('old.sqlite3' if old else 'new.sqlite3')
    shutil.copy(folder / 'seed.sqlite3', db)
    replies = []
    real_time = time.time
    time.time = lambda: 1791800000.0   # 固定时间，邮件时间戳等两边一致
    try:
        services = wire(db)
        pushes = []
        server.bridge.game_peers[friend] = FakePeer(pushes)
        server.bridge.game_peers['local-offline'] = FakePeer(pushes, broken=True)
        if old:
            services.bridge = server.bridge   # 基线处理器仍按旧方式直接取房间对象
        seed()
        for i, (kind, fills) in enumerate(STEPS):
            random.seed(1000 + i)
            m = server.pb.Message(type=kind, seq=i + 1)
            for f in fills:
                f(m)
            ans, _ = handler(kind, old)(services, m, '10001', uid, lambda k: f'eq:{k}:{i}')
            replies.append((kind, ans.response.errcode, ans.SerializeToString(deterministic=True).hex(), list(pushes)))
    finally:
        time.time = real_time
    return replies, dump(db)

old_r, old_db = run(True)
new_r, new_db = run(False)
diff_replies = [i for i, (a, b) in enumerate(zip(old_r, new_r)) if a != b]
diff_tables = [t for t in set(old_db) | set(new_db) if old_db.get(t) != new_db.get(t)]
result = {'passed': not diff_replies and not diff_tables, 'steps': len(STEPS),
          'message_types': sorted({k for k, _ in STEPS}),
          'errcodes': [[k, e] for k, e, _, _ in new_r if e],
          'pushes': len(new_r[-1][3]),
          'reply_mismatch_steps': diff_replies, 'table_mismatch': diff_tables,
          'baseline': str(BASE.relative_to(ROOT))}
(ROOT / 'content/1011_数据层等价验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert result['passed'], '数据访问层拆分前后结果不一致'
