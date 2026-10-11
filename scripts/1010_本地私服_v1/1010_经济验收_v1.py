"""在独立测试库验收事务、重试、余额和限购，不操作运行角色。"""
import importlib.util, json, sqlite3, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('economy',Path(__file__).with_name('1010_economy_v1.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.NO_BUY_LIMIT=False  # 验收原版限购逻辑
folder=ROOT/'drafts/1010_经济验收_v1';folder.mkdir(parents=True,exist_ok=True)
db=folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as conn:
    conn.executescript('''CREATE TABLE accounts(account TEXT PRIMARY KEY,uid TEXT UNIQUE,name TEXT,silver INTEGER,gold INTEGER,diamonds INTEGER,friendship INTEGER,rank_score INTEGER DEFAULT 9000);
    CREATE TABLE operations(uid TEXT,operation_id TEXT,result TEXT,PRIMARY KEY(uid,operation_id));
    INSERT INTO accounts VALUES('test','test','测试角色',50000,0,0,0,9000);''')
tables=json.loads((ROOT/'content/1010_微端完整数据_v1.json').read_text(encoding='utf-8'))['tables']
catalog=next(x['rows'] for x in tables if x['name']=='StoreBaseVo')
economy=module.Economy(db,catalog)
first=economy.buy('test',314,1,1,'purchase1')
assert first['balance']['silver']==45000
retry=economy.buy('test',314,1,1,'purchase1')
assert retry['balance']==first['balance'] and retry['item_count']==1 and retry['added']==0
assert module.Economy(db,catalog).items('test',27)[0]['count']==1
def rejected(call,expected):
    try:call()
    except ValueError as exc:assert str(exc)==expected
    else:raise AssertionError('未拒绝异常消费')
rejected(lambda:economy.buy('test',316,1,1,'poor'),'LOCAL_BALANCE_INSUFFICIENT')
rejected(lambda:economy.buy('test',314,1,1,'purchase2'),'LOCAL_LIMIT_REACHED')
rejected(lambda:economy.buy('test',314,2,1,'purchase1'),'LOCAL_REQUEST_ID_CONFLICT')
def buy_material(index):
    try:economy.buy('test',317,1,1,'material'+str(index));return True
    except ValueError as exc:assert str(exc) in ('LOCAL_LIMIT_REACHED','LOCAL_BALANCE_INSUFFICIENT');return False
with ThreadPoolExecutor(max_workers=8) as pool:
    results=list(pool.map(buy_material,range(8)))
assert sum(results)==3
with sqlite3.connect(db) as conn:
    silver=conn.execute("SELECT silver FROM accounts WHERE uid='test'").fetchone()[0]
assert silver==0
retry=economy.buy('test',314,1,1,'purchase1')
assert retry['balance']['silver']==0 and retry['item_count']==1 and retry['added']==0
assert module.Economy(db,catalog).items('test',13)[0]['count']==3
report={'passed':['原生价格5000银币兑换喇叭','相同请求不重复扣费或发放增量','旧请求重试返回当前余额','重新打开数据库库存保留','余额不足拒绝并保持余额','限购拒绝','相同请求号不同内容拒绝','8并发请求仅3个成功且库存余额一致'],
        'test_database':str(db),'final_silver':silver,'live_client_purchase_verified':False}
(ROOT/'content/1010_本地经济验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))

# 军功宝箱：扣军功、发放GiftBaseVo奖励、英雄/皮肤概率各25%
gifts=next(x['rows'] for x in tables if x['name']=='GiftBaseVo')
economy.gift_rows=gifts
with sqlite3.connect(db) as conn:
    conn.execute("UPDATE accounts SET rank_score=9000 WHERE uid='test'")
    conn.execute('CREATE TABLE IF NOT EXISTS heroes(uid TEXT,hero_id INTEGER,proficiency INTEGER DEFAULT 0,skin_id INTEGER DEFAULT 0,star INTEGER DEFAULT 0,awake INTEGER DEFAULT 0)')
box=economy.buy('test',336,1,14,'box1')
assert box['balance']['rank_score']==8750 and len(box['rewards'])==1, box
import collections
hits=collections.Counter(economy._roll_gift(505)[0] for _ in range(20000))
assert 0.47<hits[6]/20000<0.53, hits
print(json.dumps({'box_extra':['军功扣250','宝箱发奖1件','皮肤合计≈50%',dict(hits)]},ensure_ascii=False))
