"""独立测试房间生命周期、身份隔离、补帧和重复结算。"""
import importlib.util,json,sqlite3,uuid,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('rooms',Path(__file__).with_name('1010_rooms_v1.py'))
module=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=module
spec.loader.exec_module(module)
folder=ROOT/'drafts/1010_房间验收_v1';folder.mkdir(parents=True,exist_ok=True)
db=folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as conn:
    conn.execute('CREATE TABLE accounts(uid TEXT PRIMARY KEY,silver INTEGER)')
    conn.executemany('INSERT INTO accounts VALUES(?,?)',[('A',100),('B',100)])
rooms=module.Rooms(db)
leaving=rooms.create('A','test-map',3)
rooms.join(leaving.room_id,'B')
rooms.leave(leaving.room_id,'A')
assert leaving.members==['B'] and leaving.leader=='B' and leaving.state=='waiting'
with sqlite3.connect(db) as conn:
    assert conn.execute('SELECT uid FROM local_room_members WHERE room_id=?',(leaving.room_id,)).fetchall()==[('B',)]
rooms.leave(leaving.room_id,'B')
assert leaving.state=='closed'
room=rooms.create('A','test-map',3)
rooms.join(room.room_id,'B');rooms.join(room.room_id,'B')
assert room.members==['A','B']
def rejected(call):
    try:call()
    except ValueError:return
    raise AssertionError('异常操作未拒绝')
rejected(lambda:rooms.begin_loading(room.room_id,'A'))
rooms.set_ready(room.room_id,'A');rooms.set_ready(room.room_id,'B')
rooms.begin_loading(room.room_id,'A')
assert rooms.loaded(room.room_id,'A')=='loading'
assert rooms.loaded(room.room_id,'B')=='running'
rejected(lambda:rooms.input(room.room_id,'A',[{'uid':'B','ctrl':1}]))
rooms.input(room.room_id,'A',[{'uid':'A','ctrl':1}])
# 模拟磁盘写入失败：帧号和待广播输入必须保留，修复后仍从第一帧提交。
with sqlite3.connect(db) as conn:
    conn.execute("CREATE TRIGGER test_frame_failure BEFORE INSERT ON local_frames BEGIN SELECT RAISE(ABORT,'test'); END")
try:
    rooms.tick(room.room_id)
except sqlite3.IntegrityError:
    pass
else:
    raise AssertionError('模拟写入失败没有触发')
assert room.frame_id==0 and len(room.pending['A'])==1
with sqlite3.connect(db) as conn:
    conn.execute('DROP TRIGGER test_frame_failure')
first=rooms.tick(room.room_id)
assert first=={'FrameId':1,'msgs':[{'uid':'A','ctrl':1}]}
assert rooms.tick(room.room_id)=={'FrameId':2,'msgs':[]}
rooms.input(room.room_id,'A',[{'uid':'A','ctrl':2}])
rooms.input(room.room_id,'A',[{'uid':'A','ctrl':3}])
assert len(rooms.tick(room.room_id)['msgs'])==2
assert rooms.frames(room.room_id,'B',1,2)[0]==first
rejected(lambda:rooms.frames(room.room_id,'outsider',1,2))
rooms.finish(room.room_id,'A');rooms.finish(room.room_id,'A')
restored=module.Rooms(db)
assert restored.frames(room.room_id,'B',1,2)[0]==first
rejected(lambda:restored.frames(room.room_id,'outsider',1,2))
with sqlite3.connect(db) as conn:
    # 结束只改状态，奖励改在 3002 结算时按原生表发放（见原生战斗协议验收）
    assert conn.execute('SELECT SUM(silver) FROM accounts').fetchone()[0]==200
report={'passed':['房间加入幂等','全员准备才可加载','全员加载才可开始','输入身份隔离','单调帧号与空帧','同帧多次输入不丢失','数据库补帧','服务重启后成员权限及回放恢复','旁观账号访问拒绝','重复结算不重复发奖'],
        'test_database':str(db),'native_battle_connected':False,'network_keyframe_rate':module.FRAME_RATE,'logic_frame_rate':module.LOGIC_FRAME_RATE,
        'frame_rate_evidence':'正常接收及补帧路径均在关键帧后补空消息；采用30关键帧/秒驱动60次逻辑更新，客户端实战尚未验证'}
(ROOT/'content/1010_本地房间验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
