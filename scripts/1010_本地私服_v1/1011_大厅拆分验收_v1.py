"""独立存档检验大厅分发、账号访问和未声明的依赖。"""
import importlib.util
import json
import symtable
import builtins
import sys
import uuid
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
spec=importlib.util.spec_from_file_location('lobby_fixture_server',HERE/'1010_server_v1.py')
server=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=server
spec.loader.exec_module(server)
folder=ROOT/'drafts/1011_大厅拆分验收_v1'/uuid.uuid4().hex
folder.mkdir(parents=True)
server.DB=folder/'角色.sqlite3'
server.record=lambda **kw:None
server.init_db()
server.economy=server.economy_module.Economy(server.DB,server.TABLES['StoreBaseVo'])
server.bridge=server.rooms_module.NativeRooms(server.pb,server.DB,server.TABLES,lambda:True)
uid=server.state('10001')['uid']
services=server.lobby_services()
repo=server.account_repository()
passed=[]

def request(kind,fill=None):
    message=server.pb.Message(type=kind,seq=7)
    if fill:
        fill(message)
    answer,jobs=server.lobby_module.dispatch(services,message,'10001',uid,lambda kind:'qa:'+kind+uuid.uuid4().hex)
    assert answer.type==kind and answer.seq==7
    assert not answer.response.errcode,(kind,answer.response.errcode)
    return answer

request(3069)
request(3067,lambda m:setattr(m.request.headImageChangeRequest,'id',3))
request(3075,lambda m:setattr(m.request.headBoxChangeRequest,'headBoxId',4))
request(3101,lambda m:setattr(m.request.chatBoxIdSetRequest,'id',5))
reply=request(3050)
assert reply.response.userInfoBaseResponse.headImage==3
assert repo.profile(uid)['head_box']==4 and repo.profile(uid)['chat_box']==5
passed.append('账号消息分发与资料持久化')

with server.connect_db() as conn:
    before=repo.heroes(conn,uid)[0]
    before_skin=before['skin_id']
    hero=before['hero_id']
    try:
        repo.equip_skin(conn,uid,hero,999)
        raise RuntimeError('故障注入')
    except RuntimeError:
        conn.rollback()
with server.connect_db() as conn:
    assert next(r for r in repo.heroes(conn,uid) if r['hero_id']==hero)['skin_id']==before_skin
    assert repo.account(conn,'10001')['uid']==uid
    assert repo.account_by_uid(conn,uid)['account']=='10001'
    assert repo.hero_count(conn,uid)>0
    assert isinstance(repo.inventory(conn,uid),list)
passed.append('英雄写入由调用者事务回滚，账号和背包接口正常')

try:
    repo.set_profile(uid,'diamonds',1)
    raise AssertionError('非法资料字段未拒绝')
except ValueError:
    pass
passed.append('资料更新字段白名单')

router=server.lobby_module
assert 3000 not in router.ROUTES and not set(server.bridge.GAME_TYPES)&router.ROUTES.keys()
assert all(callable(fn) for fn in router.ROUTES.values())
assert router.ROUTES[3003].__module__=='lobby_shop'
assert router.ROUTES[3401].__module__=='lobby_friends'
assert router.ROUTES[3345].__module__=='lobby_activities'
passed.append('消息归属明确，登录和房间入口不被大厅注册表覆盖')

# 检查嵌套函数，防止迁移时遗漏旧全局变量。
for path in HERE.glob('1011_lobby_*_v1.py'):
    source=path.read_text(encoding='utf-8')
    symbols=symtable.symtable(source,str(path),'exec')
    declared={s.get_name() for s in symbols.get_symbols()}|set(dir(builtins))
    def inspect(table):
        for symbol in table.get_symbols():
            if symbol.is_global() and symbol.is_referenced():
                assert symbol.get_name() in declared,(path.name,symbol.get_name())
        for child in table.get_children():
            inspect(child)
    inspect(symbols)
    assert '1010_server_v1.py' not in source and 'self.uid' not in source
passed.append('所有处理器含嵌套函数无未声明全局依赖或服务器反向引用')
result={'passed':True,'checks':passed,'registered_types':len(router.ROUTES),'database':str(server.DB)}
(ROOT/'content/1011_大厅拆分验收_v1.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
