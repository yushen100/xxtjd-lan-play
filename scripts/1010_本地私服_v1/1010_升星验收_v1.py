"""独立数据库验证原生成本升星、回滚和重复请求。"""
import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('star_economy', Path(__file__).with_name('1010_economy_v1.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
folder = ROOT/'drafts/1010_升星验收_v1'
folder.mkdir(parents=True,exist_ok=True)
db = folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as conn:
    conn.executescript('''CREATE TABLE accounts(uid TEXT PRIMARY KEY,silver INTEGER);
    CREATE TABLE heroes(uid TEXT,hero_id INTEGER,star INTEGER,PRIMARY KEY(uid,hero_id));
    CREATE TABLE operations(uid TEXT,operation_id TEXT,result TEXT,PRIMARY KEY(uid,operation_id));
    INSERT INTO accounts VALUES('A',10000); INSERT INTO heroes VALUES('A',1,0);''')
economy = module.Economy(db,[])
tables = json.loads((ROOT/'content/1010_微端完整数据_v1.json').read_text(encoding='utf-8'))['tables']
rules = next(table['rows'] for table in tables if table['name']=='HeroStarBaseVo')
try:
    economy.upgrade_star('A',1,False,False,'fail',rules)
    raise AssertionError('缺材料仍成功')
except ValueError as exc:
    assert str(exc)=='LOCAL_MATERIAL_INSUFFICIENT'
with sqlite3.connect(db) as conn:
    assert conn.execute('SELECT silver FROM accounts').fetchone()[0]==10000
    assert conn.execute('SELECT star FROM heroes').fetchone()[0]==0
    conn.executemany('INSERT INTO native_items VALUES(?,?,?,?)',[('A',22,1,1),('A',22,0,1000),('A',13,19,1000)])
first = economy.upgrade_star('A',1,False,False,'one',rules)
assert first['hero']['star']==1 and first['silver']==9990
retry = economy.upgrade_star('A',1,False,False,'one',rules)
assert retry==first
quick = economy.upgrade_star('A',1,True,True,'quick',rules)
assert quick['hero']['star']==11
assert quick['silver']==9990-sum(row['Silver'] for row in rules if 2<=row['Id']<=11)
assert module.Economy(db,[]).upgrade_star('A',1,True,True,'quick',rules)==quick
report={'passed':['缺材料整笔回滚','表内银币与碎片扣费','重复请求不重复升星','通用碎片补足缺口','快速升十星','重开数据库保留'], 'test_database':str(db),'native_ui_verified':False}
(ROOT/'content/1010_升星验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
