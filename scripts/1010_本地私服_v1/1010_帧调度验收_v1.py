"""独立测试库按真实时钟验证关键帧调度，不能代替原版客户端对局。"""
import importlib.util,json,sqlite3,sys,threading,time,uuid,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tools/python-libs'))
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
pb=load('timing_pb','1010_native_v1_pb2.py');adapter=load('timing_adapter','1010_native_rooms_v1.py')
folder=ROOT/'drafts/1010_帧调度验收_v1';folder.mkdir(parents=True,exist_ok=True)
db=folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as conn:
    conn.execute('CREATE TABLE accounts(uid TEXT PRIMARY KEY,silver INTEGER)')
    conn.execute("INSERT INTO accounts VALUES('timing',0)")
bridge=adapter.NativeRooms(pb,db,{},lambda:False)
room=bridge.rooms.create('timing','synthetic-test-map',3)
bridge.rooms.set_ready(room.room_id,'timing');bridge.rooms.begin_loading(room.room_id,'timing');bridge.rooms.loaded(room.room_id,'timing')
class Peer:
    def __init__(self):self.frames=[];self.timestamps=[]
    def send_message(self,message):
        decoded=pb.Message.FromString(message.SerializeToString())
        self.frames.append(decoded.response.battleSyncResponse.FrameId);self.timestamps.append(time.monotonic())
peer=Peer();bridge.battle_peers['timing']=peer
events=[]
thread=threading.Thread(target=bridge.run,args=(lambda **fields:events.append(fields),),daemon=True)
thread.start();time.sleep(3);bridge.stop_event.set();thread.join(10)
assert not thread.is_alive()
assert 85<=len(peer.frames)<=95
assert peer.frames==list(range(1,len(peer.frames)+1))
assert not any(event.get('event')=='battle_tick_error' for event in events)
intervals=[b-a for a,b in zip(peer.timestamps,peer.timestamps[1:])]
with sqlite3.connect(db) as conn:assert conn.execute('SELECT COUNT(*) FROM local_frames').fetchone()[0]==len(peer.frames)
report={'duration_seconds':3,'frames':len(peer.frames),
        'measured_keyframes_per_second':round((len(peer.frames)-1)/(peer.timestamps[-1]-peer.timestamps[0]),2),
        'mean_interval_ms':round(statistics.mean(intervals)*1000,2),'max_interval_ms':round(max(intervals)*1000,2),
        'events':events,'database_frames_match':True,'synthetic_peer':True,'native_client_battle_verified':False}
(ROOT/'content/1010_帧调度验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
