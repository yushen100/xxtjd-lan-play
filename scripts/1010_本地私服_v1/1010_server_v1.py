"""本地微端适配服务。尚未实现的消息明确拒绝，不伪造成功。"""
import argparse
import importlib.util
import json
import socketserver
import socket
import sqlite3
import struct
import sys
import threading
import time
import zlib
import ipaddress
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/python-libs'))
spec = importlib.util.spec_from_file_location('native_pb', Path(__file__).with_name('1010_native_v1_pb2.py'))
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)
economy_spec = importlib.util.spec_from_file_location('native_economy', Path(__file__).with_name('1010_economy_v1.py'))
economy_module = importlib.util.module_from_spec(economy_spec)
economy_spec.loader.exec_module(economy_module)
rooms_spec = importlib.util.spec_from_file_location('native_rooms', Path(__file__).with_name('1010_native_rooms_v1.py'))
rooms_module = importlib.util.module_from_spec(rooms_spec)
sys.modules[rooms_spec.name] = rooms_module
rooms_spec.loader.exec_module(rooms_module)
CLIENT = ROOT / 'assets/1010_本地客户端_v1'
DATA = ROOT / 'drafts/1010_本地私服_v1'
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / '1010_角色数据库_v1.sqlite3'
TABLES = {x['name']: x['rows'] for x in json.loads((ROOT / 'content/1010_微端完整数据_v1.json').read_text(encoding='utf-8'))['tables']}
GAME_PORT, HTTP_PORT, BATTLE_PORT = 18798, 18799, 18800
LOG = ROOT / 'memory/1010_本地协议运行_v1.jsonl'
log_lock = threading.Lock()
economy = None
bridge = None
RESOURCE_NAMES = [name for _, name in json.loads((ROOT / 'content/1010_原生资源清单_v1.json').read_text(encoding='utf-8'))['AssetBundleNames']]


def resources_ready():
    return all((CLIENT / 'res' / name).is_file() for name in RESOURCE_NAMES)


def record(**fields):
    line = json.dumps({'time': int(time.time()), **fields}, ensure_ascii=False)
    with log_lock:
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
    print(line, flush=True)


storage_module_spec = importlib.util.spec_from_file_location('storage_module', Path(__file__).with_name('1011_storage_v1.py'))
storage_module = importlib.util.module_from_spec(storage_module_spec)
storage_module_spec.loader.exec_module(storage_module)
protocol_module_spec = importlib.util.spec_from_file_location('protocol_module', Path(__file__).with_name('1011_protocol_v1.py'))
protocol_module = importlib.util.module_from_spec(protocol_module_spec)
protocol_module_spec.loader.exec_module(protocol_module)

lobby_spec = importlib.util.spec_from_file_location('lobby_module', Path(__file__).with_name('1011_lobby_v1.py'))
lobby_module = importlib.util.module_from_spec(lobby_spec)
lobby_spec.loader.exec_module(lobby_module)

def lobby_services():
    return lobby_module.LobbyServices(
        BATTLE_RECORD_SQL=BATTLE_RECORD_SQL,
        CHAT_LOCK=CHAT_LOCK,
        CHAT_LOG=CHAT_LOG,
        FRIEND_COIN_GIFT=FRIEND_COIN_GIFT,
        LOCAL_LEVEL=LOCAL_LEVEL,
        LOCAL_RANK_ID=LOCAL_RANK_ID,
        LOCAL_RANK_SCORE=LOCAL_RANK_SCORE,
        accounts=account_repository(),
        MAIL_SQL=MAIL_SQL,
        MSG_NAMES=MSG_NAMES,
        ROOT=ROOT,
        SEASON_AWARD1=SEASON_AWARD1,
        SEASON_AWARD2_RANK=SEASON_AWARD2_RANK,
        SEASON_ORB=SEASON_ORB,
        TABLES=TABLES,
        add_hero=add_hero,
        all_real_accounts=all_real_accounts,
        bridge=bridge,
        bubble_list=bubble_list,
        connect_db=connect_db,
        economy=economy,
        owned_items=owned_items,
        fill_friend=fill_friend,
        is_growth=is_growth,
        limited_hero_price=limited_hero_price,
        login_response=login_response,
        pb=pb,
        profile=profile,
        season_bounds=season_bounds,
        season_info=season_info,
        send_mail=send_mail,
        set_profile=set_profile,
        skin_price=skin_price,
        state=state,
    )


accounts_spec = importlib.util.spec_from_file_location('accounts_module', Path(__file__).with_name('1011_accounts_v1.py'))
accounts_module = importlib.util.module_from_spec(accounts_spec)
accounts_spec.loader.exec_module(accounts_module)

def account_repository():
    return accounts_module.AccountRepository(connect_db)


def connect_db():
    return storage_module.SQLiteStore(DB).connect()


