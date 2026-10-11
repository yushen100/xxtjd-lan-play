"""禁选/选人阶段“想禁/想选”（1016）：回给自己的响应带上本人 uid 和意向，只推给同队真人，不推给对方队伍。"""
import importlib.util, json, sqlite3, sys, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / 'tools/python-libs'))
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m
pb = load('bp_pb', '1010_native_v1_pb2.py')
adapter = load('bp_rooms', '1010_native_rooms_v1.py')
folder = ROOT / 'drafts/1011_禁选意向验收_v1'; folder.mkdir(parents=True, exist_ok=True)
db = folder / (uuid.uuid4().hex + '.sqlite3')
with sqlite3.connect(db) as c:
    c.executescript('CREATE TABLE accounts(uid TEXT PRIMARY KEY,name TEXT,silver INTEGER);CREATE TABLE heroes(uid TEXT,hero_id INTEGER);')
    c.executemany('INSERT INTO accounts VALUES(?,?,100)', [(u, u) for u in 'ABCD'])
    c.executemany('INSERT INTO heroes VALUES(?,?)', [(u, 1) for u in 'ABCD'])
tables = {r['name']: r['rows'] for r in json.loads((ROOT / 'content/1010_微端完整数据_v1.json').read_text(encoding='utf-8'))['tables']}
bridge = adapter.NativeRooms(pb, db, tables, lambda: True)
class Peer:
    def __init__(self): self.messages = []
    def send_message(self, m): self.messages.append(pb.Message.FromString(m.SerializeToString()))
game = {u: Peer() for u in 'ABCD'}; fight = {u: Peer() for u in 'ABCD'}
for u in 'ABCD':
    bridge.register_game(u, game[u])
seq = [0]
def g(kind, uid, fill=None):
    seq[0] += 1; m = pb.Message(type=kind, uid=uid, seq=seq[0])
    if fill: fill(m)
    r, jobs = bridge.game(m, uid); bridge.deliver(jobs); assert not r.response.errcode, (kind, r.response.errcode); return r
def create(m): m.request.teamCreateRequest.battleType = 3; m.request.teamCreateRequest.teamType = 1
rid = g(3001, 'A', create).response.teamCreateResponse.teamId
def join(m): m.request.teamJoinRequest.teamId = rid; m.request.teamJoinRequest.battleType = 3
for u in 'BCD':
    g(3035, u, join)
for u in 'BCD':
    g(2010, u)
g(2004, 'A')
room = bridge.rooms.rooms[rid]
for u in 'ABCD':
    m = pb.Message(type=1000, uid=u, seq=1); m.request.battleLoginRequest.battleId = rid
    r, jobs = bridge.battle(m, fight[u]); bridge.deliver(jobs)
bridge.deliver(bridge.bp_start(room))
team = {u: bridge.seat(room, u)[0] for u in 'ABCD'}
mates = [u for u in 'BCD' if team[u] == team['A']]; foes = [u for u in 'BCD' if team[u] != team['A']]
assert mates and foes, team
before = {u: len(fight[u].messages) for u in 'ABCD'}
m = pb.Message(type=1016, uid='A', seq=99)
r_ = m.request.battleBanPickSwitchRequest; r_.switchType = 3; r_.switchHeroId = 5; r_.switchSkinId = 12
reply, jobs = bridge.battle(m, fight['A']); bridge.deliver(jobs)
info = reply.response.battleBanPickSwitchResponse
assert not reply.response.errcode and info.uid == 'A' and info.switchType == 3 and info.switchSkinId == 12, reply
for u in mates:
    got = [x for x in fight[u].messages[before[u]:] if x.type == 1016]
    assert len(got) == 1 and got[0].response.battleBanPickSwitchResponse.uid == 'A' and got[0].response.battleBanPickSwitchResponse.switchHeroId == 5
for u in foes:
    assert not [x for x in fight[u].messages[before[u]:] if x.type == 1016], '意向不能推给对方队伍'
result = {'passed': ['回给自己带本人uid与意向', '同队真人收到1次', '对方队伍收不到']}
(ROOT / 'content/1011_禁选意向验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
