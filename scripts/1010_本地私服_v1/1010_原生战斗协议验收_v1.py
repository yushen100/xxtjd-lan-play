"""合成客户端验收原生消息链，不能替代可见游戏对局验收。"""
import importlib.util,json,sqlite3,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tools/python-libs'))
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
pb=load('native_pb_test','1010_native_v1_pb2.py')
adapter=load('native_rooms_test','1010_native_rooms_v1.py')
folder=ROOT/'drafts/1010_原生战斗验收_v1';folder.mkdir(parents=True,exist_ok=True)
db=folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as conn:
    conn.executescript('CREATE TABLE accounts(uid TEXT PRIMARY KEY,name TEXT,silver INTEGER);CREATE TABLE heroes(uid TEXT,hero_id INTEGER);')
    conn.executemany('INSERT INTO accounts VALUES(?,?,100)', [('A','测试A'),('B','测试B')])
    conn.executemany('INSERT INTO heroes VALUES(?,?)',[('A',1),('B',2)])
tables={row['name']:row['rows'] for row in json.loads((ROOT/'content/1010_微端完整数据_v1.json').read_text(encoding='utf-8'))['tables']}
bridge=adapter.NativeRooms(pb,db,tables,lambda:True) # 仅合成测试跳过资源文件检查，不改生产配置。
class Peer:
    uid=None
    def __init__(self):self.messages=[]
    def send_message(self,message):self.messages.append(pb.Message.FromString(message.SerializeToString()))
game_a,game_b,battle_a,battle_b=[Peer() for _ in range(4)]
bridge.register_game('A',game_a);bridge.register_game('B',game_b)
observer=Peer();bridge.register_game('A',observer);bridge.unregister('A',observer)
assert bridge.game_peers['A'] is game_a
seq=0
def game(kind,uid,fill=None):
    global seq
    seq+=1;msg=pb.Message(type=kind,uid=uid,seq=seq)
    if fill:fill(msg)
    reply,jobs=bridge.game(msg,uid);bridge.deliver(jobs)
    assert not reply.response.errcode
    return reply
def battle(kind,uid,peer,fill=None):
    global seq
    seq+=1;msg=pb.Message(type=kind,uid=uid,seq=seq)
    if fill:fill(msg)
    reply,jobs=bridge.battle(msg,peer);bridge.deliver(jobs)
    return reply
def create(msg):msg.request.teamCreateRequest.battleType=3;msg.request.teamCreateRequest.teamType=1  # 自建房流程
rid=game(3001,'A',create).response.teamCreateResponse.teamId
def join(msg):msg.request.teamJoinRequest.teamId=rid;msg.request.teamJoinRequest.battleType=3
game(3035,'B',join)
game(2001,'B')
assert bridge.rooms.rooms[rid].members==['A'] and bridge.rooms.rooms[rid].state=='waiting'
game(3035,'B',join);game(2010,'B');game(2004,'A')
game(2005,'A')
assert rid not in bridge.matched and bridge.rooms.rooms[rid].members==['A','B']
game(2004,'A')
assert game_a.messages[-1].response.matchBattleInfoResponse.port==18800
for uid,peer in [('A',battle_a),('B',battle_b)]:
    battle(1000,uid,peer,lambda msg:setattr(msg.request.battleLoginRequest,'battleId',rid))
def select(msg,hero):msg.request.battleSelHeroRequest.heroId=hero;msg.request.battleSelHeroRequest.skinId=hero
battle(1005,'A',battle_a,lambda msg:select(msg,1));battle(1005,'B',battle_b,lambda msg:select(msg,2))
battle(1006,'A',battle_a);battle(1006,'B',battle_b)
assert bridge.rooms.rooms[rid].state=='loading'
for uid,peer in [('A',battle_a),('B',battle_b)]:
    battle(1008,uid,peer,lambda msg:setattr(msg.request.battleLoadingRequest,'percent',100))
assert bridge.rooms.rooms[rid].state=='running'
def control(msg):
    req=msg.request.battleSyncRequest;req.battleId=rid;req.msgs.add(uid='A',hId=1,ctrl=1)
battle(1001,'A',battle_a,control)
for _ in range(10):bridge.tick()
a_frames=[msg.response.battleSyncResponse for msg in battle_a.messages if msg.type==1001]
b_frames=[msg.response.battleSyncResponse for msg in battle_b.messages if msg.type==1001]
assert len(a_frames)==10 and a_frames==b_frames
assert [frame.FrameId for frame in a_frames]==list(range(1,11))
assert a_frames[0].msgs[0].uid=='A'
def replay(msg):req=msg.request.battleFrameListRequest;req.battleId=rid;req.startFrameId=1;req.limit=3
recovered=battle(1018,'B',battle_b,replay).response.battleFrameListResponse
assert [frame.FrameId for frame in recovered.frameList]==[1,2,3]
battle(1004,'A',battle_a);battle(1004,'A',battle_a)
def finish(msg):msg.request.battleEndRequest.battleId=rid
R1=game(3002,'A',finish).response.battleEndResponse; GAIN=R1.addCoin; assert GAIN>0 and R1.commonUpdate.itemList[0].ItemNum==100+GAIN  # 本地每局奖励（1010_rewards_v1）
with sqlite3.connect(db) as conn:assert conn.execute('SELECT SUM(silver) FROM accounts').fetchone()[0]==200+GAIN
rid=game(3001,'A',create).response.teamCreateResponse.teamId
game(2006,'A',lambda msg:setattr(msg.request.teamRobotAddRequest,'tId',2))
bot=bridge.rooms.rooms[rid].members[1]
assert game_a.messages[-1].response.teamListResponse.userlist[1].isRobot
game(2004,'A')
assert game_a.messages[-1].response.matchBattleInfoResponse.userlist[1].isRobot
fresh=Peer()
battle(1000,'A',fresh,lambda msg:setattr(msg.request.battleLoginRequest,'battleId',rid))
battle(1006,'A',fresh)
assert bridge.rooms.rooms[rid].state=='loading'
battle(1008,'A',fresh,lambda msg:setattr(msg.request.battleLoadingRequest,'percent',100))
assert bridge.rooms.rooms[rid].state=='running' and bot in bridge.rooms.rooms[rid].loaded
try:
    game(3002,'A',finish)
    raise AssertionError('提前结算未被拒绝')
except ValueError as exc:
    assert str(exc)=='LOCAL_BATTLE_NOT_ENDED'
bridge.rooms.rooms[rid].frame_id=300*adapter.core.FRAME_RATE
R2=game(3002,'A',finish).response.battleEndResponse; GAIN2=R2.addCoin; assert GAIN2>0
assert game(3002,'A',finish).response.battleEndResponse.commonUpdate.itemList[0].ItemNum==100+GAIN+GAIN2  # 重复请求不重复发奖
with sqlite3.connect(db) as conn:
    assert conn.execute('SELECT silver FROM accounts WHERE uid=?',('A',)).fetchone()[0]==100+GAIN+GAIN2
report={'passed':['建房和加入','准备和匹配通知','本地18800地址','双账号战斗登录','原生英雄选择','确认及全员加载','相同输入帧双端广播','单调关键帧编号','原生补帧响应','重复结束不重复奖励','原生结算余额回写'],
        'synthetic_client':True,'real_client_battle_verified':False,'network_keyframe_rate':adapter.core.FRAME_RATE,'logic_frame_rate':adapter.core.LOGIC_FRAME_RATE,'test_database':str(db)}
(ROOT/'content/1010_原生战斗协议验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
