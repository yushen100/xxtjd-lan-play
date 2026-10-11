"""检验协议异常、事务所有权和业务模块的依赖方向。"""
import ast
import importlib.util
import json
import sqlite3
import struct
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

protocol = load('1011_protocol_v1')
storage = load('1011_storage_v1')
sync = load('1011_sync_v1')
settlement = load('1011_settlement_v1')

# 更换奖励算法无需启动服务器、创建连接或修改房间代码。
rules = SimpleNamespace(compute=lambda *args: {'reward': {'silver':123}})
business = settlement.SettlementService(None,{'HeroBaseVo':[{'Id':1}]},None,None,None,reward_rules=rules)
params = dict(room_mode=3,mode=3,hero=1,star=0,awake=0,talent_ratio=0)
assert business.recompute_reward(params,SimpleNamespace()) == {'silver':123}
rules.compute = lambda *args: {'reward': {'silver':456}}
assert business.recompute_reward(params,SimpleNamespace()) == {'silver':456}

class Stream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def recv(self, size):
        return next(self.chunks, b'')

def frame(kind, crc=0):
    payload = struct.pack('<H', kind)
    return struct.pack('<HI', len(payload) + 4, crc) + payload

def decode(payload):
    return SimpleNamespace(type=struct.unpack('<H', payload)[0])

packet = frame(1001) + frame(1002)
assert [m.type for m in protocol.read_messages(Stream([packet[:1], packet[1:4], packet[4:]]), decode, battle=True)] == [1001,1002]
for data, reason in [(frame(1001,1),'战斗TCP帧校验字段应为零'),(frame(3000),'非战斗消息'),(b'\x03\x00','帧长度不足')]:
    try:
        list(protocol.read_messages(Stream([data]), decode, battle=True))
        raise AssertionError('非法帧没有拒绝')
    except ValueError as exc:
        assert str(exc) == reason
import zlib
payload = struct.pack('<H',3002)
crc = zlib.crc32(payload+b'VSzEI3')
assert next(protocol.read_messages(Stream([frame(3002,crc)]), decode)).type == 3002
try:
    list(protocol.read_messages(Stream([frame(3002)]), decode))
    raise AssertionError('未拒绝错误CRC')
except ValueError:
    pass

# 明确由调用者提交或回滚；存储层不偷偷提交业务操作。
folder = ROOT / 'drafts/1011_模块边界验收_v1'
folder.mkdir(exist_ok=True)
temp = folder / uuid.uuid4().hex
temp.mkdir()
def verify_storage():
    db = storage.SQLiteStore(temp/'角色.sqlite3')
    with closing(db.connect()) as conn, conn:
        conn.execute('CREATE TABLE probe(value INTEGER)')
    try:
        with closing(db.connect()) as conn, conn:
            conn.execute('INSERT INTO probe VALUES(1)')
            raise RuntimeError('故障注入')
    except RuntimeError:
        pass
    with closing(db.connect()) as conn, conn:
        assert conn.execute('SELECT COUNT(*) FROM probe').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1

verify_storage()

# 帧提交失败不能生成任何下发任务。
recipients = []
def fail_tick(room_ids):
    raise sqlite3.OperationalError('提交失败')
service = sync.FrameSync(None,fail_tick,lambda room_id:recipients.append(room_id))
try:
    service.collect([1])
    raise AssertionError('提交失败被吞掉')
except sqlite3.OperationalError:
    assert not recipients

# 防止后来把服务器、网络连接或整套房间对象重新塞回业务模块。
for name in ('1011_settlement_v1','1011_sync_v1','1011_storage_v1','1011_protocol_v1'):
    text = (HERE/(name+'.py')).read_text(encoding='utf-8')
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):
            assert '1010_server_v1.py' not in node.value
            assert '1010_native_rooms_v1.py' not in node.value
    if name == '1011_settlement_v1':
        assert not any(isinstance(n,ast.Attribute) and n.attr in ('send_message','battle_peers','game_peers','rooms','request') for n in ast.walk(tree))

# 大厅业务模块不直接写 SQL：数据库读写只能经 1011_repos_v1（由大厅路由注入）。
for path in sorted(HERE.glob('1011_lobby_*_v1.py')):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    raw = [n.lineno for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('execute','executemany','executescript')]
    assert not raw, f'{path.name} 第{raw}行直接执行SQL'
    bad = [n.lineno for n in ast.walk(tree) if isinstance(n,ast.Attribute) and n.attr in ('bridge','game_peers','battle_peers')]
    assert not bad, f'{path.name} 第{bad}行绕过 RoomPorts 直接访问房间对象'
