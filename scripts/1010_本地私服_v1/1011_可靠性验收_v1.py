"""独立数据库验证结算事务与战报规则，保留验收证据。"""
import importlib.util
import json
import sqlite3
import sys
import uuid
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

server = load('reliability_server', '1010_server_v1.py')
folder = ROOT / 'drafts/1011_可靠性验收_v1' / uuid.uuid4().hex
folder.mkdir(parents=True)
server.DB = folder / '角色.sqlite3'
server.record = lambda **kw: None
server.init_db()
e = server.economy_module.Economy(server.DB, server.TABLES['StoreBaseVo'])
f = load('reliability_features', '1011_features_v1.py')
p = load('reliability_passport', '1010_passport_v1.py')
c = load('reliability_crosscheck', '1011_crosscheck_v1.py')
bridge = server.rooms_module.NativeRooms(server.pb, server.DB, server.TABLES, lambda: True)
bridge.features2 = f.Features2(f.Features(e, server.TABLES))
bridge.passport = p.Passport(e, server.TABLES)
bridge.crosscheck = c.CrossCheck(server.pb, folder / '核对.jsonl', bridge.mail, bridge.revoke_room_reward)
server.economy = e
for account in ('qa1', 'qa2', 'qa3'):
    server.login_response(account, server.pb.Message(type=3000))
uid = 'local-qa1'
room = SimpleNamespace(room_id=9001, mode=3, members=[uid], leader=uid)
bridge.seats[9001] = {uid: [1, 1]}
req = server.pb.Message().request.battleEndRequest
req.isWin = True

def snapshot():
    with sqlite3.connect(server.DB) as conn:
        return {name: conn.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall()
                for name in ('accounts', 'native_items', 'local_rewards', 'battle_boxes', 'passport', 'room_extras')}

passed = []
bridge.settle(room, uid, req)
before = snapshot()
bridge.settle(room, uid, req)
assert snapshot() == before
passed.append('重复结算不重复发货币、材料、宝箱、通行证经验')

room.room_id = 9002
bridge.seats[9002] = {uid: [1, 1]}
original = bridge.passport.add_battle_exp
def fail(*args, **kwargs):
    raise RuntimeError('故障注入')
bridge.passport.add_battle_exp = fail
try:
    bridge.settle(room, uid, req)
    raise AssertionError('故障未向上传递')
except RuntimeError:
    pass
assert snapshot() == before
with sqlite3.connect(server.DB) as conn:
    assert not conn.execute('SELECT 1 FROM battle_records WHERE room_id=9002').fetchone()
    assert not conn.execute('SELECT 1 FROM reward_log WHERE room_id=9002').fetchone()
bridge.passport.add_battle_exp = original
bridge.settle(room, uid, req)
passed.append('最后阶段故障整笔回滚，重试只提交一次')

with sqlite3.connect(server.DB) as conn:
    conn.execute('UPDATE accounts SET silver=3 WHERE uid=?', (uid,))
    bridge.deduct_reward(conn, uid, {'silver': 10})
    assert conn.execute('SELECT silver FROM accounts WHERE uid=?', (uid,)).fetchone()[0] == 0
    assert conn.execute('SELECT amount FROM reward_recovery_pending WHERE uid=? AND asset=?', (uid, json.dumps('silver'))).fetchone()[0] == 7
passed.append('已花掉的奖励记录待回收金额，不静默丢失')

def report(team):
    r = server.pb.Message().request.battleEndRequest
    r.record.winTeam = team
    for account in ('qa1', 'qa2', 'qa3'):
        r.record.battleRecordUserList.add(uid='local-'+account, tid=1, score=10)
    return r

with sqlite3.connect(server.DB) as conn:
    roster = ['local-qa1', 'local-qa2', 'local-qa3']
    bridge.crosscheck.before_settle(conn, 9100, roster[0], report(1), roster)
    bridge.crosscheck.before_settle(conn, 9100, roster[1], report(2), roster)
    assert not bridge.crosscheck.is_disputed(conn, 9100)
    bridge.crosscheck.before_settle(conn, 9100, roster[2], report(1), roster)
    assert not bridge.crosscheck.is_disputed(conn, 9100)
    bridge.crosscheck.before_settle(conn, 9101, roster[0], report(1), roster[:2])
    bridge.crosscheck.before_settle(conn, 9101, roster[1], report(2), roster[:2])
    assert bridge.crosscheck.is_disputed(conn, 9101)
