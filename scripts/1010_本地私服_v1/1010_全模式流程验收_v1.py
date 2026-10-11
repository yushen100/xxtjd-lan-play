"""全模式协议流程验收（独立数据库复制品，合成客户端）：建房→加机器人→邀请/加入→准备→开始→战斗登录→选人→加载→帧→结束→结算。
不能替代真实客户端：各模式特有规则由客户端模拟，此处只验证服务端流程不报错。"""
import importlib.util,json,pathlib,shutil,sqlite3,sys,uuid
HERE=pathlib.Path(__file__).resolve().parent; ROOT=HERE.parents[1]
spec=importlib.util.spec_from_file_location('srv',HERE/'1010_server_v1.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
folder=ROOT/'drafts/1010_全模式验收_v1';folder.mkdir(parents=True,exist_ok=True)
src=sqlite3.connect(m.DB);db=folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as dst: src.backup(dst)
m.DB=db;m.init_db();pb=m.pb
core=m.rooms_module.core
names={v.number:v.name for v in pb.DESCRIPTOR.enum_types_by_name['BATTLE_TYPE'].values}
class P:
    uid=None
    def __init__(s):s.got=[]
    def send_message(s,x):s.got.append(x.type)
results={}
for mode,name in names.items():
    if mode==0: continue
    b=m.rooms_module.NativeRooms(pb,db,m.TABLES,lambda:True)
    ga,gb,ba,bb=P(),P(),P(),P()
    b.register_game('local-10001',ga);b.register_game('local-10002',gb)
    q=[0];step=[]
    def g(k,u,f=None):
        q[0]+=1;x=pb.Message(type=k,uid=u,seq=q[0]);f and f(x)
        r,jobs=b.game(x,u);b.deliver(jobs);return r
    def bt(k,u,peer,f=None):
        q[0]+=1;x=pb.Message(type=k,uid=u,seq=q[0]);f and f(x)
        r,jobs=b.battle(x,peer);b.deliver(jobs);return r
    try:
        step.append('建房');rid=g(3001,'local-10001',lambda x:(setattr(x.request.teamCreateRequest,'battleType',mode),setattr(x.request.teamCreateRequest,'teamType',1))).response.teamCreateResponse.teamId
        step.append('加机器人');[g(2006,'local-10001',lambda x,t=t:setattr(x.request.teamRobotAddRequest,'tId',t)) for t in (1,2,2)]
        step.append('邀请');g(3033,'local-10001',lambda x:(setattr(x.request.teamApplyRequest,'teamId',rid),setattr(x.request.teamApplyRequest,'byApplyUid','local-10002')))
        step.append('加入');g(3035,'local-10002',lambda x:(setattr(x.request.teamJoinRequest,'teamId',rid),setattr(x.request.teamJoinRequest,'battleType',mode)))
        step.append('准备');g(2010,'local-10002')
        step.append('开始');g(2004,'local-10001')
        room=b.rooms.rooms[rid]
        step.append('战斗登录')
        for u,p in (('local-10001',ba),('local-10002',bb)):
            bt(1000,u,p,lambda x:setattr(x.request.battleLoginRequest,'battleId',rid))
        step.append('选人确认')
        for u,p in (('local-10001',ba),('local-10002',bb)):
            hero,skin=b.hero(u)
            bt(1005,u,p,lambda x:(setattr(x.request.battleSelHeroRequest,'heroId',hero),setattr(x.request.battleSelHeroRequest,'skinId',skin)))
            bt(1006,u,p)
        step.append('加载')
        for u,p in (('local-10001',ba),('local-10002',bb)):
            bt(1008,u,p,lambda x:setattr(x.request.battleLoadingRequest,'percent',100))
        assert room.state=='running',room.state
        step.append('帧与结束')
        for _ in range(30): b.tick()
        room.frame_id=(b.duration(room)+3)*core.FRAME_RATE  # 跳过时长，直接到超时结束点
        b.tick()
        assert room.state=='ended',room.state
        assert 1004 in ba.got and 1004 in bb.got
        step.append('结算');r=g(3002,'local-10001',lambda x:setattr(x.request.battleEndRequest,'battleId',rid))
        assert not r.response.errcode,r.response.errcode
        results[f'{mode}:{name}']='通过'
    except Exception as e:
        results[f'{mode}:{name}']=f'失败于[{step[-1]}] {type(e).__name__}: {e}'
report={'results':results,'synthetic_client':True,'note':'只验证服务端通用流程；模式规则、地图、胜负由客户端决定，需真实客户端验收'}
(ROOT/'content/1010_全模式流程验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
for k,v in results.items(): print(k,v)
