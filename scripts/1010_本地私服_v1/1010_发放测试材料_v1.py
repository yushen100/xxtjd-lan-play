"""给本地账号10001/10002发放成长验收用材料（仅本地数据库）。先用SQLite备份API在项目内备份。"""
import datetime,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DB=ROOT/'drafts/1010_本地私服_v1/1010_角色数据库_v1.sqlite3'
# (物品类型, 物品ID, 数量)：通用英雄碎片、活跃道具、通用芯片、芯片材料205、天赋材料220、通用天赋芯片
GRANTS=[(22,0,500),(13,19,500),(41,0,500),(13,205,500),(13,206,500),(13,220,500),(43,0,500)]
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
backup=ROOT/f'drafts/1010_本地私服_v1/1010_角色数据库备份_{stamp}_v1.sqlite3'
src=sqlite3.connect(DB,timeout=15)
with sqlite3.connect(backup) as dst: src.backup(dst)
with src:
    for uid in ('local-10001','local-10002'):
        for kind,item,num in GRANTS:
            src.execute('INSERT INTO native_items VALUES(?,?,?,?) ON CONFLICT(uid,item_type,item_id) DO UPDATE SET count=count+excluded.count',(uid,kind,item,num))
print('备份:',backup.name)
for row in src.execute("SELECT uid,item_type,item_id,count FROM native_items WHERE uid IN ('local-10001','local-10002') ORDER BY uid,item_type,item_id"):
    print(*row)