passed.append('等真人战报报齐：2比1多数通过，1比1最终平票记争议')

with e.connection() as conn:
    bridge.features2._box_tables(conn)
    one = bridge.features2.add_box(uid, True, conn=conn, room_id=9200)
    two = bridge.features2.add_box(uid, True, conn=conn, room_id=9201)
    conn.execute('UPDATE battle_boxes SET box_type=? WHERE id=?', (one[1], two[0]))
bridge.features2.box_into_slot(uid, one[0])
bridge.features2.box_into_slot(uid, two[0])
with e.connection() as conn:
    conn.execute('INSERT INTO room_extras VALUES(?,?,?,?,?)', (9200, uid, one[0], one[1], 0))
    bridge.revoke_room_reward(conn, 9200, uid, {})
    assert not conn.execute('SELECT 1 FROM box_slots WHERE uid=? AND slot=1', (uid,)).fetchone()
    assert conn.execute('SELECT 1 FROM box_slots WHERE uid=? AND slot=2', (uid,)).fetchone()
passed.append('同类型宝箱只回收本局所属槽位，保留其他局宝箱')

for ip in ('127.0.0.1', '192.168.1.2', '10.0.0.2', '172.16.0.2', '26.1.2.3', '100.64.0.1', '100.127.255.254'):
    assert server.is_lan(ip), ip
for ip in ('8.8.8.8', '100.63.255.255', '100.128.0.1', '26x', '172.32.0.1'):
    assert not server.is_lan(ip), ip
passed.append('局域网、Radmin、Tailscale地址边界正确；拒绝公网')

# 批量关键帧的第二个房间失败时，第一个房间也不能提交或推进内存。
rooms = [bridge.rooms.create(uid, 'fixture', 3) for _ in range(2)]
for active in rooms:
    bridge.rooms.set_state(active, 'running')
    active.pending[uid] = [{'uid': uid, 'ctrl': 1}]
with sqlite3.connect(server.DB) as conn:
    conn.execute(f"CREATE TRIGGER injected_frame_error BEFORE INSERT ON local_frames WHEN NEW.room_id={rooms[1].room_id} BEGIN SELECT RAISE(ABORT,'故障注入'); END")
try:
    bridge.rooms.tick_many([active.room_id for active in rooms])
    raise AssertionError('批量帧故障未触发')
except sqlite3.IntegrityError:
    pass
assert all(active.frame_id == 0 and active.pending for active in rooms)
with sqlite3.connect(server.DB) as conn:
    assert conn.execute('SELECT COUNT(*) FROM local_frames').fetchone()[0] == 0
    conn.execute('DROP TRIGGER injected_frame_error')
assert len(bridge.rooms.tick_many([active.room_id for active in rooms])) == 2
passed.append('多房间批量帧故障全部回滚，输入保留且重试无跳帧')

transport = load('reliability_transport', '1011_transport_v1.py')
entered, released, disconnected = threading.Event(), threading.Event(), threading.Event()
def slow_write(packet):
    entered.set()
    released.wait(2)
def disconnect():
    disconnected.set()
    released.set()
sender = transport.BoundedSender(slow_write, disconnect, max_messages=2, max_bytes=16)
sender.send(b'1234')
assert entered.wait(1)
sender.send(b'1234')
sender.send(b'1234')
start = time.monotonic()
try:
    sender.send(b'1234')
    raise AssertionError('慢客户端未限流')
except OSError:
    pass
assert time.monotonic()-start < .1 and disconnected.is_set()
sender.thread.join(timeout=1)
assert not sender.thread.is_alive()
passed.append('慢客户端队列有界，立即断开且发送线程释放')
result = {'passed': passed, 'database': str(server.DB), 'real_client_verified': False}
(ROOT / 'content/1011_可靠性验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
