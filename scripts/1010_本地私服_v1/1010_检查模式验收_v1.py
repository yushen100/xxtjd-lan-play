"""使用独立库确认检查模式不会重建战斗桥或关闭正在运行的房间。"""
import importlib.util
import sqlite3
import sys
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_server', Path(__file__).with_name('1010_server_v1.py'))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
folder = root / 'drafts/1010_检查模式验收_v1'
folder.mkdir(parents=True, exist_ok=True)
server.DB = folder / (uuid.uuid4().hex + '.sqlite3')
server.init_db()
with sqlite3.connect(server.DB) as conn:
    conn.execute('CREATE TABLE local_rooms(room_id INTEGER PRIMARY KEY,state TEXT)')
    conn.execute("INSERT INTO local_rooms VALUES(1,'running')")

def forbidden_bridge(*args):
    raise AssertionError('检查模式不应创建战斗桥')

server.rooms_module.NativeRooms = forbidden_bridge
sys.argv = [str(Path(__file__)), '--check']
server.main()
with sqlite3.connect(server.DB) as conn:
    assert conn.execute('SELECT state FROM local_rooms WHERE room_id=1').fetchone()[0] == 'running'
print('独立数据库检查通过：运行房间状态未改变，未创建战斗桥')
