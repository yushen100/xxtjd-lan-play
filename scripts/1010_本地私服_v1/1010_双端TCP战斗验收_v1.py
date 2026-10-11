"""独立数据库、临时端口的双端TCP验收；不操作运行角色。"""
import importlib.util,json,socket,struct,sys,threading,time,uuid,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('tcp_fixture_server',Path(__file__).with_name('1010_server_v1.py'))
server=importlib.util.module_from_spec(spec);sys.modules[spec.name]=server;spec.loader.exec_module(server)
folder=ROOT/'drafts/1010_双端TCP战斗验收_v1';folder.mkdir(parents=True,exist_ok=True)
server.DB=folder/(uuid.uuid4().hex+'.sqlite3')
events=[];server.record=lambda **fields:events.append(fields)
server.init_db()
server.economy=server.economy_module.Economy(server.DB,server.TABLES['StoreBaseVo'])
server.bridge=server.rooms_module.NativeRooms(server.pb,server.DB,server.TABLES,lambda:True)
pb=server.pb
class Client:
    def __init__(self,port):
        self.sock=socket.create_connection(('127.0.0.1',port),timeout=3)
        self.seq=0;self.history=[]
    def exact(self,n):
        data=b''
        while len(data)<n:
            part=self.sock.recv(n-len(data))
            if not part:raise ValueError('测试连接被关闭')
            data+=part
        return data
    def send(self,kind,uid='',fill=None,crc_override=None):
        self.seq+=1;msg=pb.Message(type=kind,uid=uid,seq=self.seq)
        if fill:fill(msg)
        body=msg.SerializeToString()
        crc=zlib.crc32(body+b'VSzEI3') if kind==3002 else 0
        if crc_override is not None:crc=crc_override
        frame=struct.pack('<HI',len(body)+4,crc)+body
        # 分成两次发送，验证真实流式拆包。
        self.sock.sendall(frame[:3]);self.sock.sendall(frame[3:])
        return self.seq
    def receive(self,predicate):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            size=struct.unpack('<H',self.exact(2))[0]
            msg=pb.Message.FromString(self.exact(size));self.history.append(msg)
            if predicate(msg):return msg
        raise AssertionError('未收到预期消息')
    def request(self,kind,uid='',fill=None):
        seq=self.send(kind,uid,fill)
        reply=self.receive(lambda msg:msg.type==kind and msg.seq==seq)
        assert not reply.response.errcode,reply.response.errcode
        return reply
    def close(self):self.sock.close()

