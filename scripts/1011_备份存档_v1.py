"""SQLite 在线备份，只处理当前项目，不删除历史备份。"""
import datetime
import sqlite3
import uuid
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = root / 'drafts/1010_本地私服_v1/1010_角色数据库_v1.sqlite3'
if not source.is_file():
    raise SystemExit('本项目尚未创建角色数据库，无需备份')
folder = source.parent / '手动备份'
folder.mkdir(parents=True, exist_ok=True)
target = folder / (datetime.datetime.now().strftime('%m%d_%H%M%S')+'_角色备份_v1_'+uuid.uuid4().hex[:8]+'.sqlite3')
with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
    if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('备份完整性检查失败，保留文件供排查')
print('存档已备份并校验：'+str(target))
