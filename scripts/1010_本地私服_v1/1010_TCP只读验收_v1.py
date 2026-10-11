"""只读验收实际TCP入口，不执行购买或奖励。"""
import importlib.util,json,socket,sqlite3,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tools/python-libs'))
spec=importlib.util.spec_from_file_location('native',Path(__file__).with_name('1010_native_v1_pb2.py'))
pb=importlib.util.module_from_spec(spec);spec.loader.exec_module(pb)
def exact(sock,n):
    value=b''
    while len(value)<n:
        part=sock.recv(n-len(value))
        if not part:raise ValueError('TCP响应截断')
        value+=part
    return value
DB=ROOT/'drafts/1010_本地私服_v1/1010_角色数据库_v1.sqlite3'
def expected_silver(account):
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as db:
        return db.execute('select silver from accounts where account=?',(account,)).fetchone()[0]
rows=[]
for account in ('10001','10002'):
    with socket.create_connection(('127.0.0.1',18798),timeout=5) as sock:
        def request(kind,seq):
            message=pb.Message(type=kind,seq=seq)
            if kind==3000:message.request.loginRequest.ptid=account
            elif kind==3072:message.request.itemNumListRequest.ItemType=27
            data=message.SerializeToString()
            sock.sendall(struct.pack('<HI',len(data)+4,0)+data)
            size=struct.unpack('<H',exact(sock,2))[0]
            answer=pb.Message.FromString(exact(sock,size))
            assert answer.type==kind and answer.seq==seq and not answer.response.errcode
            return answer
        login=request(3000,1).response.loginResponse
        assert login.uid=='local-'+account
        assert login.commonUser.coin==expected_silver(account)
        assert login.HasField('commonFirstRecharge')
        hero_count=len(login.commonHeroList)
        assert hero_count>0
        assert request(3069,2).response.heartbeatResponse.now>0
        request(3401,3);request(3072,4)
        rows.append({'account':account,'hero_count':hero_count,'silver':login.commonUser.coin,
                     'passed':['TCP登录','首充对象存在','原生英雄列表','心跳','商城购买次数查询','背包查询']})
report={'results':rows,'purchases_executed':0,'visible_client_verified':False}
(ROOT/'content/1010_本地TCP验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