def init_db():
    with connect_db() as conn:
        conn.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS accounts(
          account TEXT PRIMARY KEY, uid TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, silver INTEGER NOT NULL CHECK(silver>=0),
          gold INTEGER NOT NULL CHECK(gold>=0), diamonds INTEGER NOT NULL CHECK(diamonds>=0),
          friendship INTEGER NOT NULL CHECK(friendship>=0));
        CREATE TABLE IF NOT EXISTS inventory(
          uid TEXT NOT NULL REFERENCES accounts(uid), item_id INTEGER NOT NULL,
          count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(uid,item_id));
        CREATE TABLE IF NOT EXISTS operations(
          uid TEXT NOT NULL REFERENCES accounts(uid), operation_id TEXT NOT NULL,
          result TEXT NOT NULL, PRIMARY KEY(uid,operation_id));
        CREATE TABLE IF NOT EXISTS heroes(
          uid TEXT NOT NULL REFERENCES accounts(uid), hero_id INTEGER NOT NULL,
          proficiency INTEGER NOT NULL DEFAULT 0, skin_id INTEGER NOT NULL DEFAULT 0,
          star INTEGER NOT NULL DEFAULT 0, awake INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(uid,hero_id));
        ''')
        # 军功真实入库；首次迁移给现有账号 RankBaseVo 最高档军功。
        if 'rank_score' not in {r[1] for r in conn.execute('PRAGMA table_info(accounts)')}:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN rank_score INTEGER NOT NULL DEFAULT {LOCAL_RANK_SCORE} CHECK(rank_score>=0)")
        conn.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')
        for account, name in [('10001', '本地角色A'), ('10002', '本地角色B')]:
            ensure_account(conn, account, name)
        # 本地规则（用户指定）：所有材料/货币统一上限 ITEM_CAP，到上限后不再叠加（用触发器在任何写入后截断）。
        conn.execute('CREATE TABLE IF NOT EXISTS native_items(uid TEXT NOT NULL, item_type INTEGER NOT NULL, item_id INTEGER NOT NULL, count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(uid,item_type,item_id))')
        for trig in ('item_cap_ins', 'item_cap_upd', 'acct_cap_upd'):
            conn.execute(f'DROP TRIGGER IF EXISTS {trig}')
        conn.execute(f"CREATE TRIGGER item_cap_ins AFTER INSERT ON native_items WHEN NEW.count > {ITEM_CAP} BEGIN UPDATE native_items SET count={ITEM_CAP} WHERE uid=NEW.uid AND item_type=NEW.item_type AND item_id=NEW.item_id; END")
        conn.execute(f"CREATE TRIGGER item_cap_upd AFTER UPDATE OF count ON native_items WHEN NEW.count > {ITEM_CAP} BEGIN UPDATE native_items SET count={ITEM_CAP} WHERE uid=NEW.uid AND item_type=NEW.item_type AND item_id=NEW.item_id; END")
        conn.execute(f"""CREATE TRIGGER acct_cap_upd AFTER UPDATE ON accounts WHEN NEW.silver > {ITEM_CAP} OR NEW.gold > {ITEM_CAP} OR NEW.diamonds > {ITEM_CAP}
            OR NEW.friendship > {ITEM_CAP} OR NEW.rank_score > {ITEM_CAP}
            BEGIN UPDATE accounts SET silver=MIN(silver,{ITEM_CAP}), gold=MIN(gold,{ITEM_CAP}), diamonds=MIN(diamonds,{ITEM_CAP}),
            friendship=MIN(friendship,{ITEM_CAP}), rank_score=MIN(rank_score,{ITEM_CAP}) WHERE uid=NEW.uid; END""")
        conn.execute(f'UPDATE native_items SET count={ITEM_CAP} WHERE count>{ITEM_CAP}')


ITEM_CAP = 99999999  # 所有材料/货币上限（9999万，低于客户端 int32 上限）
HOST_IP = '127.0.0.1'


# 网络策略独立配置，服务器与客户端补丁使用同一份地址范围。
network_spec = importlib.util.spec_from_file_location('rea_network', Path(__file__).with_name('1011_network_v1.py'))
network_module = importlib.util.module_from_spec(network_spec)
network_spec.loader.exec_module(network_module)
network_policy = network_module.NetworkPolicy()
is_lan = network_policy.is_allowed
transport_spec = importlib.util.spec_from_file_location('rea_transport', Path(__file__).with_name('1011_transport_v1.py'))
transport_module = importlib.util.module_from_spec(transport_spec)
transport_spec.loader.exec_module(transport_module)
build_spec = importlib.util.spec_from_file_location('rea_build', Path(__file__).with_name('1011_build_v1.py'))
build_module = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(build_module)
BUILD_ID = build_module.build_id(ROOT)


FESTIVAL_SKIN_PRICE = 1288   # 节日皮肤统一价（用户确认）
SPECIAL_BUY_SKINS = {217, 336, 295, 366, 369}   # 小哪咤·庚鼠、小司马·虎虎生风；巳巳如意下线后的 嬴正·岚炎霸少、姬小发·暮影先锋、姬轩辕·龙吟九天
SKIN_LIMITED_PRICE_RATE = 1.5


SEASON_WIN_SCORE, SEASON_LOSE_SCORE = 50, 25   # 段位分：排位赢 +50，输 -25（不低于 0）
SEASON_EPOCH = (2025, 7)   # 赛季编号：2026 年 10 月 = 第 16 赛季（大于赛季商店最大 Term=15，15 款排位皮肤全部可换），之后每月 +1


def season_bounds(ts=None):
    t = time.localtime(ts)
    start = int(time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1)))
    ny, nm = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    end = int(time.mktime((ny, nm, 1, 0, 0, 0, 0, 0, -1))) - 1
    season = (t.tm_year - SEASON_EPOCH[0]) * 12 + t.tm_mon - SEASON_EPOCH[1] + 1
    return season, start, end


grants_spec = importlib.util.spec_from_file_location('rea_grants', Path(__file__).with_name('1011_grants_v1.py'))
grants_module = importlib.util.module_from_spec(grants_spec)
grants_spec.loader.exec_module(grants_module)
# 规则常量由 1011_grants_v1 定义，这里保留同名引用供其他模块/服务注入使用
ACCOUNT_RE = grants_module.ACCOUNT_RE
NEW_ACCOUNT_GROWTH = grants_module.NEW_ACCOUNT_GROWTH
GROWTH_START = grants_module.GROWTH_START
SPECIAL_REFILL_UIDS = grants_module.SPECIAL_REFILL_UIDS
RANK_SKIN_WINS = grants_module.RANK_SKIN_WINS
SEASON_ORB = grants_module.SEASON_ORB
SEASON_AWARD1 = grants_module.SEASON_AWARD1
SEASON_AWARD2_RANK = grants_module.SEASON_AWARD2_RANK
MAIL_SQL = grants_module.MAIL_SQL
send_mail = grants_module.send_mail


def season_info(uid, ts=None):
    season, start, end = season_bounds(ts)
    rows = []
    with connect_db() as conn:
        try:
            rows = [r[0] for r in conn.execute(
                "SELECT win FROM local_rewards WHERE uid=? AND mode=11 AND day>=? AND day<=? ORDER BY rowid",
                (uid, time.strftime('%Y-%m-%d', time.localtime(start)), time.strftime('%Y-%m-%d', time.localtime(end))))]
        except sqlite3.OperationalError:
            rows = []
    score, streak, best = 0, 0, 0
    for w in rows:
        score = score + SEASON_WIN_SCORE if w else max(0, score - SEASON_LOSE_SCORE)
        streak = streak + 1 if w else 0
        best = max(best, streak)
    ranks = sorted(TABLES['MAxRankBaseVo'], key=lambda r: r['Num'])
    cur = [r for r in ranks if r['Num'] <= score][-1]
    nxt = next((r for r in ranks if r['Num'] > score), ranks[-1])
    gold_by_rank = {r['Id']: r['Gold'] for r in TABLES['RankAwardBaseVo'] if r['SeasonId'] == -1}
    return {'start': start, 'gold': gold_by_rank.get(max(cur['Id'], SEASON_AWARD2_RANK), 200),
            'season': season, 'end': end, 'score': score, 'rank_id': cur['Id'], 'next_score': nxt['Num'],
            'top_id': ranks[-1]['Id'], 'fights': len(rows), 'wins': sum(1 for w in rows if w), 'streak': best}


grants = grants_module.AccountGrants(TABLES, lambda ts=None: season_bounds(ts), lambda uid, ts=None: season_info(uid, ts))


def add_hero(conn, uid, hero_id):
    grants.add_hero(conn, uid, hero_id)


def ensure_account(conn, account, name=None):
    grants.ensure_account(conn, account, name)


ERR_TEXT = {   # 客户端直接显示 errcode，这里翻成中文
    'LOCAL_HERO_UNAVAILABLE': '还没有这个英雄，先购买英雄才能使用皮肤',
    'LOCAL_NEED_HERO': '还没有这个英雄，先购买英雄再买皮肤',
    'LOCAL_BALANCE_INSUFFICIENT': '货币不足', 'LOCAL_MATERIAL_INSUFFICIENT': '材料不足',
    'LOCAL_ALREADY_OWNED': '已经拥有', 'LOCAL_ALREADY_CLAIMED': '已经领取过了',
    'LOCAL_TASK_NOT_DONE': '条件未达成', 'LOCAL_LIMIT_REACHED': '已达购买上限',
    'LOCAL_SKIN_NOT_FOR_SALE': '该皮肤不能用钻石购买（节日/活动/排位/赛事/通行证限定）', 'LOCAL_PASS_ONLY': '通行证英雄只能在通行证商店用通行币兑换',
    'LOCAL_PRICE_UNSUPPORTED': '该商品不能用这种货币购买', 'LOCAL_BOX_NOT_READY': '宝箱还没到开启时间',
    'LOCAL_EVENT_CLOSED': '活动未开放', 'LOCAL_SEASON_LOCKED': '该赛季皮肤尚未开放',
    'LOCAL_INVITE_ACCESS_DENIED': '不能邀请该玩家（只能邀请其他真人玩家）', 'LOCAL_BAD_ROOM_STATE': '当前房间状态不能进行这个操作',
    'LOCAL_DIAM_PAY_UNSUPPORTED': '本地不支持钻石代付材料，请取消勾选“使用钻石”', 'LOCAL_AWAKE_MAX': '已经觉醒到最高级',
    'LOCAL_STAR_MAX': '已经升到最高星', 'LOCAL_TALENT_MAX': '天赋已满级',
    'LOCAL_FRIEND_UNKNOWN': '找不到这个好友，或对方还没设置助力英雄', 'LOCAL_TASK_UNKNOWN': '任务不存在', 'LOCAL_MODE_CLOSED': '该模式当前未开放',
}
COMPETITION_TOP10_SKIN = 363      # 孙小策·玄雷神算（赛事限定）：争霸赛前 10 名邮件奖励（用户指定），不能购买/赠送
def skin_price(row):
    """直购价：普通原价；通行证/节日/活动限定 ×1.5；排位/赛事不能直购。"""
    if row and row['Id'] in SPECIAL_BUY_SKINS:   # 原版无获取途径的两款节日皮肤：1288 钻可买（用户指定）
        return FESTIVAL_SKIN_PRICE
    if not row or row['Type'] in (4, 6, 7, 8, 9) or not row['Num']:   # 原版：排位/赛事/通行证/节日/活动限定都不能钻石买
        raise ValueError('LOCAL_SKIN_NOT_FOR_SALE')
    return row['Num']


def limited_hero_price(row):
    """节日英雄（活动获取且原价3688）3688；其他限定英雄（活动/专属/军队）统一 3488。"""
    return 3688 if row['IsBuy'] == 3 and row['Diam'] == 3688 else 3488


def dispute_page():
    """争议对局 / 可疑对局查看页（局域网内浏览器打开）。"""
    import html
    names = {}
    with connect_db() as c:
        for r in c.execute('SELECT uid, name FROM accounts'):
            names[r['uid']] = r['name']
    path = ROOT / 'memory/1011_可疑对局_v1.jsonl'
    rows = [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()] if path.exists() else []
    trs = []
    for r in reversed(rows[-200:]):
        t = time.strftime('%m-%d %H:%M', time.localtime(r.get('time', 0)))
        who = names.get(r.get('uid'), r.get('uid', ''))
        notes = '；'.join(r.get('notes', [])) or r.get('error', '')
        if r.get('deducted'):
            notes += '（已补扣：' + '，'.join(f'{k}×{v}' for k, v in r['deducted'].items()) + '）'
        for u, n in names.items():
            notes = notes.replace(u, n)
        trs.append(f"<tr><td>{t}</td><td>第{r.get('room')}局</td><td>{html.escape(str(who))}</td><td>{html.escape(r.get('stage',''))}</td><td>{html.escape(notes)}</td></tr>")
    body = ''.join(trs) or '<tr><td colspan=5>暂无争议或可疑对局</td></tr>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>争议对局查看</title><style>body{{font-family:'Microsoft YaHei',sans-serif;background:#eef4fb;color:#123;margin:0;padding:16px}}
h1{{font-size:22px;margin:0 0 6px}}p{{color:#456;margin:0 0 14px}}table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid #cdd8e5;padding:8px 10px;text-align:left;font-size:14px}}th{{background:#2f6fb5;color:#fff}}</style></head><body>
<h1>争议对局查看</h1><p>同局玩家上报的数据对不上时会记录在这里：争议对局所有人都不发奖励；明确作假的数据会按其他玩家的战报修正，多发的奖励会补扣并邮件通知。</p>
<table><tr><th>时间</th><th>对局</th><th>玩家</th><th>类型</th><th>说明</th></tr>{body}</table></body></html>"""


def is_growth(uid):
    with connect_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS growth_accounts(uid TEXT PRIMARY KEY, time INTEGER)')
        return bool(conn.execute('SELECT 1 FROM growth_accounts WHERE uid=?', (uid,)).fetchone())


BATTLE_RECORD_SQL = """CREATE TABLE IF NOT EXISTS battle_records(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, room_id INTEGER,
    battle_type INTEGER, hero INTEGER, skin INTEGER, kill INTEGER, dead INTEGER, assist INTEGER, win INTEGER, mvp INTEGER,
    multi INTEGER, triple INTEGER, quadra INTEGER, penta INTEGER, shuchu INTEGER, tuijin INTEGER, shengcun INTEGER,
    zhiyuan INTEGER, unix INTEGER, record BLOB, UNIQUE(uid, room_id))"""


def state(account):
    with connect_db() as conn:
        row = account_repository().account(conn, account)
        if row is None:
            ensure_account(conn, account)
            row = account_repository().account(conn, account)
        if not row['uid'].startswith('local-bot-'):
            grants.on_login(conn, row['uid'])
            row = account_repository().account(conn, account)
        result = dict(row)
        result['inventory'] = account_repository().inventory(conn, row['uid'])
        return result


def bootstrap(account):
    state(account)
    return {'banshu': 0, 'channel': 0, 'configurl': f'http://{HOST_IP}:{HTTP_PORT}/native/config/',
            'version': '1.0.241.1', 'ip': HOST_IP, 'port': GAME_PORT,
            'platformParams': {'username': account, 'serverid': '0', 'cm': 0,
                               'site': 'local', 'flag': 'local', 'time': int(time.time())}}


class HTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith('/reswd/'):
            # 局域网：好友客户端缺的资源包从房主本机 res 目录提供（/reswd/<版本>/<路径>）。
            from urllib.parse import unquote
            parts = unquote(url.path).split('/')[3:]
            base = (CLIENT / 'res').resolve()
            target = base.joinpath(*parts).resolve() if parts else base
            if base in target.parents and target.is_file():
                data = target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                record(event='res_missing', path=url.path)
                self.send_error(404)
            return
        try:
            if url.path == '/native/bootstrap':
                result = bootstrap(parse_qs(url.query).get('account', ['10001'])[0])
            elif url.path == '/health':
                result = {'service': 'native-local-v1', 'status': 'running',
                          'build_id': BUILD_ID, 'host_ip': HOST_IP,
                          'complete_game': False, 'resources_complete': resources_ready(),
                          'ports': {'http': HTTP_PORT, 'game': GAME_PORT, 'battle': BATTLE_PORT}}
            elif url.path == '/native/state':
                result = state(parse_qs(url.query).get('account', ['10001'])[0])
            elif url.path == '/native/config/config_wd.json':
                result = json.loads((CLIENT / 'config_wd.json').read_text(encoding='utf-8-sig'))
            elif url.path == '/zhuanti/other/wgjb':
                # 大厅“违规举报”按钮打开的网页 → 本地改成“争议对局查看”
                body = dispute_page().encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            elif url.path in ('/stat/', '/client_error/'):
                result = {'ok': True}
            elif url.path.startswith('/online/'):
                # 本地虚拟账号的平台适配响应，与官方认证无关。
                result = {'env': 'local_test', 'code': 200, 'message': '本地测试平台',
                          'result': {'pop_config': [], 'idcard_status': 2, 'verify_status': 1,
                                     'interval': 75, 'playtime': 0, 'time_remaining': 86400,
                                     'token': 'local-test-only'}}
            else:
                record(event='http_unsupported', path=url.path)
                self.send_error(404)
                return
            body = json.dumps(result, ensure_ascii=False).encode()
            if url.path.startswith('/online/'):
                body = b'callback_1234(' + body + b')'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, OSError) as exc:
            self.send_error(400, str(exc))

    def log_message(self, *args):
        pass  # 启动参数和URL不写入日志。


# 本地账号统一为 ExpBaseVo 最高等级（30），解锁全部按等级开放的功能；不是官方成长进度。
LOCAL_LEVEL = 30
# 本地军衔/军功统一取 RankBaseVo 最高项（元帅I），解锁按军衔开放的模式；不是官方排位结果。
LOCAL_RANK = max(TABLES['RankBaseVo'], key=lambda row: row['Id'])
LOCAL_RANK_ID, LOCAL_RANK_SCORE = LOCAL_RANK['Id'], LOCAL_RANK['Num']


MSG_NAMES = {v.number: v.name for v in pb.DESCRIPTOR.enum_types_by_name['MSG'].values}


def bubble_list(uid):
    """本地规则：FightShowToolBaseVo 全部气泡/特效/宠物(Type3)永久拥有；已装备的按英雄额外下发 heroId 条目。"""
    with connect_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS bubble_equip(uid TEXT, bubble_type INTEGER, hero_id INTEGER, bubble_id INTEGER, PRIMARY KEY(uid,bubble_type,hero_id))')
        equips = conn.execute('SELECT bubble_type,hero_id,bubble_id FROM bubble_equip WHERE uid=?', (uid,)).fetchall()
    out = []
    for row in TABLES['FightShowToolBaseVo']:
        out.append(pb.CommonBattleBubble(bubbleType=row['Type'], bubbleId=row['BubbleId'], expireTime=-1, isBuy=True))
    for bubble_type, hero_id, bubble_id in equips:
        out.append(pb.CommonBattleBubble(bubbleType=bubble_type, bubbleId=bubble_id, expireTime=-1, isBuy=True, heroId=hero_id))
    return out


def owned_items(uid, item_type=None):
    """背包道具 + 本地规则下直接拥有的全部已上线头像框(16)/聊天泡泡(37)。
    登录和按类型刷新(3072)共用，避免刷新后客户端把头像框/泡泡当成未拥有而锁住。"""
    items = economy.items(uid, item_type) if economy else []
    have = {(i['item_type'], i['item_id']) for i in items}
    for f in TABLES.get('PictureFrameBaseVo', []):
        kind = 16 if f['BaseType'] == 1 else 37
        if f['IsOpen'] == 1 and (kind, f['Id']) not in have and item_type in (None, kind):
            items.append({'item_type': kind, 'item_id': f['Id'], 'count': 1})
    return items


def profile(uid):
    return account_repository().profile(uid)


def set_profile(uid, col, value):
    return account_repository().set_profile(uid, col, value)


CHAT_LOG = []          # 本地聊天记录（内存，最近 200 条）：(chatType, from_uid, to_uid, ChatInfo bytes)
CHAT_LOCK = threading.Lock()
FRIEND_COIN_GIFT = 10   # 好友送友情币：每次给对方 10 个 13:35（本地规则，不扣自己）


def all_real_accounts():
    return account_repository().real_accounts()


def fill_friend(msg, acc):
    prof = profile(acc['uid'])
    online = acc['uid'] in bridge.game_peers
    msg.uid = acc['uid']; msg.name = acc['name']; msg.level = LOCAL_LEVEL
    msg.headImage = prof['head_image']; msg.headBoxId = prof['head_box']
    msg.rankId = LOCAL_RANK_ID; msg.maxRankId = season_info(acc['uid'])['rank_id']   # maxRankId=赛季段位(MAxRankBaseVo)
    msg.onlineStatus = 1 if online else 0
    msg.loginUnix = int(time.time()) if online else int(time.time()) - 3600


def login_response(account, request):
    s = state(account)
    answer = pb.Message(type=request.type, uid=s['uid'], seq=request.seq)
    answer.response.errcode = ''
    login = answer.response.loginResponse
    login.uid = s['uid']
    login.isNewUser = False
    login.now = int(time.time())
    if getattr(bridge, 'events', None):
        for key, eid, begin, end in bridge.events.week_events():   # 本周轮换的 5 个限时活动
            login.commonFeatureTimeList.add(id=key, beginTime=begin, endTime=end, playerEndTime=end)
    login.commonUser.name = s['name']
    login.commonUser.level = LOCAL_LEVEL
    prof = profile(s['uid'])
    login.commonUser.headImage = prof['head_image']
    login.commonUser.headBoxId = prof['head_box']
    login.commonUser.chatBoxId = prof['chat_box']
    login.commonUser.coin = s['silver']
    login.commonUser.gold = s['gold']
    login.commonUser.diam = s['diamonds']
    with connect_db() as mc:
        mrow = mc.execute('SELECT count FROM native_items WHERE uid=? AND item_type=4 AND item_id=0', (s['uid'],)).fetchone()
    login.commonUser.medalMoney = mrow[0] if mrow else 0
    login.doubleBuffCountdownList.extend([0, 0])
    # 本地初始开放英雄的默认皮肤；客户端以皮肤所有权判断能否出战。
    with connect_db() as conn:
        owned = {r[0] for r in conn.execute('SELECT skin_id FROM owned_skins WHERE uid=?', (s['uid'],))}
    growth = is_growth(s['uid'])
    for skin in TABLES['SkinIdBaseVo']:
        if skin['Type'] == 1 or skin['Id'] in owned or (not growth and skin['IsOpen'] == 1):  # 本地规则：全部已上线皮肤解锁（IsOpen=0 为未上线/怪物模型，无资源）
            login.commonSkinList.add(SkinId=skin['Id'], IsBuy=True, UseNum=0)
    for item in owned_items(s['uid']):
        login.commonItemList.add(ItemType=item['item_type'], ItemId=item['item_id'], ItemNum=item['count'])
    with connect_db() as conn:
        conn.execute(BATTLE_RECORD_SQL)
        # 英雄“使用次数”：按本地战绩逐英雄统计（战绩表启用前的旧对局无英雄记录，不计入）
        fights = dict(conn.execute('SELECT hero, COUNT(*) FROM battle_records WHERE uid=? GROUP BY hero', (s['uid'],)).fetchall())
        for row in conn.execute('SELECT * FROM heroes WHERE uid=? ORDER BY hero_id', (s['uid'],)):
            hero = login.commonHeroList.add()
            hero.HeroId = row['hero_id']
            hero.IsBuy = True
            hero.Proficiency = row['proficiency']
            hero.SkinId = row['skin_id']
            hero.StarLevel = row['star']
            hero.AwakeLevel = row['awake']
            hero.FightNum = fights.get(row['hero_id'], 0)
    # 已确认原版大厅直接读取这些对象，必须提供对象本身。
    for field in ('commonArenaUser', 'commonMedalUser', 'noticeGetResp',
                  'commonFirstRecharge', 'card', 'commonChenMi'):
        if field in login.DESCRIPTOR.fields_by_name:
            getattr(login, field).SetInParent()
    arena = login.commonArenaUser
    arena.RankId = arena.MaxRankId = LOCAL_RANK_ID
    for bubble in bubble_list(s['uid']):
        login.commonBattleBubbleList.add().CopyFrom(bubble)
    # 军衔固定最高以保持玩法解锁；军功为数据库真实值，可被军功商店扣减。
    arena.RankScore = s.get('rank_score', LOCAL_RANK_SCORE)
    arena.MaxRankScore = max(arena.RankScore, LOCAL_RANK_SCORE)
    return answer


class TCPHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self.send_lock = threading.Lock()
        self.uid = None
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sender = transport_module.BoundedSender(self.request.sendall, self.disconnect)

    def disconnect(self):
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def send_message(self, message):
        try:
            code = message.response.errcode
            if code:
                base = code.split(':')[0]
                if base in ERR_TEXT:
                    message.response.errcode = ERR_TEXT[base]
        except Exception:
            pass
        reply = protocol_module.encode_reply(message)
        with self.send_lock:
            self.sender.send(reply)

    def finish(self):
        self.sender.close()
        if self.uid:
            bridge.unregister(self.uid, self)

    def handle(self):
        self.request.settimeout(90)
        account = None
        session_id = uuid.uuid4().hex
        operation_counter = [0]
        def next_operation(kind):
            # 原生客户端多数经济请求 seq=0，无法用于跨请求去重；按会话内序号生成唯一操作号。
            operation_counter[0] += 1
            return f'{session_id}:{kind}:{message.seq or 0}:{operation_counter[0]}'
        try:
            for message in protocol_module.read_messages(self.request, pb.Message.FromString, battle=False):
                jobs = []
                if message.type == 3000:
                    account = message.request.loginRequest.ptid
                    answer = login_response(account, message)
                    self.uid = answer.uid
                    bridge.register_game(self.uid, self)
                    try:
                        bridge.features.on_login(self.uid)
                    except Exception:
                        pass
                else:
                    answer = pb.Message(type=message.type, seq=message.seq)
                    if account is None:
                        answer.response.errcode = 'LOCAL_LOGIN_REQUIRED'
                    elif message.type in bridge.GAME_TYPES:
                        try:
                            answer, jobs = bridge.game(message, self.uid)
                        except ValueError as exc:
                            answer.response.errcode = str(exc)
                    else:
                        answer, jobs = lobby_module.dispatch(lobby_services(), message, account, self.uid, next_operation)
                self.send_message(answer)
                bridge.deliver(jobs)
                record(type=message.type, input_size=message.ByteSize(), reply_size=answer.ByteSize(),
                       result=answer.response.errcode or 'ok')
        except Exception as exc:
            # 仅写异常类型，避免原始报文或登录参数落盘。
            import traceback
            record(event='connection_end', error=type(exc).__name__,
                   reason=str(exc) if isinstance(exc, (ValueError, sqlite3.Error)) else '',
                   where=traceback.format_exc().strip().splitlines()[-3:])


class BattleTCPHandler(TCPHandler):
    def handle(self):
        # 原生客户端在单人加机器人时走本地模拟，整局不发送输入或心跳。
        # 空闲期限必须覆盖五分钟战斗及结算，不能沿用大厅的90秒期限。
        self.request.settimeout(600)
        try:
            for message in protocol_module.read_messages(self.request, pb.Message.FromString, battle=True):
                if not 1000 <= message.type < 2000:
                    raise ValueError('非战斗消息')
                try:
                    answer, jobs = bridge.battle(message, self)
                except ValueError as exc:
                    answer = pb.Message(type=message.type, seq=message.seq)
                    answer.response.errcode = str(exc)
                    jobs = []
                if answer:
                    self.send_message(answer)
                bridge.deliver(jobs)
                if message.type != 1001:
                    record(event='battle_request', type=message.type,
                           result=answer.response.errcode if answer else 'accepted')
        except Exception as exc:
            record(event='battle_connection_end', error=type(exc).__name__, uid=self.uid or '')


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 256  # 支持百人同时进入时的连接突发。

    def verify_request(self, request, client_address):
        return is_lan(client_address[0])


class LanHTTPServer(ThreadingHTTPServer):
    def verify_request(self, request, client_address):
        return is_lan(client_address[0])


AUTO_BACKUP_HOURS = 6      # 数据库自动备份：开服时一次，之后每 6 小时一次
AUTO_BACKUP_KEEP = 12      # 只保留最近 12 份自动备份（约 3 天）


def auto_backup_loop(record):
    """用 SQLite 在线备份 API 复制数据库（不中断游戏），备份放在 drafts/1010_本地私服_v1/自动备份/。"""
    folder = DATA / '自动备份'
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            target = folder / time.strftime('1010_角色数据库_自动备份_%Y%m%d_%H%M%S.sqlite3')
            src = sqlite3.connect(DB, timeout=30)
            dst = sqlite3.connect(target)
            with dst:
                src.backup(dst)
            dst.close(); src.close()
            olds = sorted(folder.glob('1010_角色数据库_自动备份_*.sqlite3'))
            for old in olds[:-AUTO_BACKUP_KEEP]:
                try:
                    old.unlink()
                except OSError:
                    pass
            record(event='auto_backup', file=target.name)
        except Exception as exc:
            record(event='auto_backup_error', error=type(exc).__name__)
        time.sleep(AUTO_BACKUP_HOURS * 3600)


def main():
    global economy, bridge
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--host-ip', default='127.0.0.1', help='本机局域网IP，下发给客户端')
    parser.add_argument('--lan', action='store_true', help='监听所有网卡（仍只接受私网来源）')
    parser.add_argument('--game-port', type=int, default=18798)
    parser.add_argument('--http-port', type=int, default=18799)
    parser.add_argument('--battle-port', type=int, default=18800)
    args = parser.parse_args()
    global GAME_PORT, HTTP_PORT, BATTLE_PORT
    GAME_PORT, HTTP_PORT, BATTLE_PORT = args.game_port, args.http_port, args.battle_port
    if len({GAME_PORT, HTTP_PORT, BATTLE_PORT}) != 3 or not all(1024 <= p <= 65535 for p in (GAME_PORT, HTTP_PORT, BATTLE_PORT)):
        parser.error('三个端口必须不同，范围 1024–65535')
    global HOST_IP
    if not is_lan(args.host_ip):
        raise SystemExit('host-ip 必须是私网或回环地址')
    HOST_IP = args.host_ip
    bind = '0.0.0.0' if args.lan else '127.0.0.1'
    init_db()
    economy = economy_module.Economy(DB, TABLES['StoreBaseVo'])
    economy.talent_rows = TABLES['TalentBaseVo']
    economy.gift_rows = TABLES['GiftBaseVo']
    economy.hero_rows = TABLES['HeroBaseVo']
    economy.is_growth = is_growth
    economy.valid_heroes = {k['HeroId'] for k in TABLES['SkinIdBaseVo'] if k['Type'] == 1}
    economy.default_skin = {k['HeroId']: k['Id'] for k in TABLES['SkinIdBaseVo'] if k['Type'] == 1}
    with connect_db() as fixc:   # 修复礼包发的英雄没设默认皮肤（skin_id=0，如许小褚 136，会导致英雄界面报错）
        for hid, sk in economy.default_skin.items():
            fixc.execute('UPDATE heroes SET skin_id=? WHERE hero_id=? AND skin_id=0', (sk, hid))
    with connect_db() as fixc:   # 清理客户端没有默认皮肤的英雄，换成万能玄玉
        for r in fixc.execute('SELECT uid, hero_id FROM heroes').fetchall():
            if r['hero_id'] not in economy.valid_heroes:
                fixc.execute('DELETE FROM heroes WHERE uid=? AND hero_id=?', (r['uid'], r['hero_id']))
                fixc.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count', (r['uid'], 43, 0, 100))
    if args.check:
        request = pb.Message(type=3000, seq=1)
        reply = login_response('10001', request)
        assert pb.Message.FromString(reply.SerializeToString()).response.loginResponse.commonFirstRecharge is not None
        assert state('10002')['uid'] != state('10001')['uid']
        print('本地账号持久化与登录序列化检查通过；大厅运行尚未验证')
        return
    bridge = rooms_module.NativeRooms(pb, DB, TABLES, resources_ready)
    bridge.host_ip = HOST_IP
    bridge.battle_port = BATTLE_PORT
    pspec = importlib.util.spec_from_file_location('passport_v1', Path(__file__).with_name('1010_passport_v1.py'))
    pmod = importlib.util.module_from_spec(pspec); pspec.loader.exec_module(pmod)
    fspec = importlib.util.spec_from_file_location('features_v1', Path(__file__).with_name('1011_features_v1.py'))
    fmod = importlib.util.module_from_spec(fspec); fspec.loader.exec_module(fmod)
    features = fmod.Features(economy, TABLES)
    features2 = fmod.Features2(features)
    features3 = fmod.Features3(features2)
    frspec = importlib.util.spec_from_file_location('friends_v1', Path(__file__).with_name('1011_friends_v1.py'))
    frmod = importlib.util.module_from_spec(frspec); frspec.loader.exec_module(frmod)
    ccspec = importlib.util.spec_from_file_location('crosscheck_v1', Path(__file__).with_name('1011_crosscheck_v1.py'))
    ccmod = importlib.util.module_from_spec(ccspec); ccspec.loader.exec_module(ccmod)
    crosscheck = ccmod.CrossCheck(pb, ROOT / 'memory/1011_可疑对局_v1.jsonl',
                                  mailer=lambda c, u, t, m: send_mail(c, u, '系统', t, m, []),
                                  revoke=bridge.revoke_room_reward)
    # 房间用到的外部业务一次性显式注入（结算邮件与登录补发共用 send_mail）
    bridge.attach(passport=pmod.Passport(economy, TABLES), features=features, features2=features2, features3=features3,
                  events=fmod.Events(features3), exchange=fmod.Exchange(features2),
                  friends=frmod.Friends(DB, TABLES, economy), friends_mod=frmod, crosscheck=crosscheck, mailer=send_mail)
    bridge.crosscheck.item_names = {(r['Type'], r['ItemId']): r['Name'] for r in TABLES['ItemBaseVo']}
    def _name_of(u):
        with connect_db() as nc:
            r = nc.execute('SELECT name FROM accounts WHERE uid=?', (u,)).fetchone()
        return r[0] if r else u
    bridge.crosscheck.names = _name_of
    with TCPServer((bind, GAME_PORT), TCPHandler) as tcp, TCPServer((bind, BATTLE_PORT), BattleTCPHandler) as battle, LanHTTPServer((bind, HTTP_PORT), HTTPHandler) as http:
        threading.Thread(target=tcp.serve_forever, daemon=True).start()
        threading.Thread(target=battle.serve_forever, daemon=True).start()
        threading.Thread(target=bridge.run, args=(record,), daemon=True).start()
        threading.Thread(target=auto_backup_loop, args=(record,), daemon=True).start()
        record(event='service_start', host_ip=HOST_IP, bind=bind, game=GAME_PORT, http=HTTP_PORT, battle=BATTLE_PORT,
               network_frame_rate=rooms_module.core.FRAME_RATE)
        try:
            http.serve_forever()
        finally:
            bridge.stop_event.set()
            tcp.shutdown()
            battle.shutdown()


if __name__ == '__main__':
    main()