clients=[]
with server.TCPServer(('127.0.0.1',0),server.TCPHandler) as game_server, server.TCPServer(('127.0.0.1',0),server.BattleTCPHandler) as battle_server:
    threads=[threading.Thread(target=srv.serve_forever,daemon=True) for srv in (game_server,battle_server)]
    for thread in threads:thread.start()
    ticker=threading.Thread(target=server.bridge.run,args=(server.record,),daemon=True)
    ticker.start()
    try:
        a,b=Client(game_server.server_address[1]),Client(game_server.server_address[1]);clients.extend([a,b])
        for client,account in [(a,'10001'),(b,'10002')]:
            client.request(3000,fill=lambda msg,account=account:setattr(msg.request.loginRequest,'ptid',account))
        uid_a,uid_b='local-10001','local-10002'
        rid=a.request(3001,uid_a,lambda msg:(setattr(msg.request.teamCreateRequest,'battleType',3),setattr(msg.request.teamCreateRequest,'teamType',1))).response.teamCreateResponse.teamId
        def join(msg):msg.request.teamJoinRequest.teamId=rid;msg.request.teamJoinRequest.battleType=3
        b.request(3035,uid_b,join);b.request(2010,uid_b);a.request(2004,uid_a)
        match=a.receive(lambda msg:msg.type==2003).response.matchBattleInfoResponse
        assert match.battleId==rid and match.ip=='127.0.0.1' and match.port==18800
        ba,bb=Client(battle_server.server_address[1]),Client(battle_server.server_address[1]);clients.extend([ba,bb])
        for client,uid in [(ba,uid_a),(bb,uid_b)]:
            client.request(1000,uid,lambda msg:setattr(msg.request.battleLoginRequest,'battleId',rid))
        def select(msg,hero):msg.request.battleSelHeroRequest.heroId=hero;msg.request.battleSelHeroRequest.skinId=hero
        ba.request(1005,uid_a,lambda msg:select(msg,1));bb.request(1005,uid_b,lambda msg:select(msg,2))
        ba.request(1006,uid_a);bb.request(1006,uid_b)
        for client,uid in [(ba,uid_a),(bb,uid_b)]:
            client.send(1008,uid,lambda msg:setattr(msg.request.battleLoadingRequest,'percent',100))
            client.receive(lambda msg:msg.type==1008 and msg.response.battleLoadingResponse.uid==uid
                           and msg.response.battleLoadingResponse.percent==100)
        ba.receive(lambda msg:msg.type==1003);bb.receive(lambda msg:msg.type==1003)
        def control(msg):req=msg.request.battleSyncRequest;req.battleId=rid;req.msgs.add(uid=uid_a,hId=1,ctrl=7)
        ba.send(1001,uid_a,control)
        frame_a=ba.receive(lambda msg:msg.type==1001 and any(item.ctrl==7 for item in msg.response.battleSyncResponse.msgs)).response.battleSyncResponse
        frame_b=bb.receive(lambda msg:msg.type==1001 and any(item.ctrl==7 for item in msg.response.battleSyncResponse.msgs)).response.battleSyncResponse
        assert frame_a==frame_b
        def replay(msg):req=msg.request.battleFrameListRequest;req.battleId=rid;req.startFrameId=frame_a.FrameId;req.limit=10
        replay_data=bb.request(1018,uid_b,replay).response.battleFrameListResponse
        assert replay_data.frameList[0]==frame_a
        # B冒用A的输入身份必须被拒绝。
        seq=bb.send(1001,uid_b,control)
        refused=bb.receive(lambda msg:msg.type==1001 and msg.seq==seq)
        assert refused.response.errcode=='LOCAL_INPUT_IDENTITY_MISMATCH'
        ba.request(1004,uid_a);ba.request(1004,uid_a)
        def finish(msg):msg.request.battleEndRequest.battleId=rid
        settled=a.request(3002,uid_a,finish)
        GAIN=settled.response.battleEndResponse.addCoin; assert GAIN>0
        assert settled.response.battleEndResponse.commonUpdate.itemList[0].ItemNum==50000+46390*5+GAIN  # 含新手材料银币; FightAwardBaseVo[30,5]
        a.request(3002,uid_a,finish)
        # 真实TCP重登读取已保存的奖励余额。
        relog=Client(game_server.server_address[1]);clients.append(relog)
        login=relog.request(3000,fill=lambda msg:setattr(msg.request.loginRequest,'ptid','10001'))
        assert login.response.loginResponse.commonUser.coin==50000+46390*5+GAIN
        invalid=Client(game_server.server_address[1]);clients.append(invalid)
        invalid.send(3000,fill=lambda msg:setattr(msg.request.loginRequest,'ptid','10001'),crc_override=1)
        assert invalid.sock.recv(1)==b''
        report={'passed':['实际TCP流式拆包','双账号建房加入准备','匹配通知','原生战斗TCP登录','选英雄和全员加载','定时关键帧双端一致','原生补帧','冒用输入身份拒绝','CRC错误拒绝','重复结束及重复结算不重复奖励','真实TCP重登读取奖励余额'],
                'synthetic_client':True,'isolated_database':str(server.DB),'live_accounts_modified':False,'visible_game_battle_verified':False}
        (ROOT/'content/1010_双端TCP战斗验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        for client in clients:client.close()
        server.bridge.stop_event.set();ticker.join(5)
        game_server.shutdown();battle_server.shutdown()