server_text = (HERE/'1010_server_v1.py').read_text(encoding='utf-8')
assert 'starter_grants' not in server_text and 'growth_refill' not in server_text, '登录补发规则应只在 1011_grants_v1.py'
# 活动/兑换/宝箱/星卡等（Features2、Events、Exchange）只经 1011_features_store_v1 读写；事务控制语句除外
ftree = ast.parse((HERE/'1011_features_v1.py').read_text(encoding='utf-8'))
for cls in [n for n in ftree.body if isinstance(n, ast.ClassDef) and n.name in ('Features','Features2','Features3','Events','Exchange')] + [n for n in ftree.body if isinstance(n, ast.FunctionDef) and n.name in ('_event_draw','_bump')]:
    for n in ast.walk(cls):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('execute','executescript'):
            arg = n.args[0] if n.args else None
            assert isinstance(arg, ast.Constant) and arg.value == 'BEGIN IMMEDIATE', f'1011_features_v1.py 第{n.lineno}行应改用 features_store'
frtree = ast.parse((HERE/'1011_friends_v1.py').read_text(encoding='utf-8'))
raw = [n.lineno for n in ast.walk(frtree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('execute','executescript','executemany')]
assert not raw, f'1011_friends_v1.py 第{raw}行应改用 friends_store'
TX = {'BEGIN IMMEDIATE', 'PRAGMA foreign_keys=ON', 'SAVEPOINT step', 'ROLLBACK TO step', 'RELEASE step'}
for name, store_name in (('1010_economy_v1.py', 'economy_store'), ('1010_passport_v1.py', 'passport_store')):
    for n in ast.walk(ast.parse((HERE/name).read_text(encoding='utf-8'))):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('execute','executescript','executemany'):
            arg = n.args[0] if n.args else None
            assert isinstance(arg, ast.Constant) and arg.value in TX, f'{name} 第{n.lineno}行应改用 {store_name}'
repos_text = (HERE/'1011_repos_v1.py').read_text(encoding='utf-8')
assert 'commit(' not in repos_text and 'connect(' not in repos_text, '数据访问层不能自行开连接或提交'

# 结算邮件：注入服务端 send_mail 后，写出的邮件与原默认写法逐列一致；房间只接受名单内的依赖。
grants = load('1011_grants_v1')
import time as _t
_mail_rows = []
for injected in (False, True):
    db = temp/f'邮件{int(injected)}.sqlite3'
    svc = settlement.SettlementService(db, {}, None, None, None)
    if injected:
        svc.configure(mailer=grants.send_mail)
    with closing(sqlite3.connect(db)) as conn, conn:
        _real = _t.time; _t.time = lambda: 1791900000.0
        try:
            svc.mail(conn, 'local-x', '标题', '正文')
        finally:
            _t.time = _real
        _mail_rows.append(conn.execute('SELECT uid,sender,title,content,ctime,is_read,items,claimed FROM mails').fetchall())
assert _mail_rows[0] == _mail_rows[1] and len(_mail_rows[0]) == 1, _mail_rows
rooms_src = (HERE/'1010_native_rooms_v1.py').read_text(encoding='utf-8')
assert "def attach(self, **services)" in rooms_src and 'mailer=getattr(self' in rooms_src
server_src = (HERE/'1010_server_v1.py').read_text(encoding='utf-8')
assert not any(f'bridge.{n} =' in server_src for n in ('passport','features','features2','features3','events','exchange','friends','friends_mod','crosscheck')), '服务端应通过 bridge.attach() 注入房间依赖'

result = {'passed':True,'checks':['半包和粘包','战斗消息范围和CRC拒绝','大厅3002校验','存储异常回滚','帧提交失败不下发','模块依赖方向','大厅业务无直接SQL','大厅只经RoomPorts访问房间','登录补发规则集中在grants模块','左侧功能(抽英雄/日常/活动/兑换/宝箱/星卡/荣耀/签到)只经features_store','好友助力搭档只经friends_store','结算邮件统一且房间依赖显式注入','经济与通行证只经各自store']}
(ROOT/'content/1011_模块边界验收_v1.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
