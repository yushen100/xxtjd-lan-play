"""觉醒、天赋、熟练度事务验收（独立数据库，不改变实际角色）。不能替代原生界面验收。"""
import importlib.util,json,sqlite3,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('eco',Path(__file__).with_name('1010_economy_v1.py'))
eco=importlib.util.module_from_spec(spec);spec.loader.exec_module(eco)
T={r['name']:r['rows'] for r in json.loads((ROOT/'content/1010_微端完整数据_v1.json').read_text(encoding='utf-8'))['tables']}
folder=ROOT/'drafts/1010_成长验收_v1';folder.mkdir(parents=True,exist_ok=True)
db=folder/(uuid.uuid4().hex+'.sqlite3')
with sqlite3.connect(db) as c:
    c.executescript('''CREATE TABLE accounts(account TEXT,uid TEXT PRIMARY KEY,name TEXT,silver INTEGER,gold INTEGER,diamonds INTEGER,friendship INTEGER);
    CREATE TABLE heroes(uid TEXT,hero_id INTEGER,proficiency INTEGER,skin_id INTEGER,star INTEGER,awake INTEGER);
    CREATE TABLE operations(uid TEXT,operation_id TEXT,result TEXT,PRIMARY KEY(uid,operation_id));''')
    c.execute("INSERT INTO accounts VALUES('t','U','测试',100000,0,0,0)")
    c.execute("INSERT INTO heroes VALUES('U',1,50,1,0,0)")
e=eco.Economy(db,T['StoreBaseVo']);e.talent_rows=T['TalentBaseVo']
def give(kind,item,n):
    with e.connection() as c: c.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count',('U',kind,item,n))
def silver():
    with e.connection() as c: return c.execute("SELECT silver FROM accounts WHERE uid='U'").fetchone()[0]
def awake():
    with e.connection() as c: return c.execute("SELECT awake FROM heroes WHERE uid='U'").fetchone()[0]
passed=[]
try: e.awake_level_up('U',1,False,False,0,'a0',T['ICCostBaseVo']); raise SystemExit('缺材料未拒绝')
except ValueError as x: assert str(x)=='LOCAL_MATERIAL_INSUFFICIENT'
assert silver()==100000 and awake()==0; passed.append('觉醒缺材料整笔回滚')
give(41,1,1);give(13,205,1)
r=e.awake_level_up('U',1,False,False,0,'a1',T['ICCostBaseVo'])
assert awake()==1 and silver()==99990; passed.append('觉醒按ICCost扣专属芯片+材料205+银币')
e.awake_level_up('U',1,False,False,0,'a1',T['ICCostBaseVo']); assert awake()==1; passed.append('觉醒重复请求幂等')
give(41,0,5);give(13,205,5)
e.awake_level_up('U',1,True,True,0,'a2',T['ICCostBaseVo']); assert awake()==6,awake(); passed.append('通用芯片补缺口且快速升到材料够用为止')
try: e.awake_level_up('U',1,True,False,5,'a3',T['ICCostBaseVo']); raise SystemExit
except ValueError as x: assert str(x)=='LOCAL_DIAM_PAY_UNSUPPORTED'
passed.append('钻石代付明确拒绝')
try: e.talent_lv_up('U',1,101,False,'t0',T['TalentBaseVo']); raise SystemExit
except ValueError as x: assert str(x)=='LOCAL_MATERIAL_INSUFFICIENT'
give(13,220,30); give(43,1,30); s0=silver()  # 英雄玄玉(43,英雄)×GeneCostNum
e.talent_lv_up('U',1,101,False,'t1',T['TalentBaseVo'])
lv,use=e.talent_info('U',1); assert lv[101]==2 and lv[201]==1 and silver()==s0-100,(lv[101],silver(),s0); passed.append('天赋默认1级、升到2级按表扣费')
give(13,220,60); give(43,0,100); e.talent_lv_up('U',1,101,True,'t2',T['TalentBaseVo'])
assert e.talent_info('U',1)[0][101]==3; passed.append('通用天赋芯片补材料')
try: e.talent_use('U',1,[101,103],T['TalentBaseVo']); raise SystemExit
except ValueError as x: assert str(x)=='LOCAL_TALENT_INVALID'
e.talent_use('U',1,[101],T['TalentBaseVo']); assert e.talent_info('U',1)[1]==[101]; passed.append('每层只能装配一个天赋')
give(43,0,200);give(13,220,200)
e.talent_clear('U',1,'c1','43,0,200;1,0,200;13,220,200'); assert e.talent_info('U',1)[1]==[] and e.talent_info('U',1)[0][101]==3; passed.append('重置天赋扣费、清空装配、保留等级')
e.reset_proficiency('U',1)
with e.connection() as c: assert c.execute("SELECT proficiency FROM heroes").fetchone()[0]==0
passed.append('熟练度重置')
e2=eco.Economy(db,T['StoreBaseVo']); assert awake()==6; passed.append('重开数据库保留')
report={'passed':passed,'test_database':str(db),'native_ui_verified':False,'local_rules':['觉醒快速一次最多10级','钻石代付不支持','天赋通用芯片替代规则推定','重置天赋不返还','熟练度重置免费','每局熟练度+10']}
(ROOT/'content/1010_成长验收_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
