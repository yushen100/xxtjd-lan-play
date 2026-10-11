"""可信本地测试的房间和帧记录核心；未替代客户端伤害判定。"""
from collections import deque
from dataclasses import dataclass, field
import json
import sqlite3
import threading
import time

# 原版一个网络关键帧入队后再补一个空消息，两次逻辑更新。
LOGIC_FRAME_RATE = 60
FRAME_RATE = 30


@dataclass
class Room:
    room_id: int
    leader: str
    map_id: str
    mode: int
    members: list = field(default_factory=list)
    state: str = 'waiting'
    ready: set = field(default_factory=set)
    loaded: set = field(default_factory=set)
    frame_id: int = 0
    pending: dict = field(default_factory=dict)
    history: deque = field(default_factory=lambda: deque(maxlen=3600))


class Rooms:
    def __init__(self, db):
        self.db = db
        self.lock = threading.RLock()
        self.rooms = {}
        with sqlite3.connect(db) as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS local_rooms(
              room_id INTEGER PRIMARY KEY AUTOINCREMENT, leader TEXT NOT NULL,
              map_id TEXT NOT NULL, mode INTEGER NOT NULL, state TEXT NOT NULL,
              created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS local_frames(
              room_id INTEGER NOT NULL, frame_id INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(room_id,frame_id));
            CREATE TABLE IF NOT EXISTS local_settlements(
              room_id INTEGER NOT NULL, uid TEXT NOT NULL, silver INTEGER NOT NULL,
              PRIMARY KEY(room_id,uid));
            CREATE TABLE IF NOT EXISTS local_room_members(
              room_id INTEGER NOT NULL, uid TEXT NOT NULL, position INTEGER NOT NULL,
              PRIMARY KEY(room_id,uid));
            ''')
            conn.execute('PRAGMA journal_mode=WAL')
            # 服务重启后中断的对局关闭，已保存回放和已结算奖励保留。
            conn.execute("UPDATE local_rooms SET state='closed' WHERE state NOT IN ('ended','closed')")
            for row in conn.execute('SELECT room_id,leader,map_id,mode,state FROM local_rooms'):
                members = [item[0] for item in conn.execute(
                    'SELECT uid FROM local_room_members WHERE room_id=? ORDER BY position', (row[0],))]
                if not members:
                    continue  # 历史版本未保存成员，不推测访问权限。
                room = Room(row[0], row[1], row[2], row[3], members, row[4])
                room.frame_id = conn.execute('SELECT COALESCE(MAX(frame_id),0) FROM local_frames WHERE room_id=?', (room.room_id,)).fetchone()[0]
                self.rooms[room.room_id] = room

    def create(self, uid, map_id, mode):
        with self.lock, sqlite3.connect(self.db) as conn:
            if conn.execute('SELECT 1 FROM accounts WHERE uid=?', (uid,)).fetchone() is None:
                raise ValueError('LOCAL_LOGIN_REQUIRED')
            cursor = conn.execute('INSERT INTO local_rooms(leader,map_id,mode,state,created) VALUES(?,?,?,?,?)',
                                  (uid, map_id, mode, 'waiting', int(time.time())))
            room = Room(cursor.lastrowid, uid, map_id, mode, [uid])
            conn.execute('INSERT INTO local_room_members VALUES(?,?,0)', (room.room_id, uid))
            self.rooms[room.room_id] = room
            return room

    def member(self, room_id, uid):
        room = self.rooms.get(room_id)
        if room is None or uid not in room.members:
            raise ValueError('LOCAL_ROOM_ACCESS_DENIED')
        return room

    def join(self, room_id, uid):
        with self.lock, sqlite3.connect(self.db) as conn:
            if conn.execute('SELECT 1 FROM accounts WHERE uid=?', (uid,)).fetchone() is None:
                raise ValueError('LOCAL_LOGIN_REQUIRED')
            room = self.rooms.get(room_id)
            if room is None or room.state != 'waiting':
                raise ValueError('LOCAL_ROOM_NOT_JOINABLE')
            if uid in room.members:
                return room
            if len(room.members) >= 10:
                raise ValueError('LOCAL_ROOM_FULL')
            room.members.append(uid)
            conn.execute('INSERT INTO local_room_members VALUES(?,?,?)', (room_id, uid, len(room.members)-1))
            return room

    def set_ready(self, room_id, uid):
        with self.lock:
            room = self.member(room_id, uid)
            if room.state != 'waiting':
                raise ValueError('LOCAL_BAD_ROOM_STATE')
            room.ready.add(uid)

    def leave(self, room_id, uid):
        with self.lock, sqlite3.connect(self.db) as conn:
            room = self.member(room_id, uid)
            if room.state != 'waiting':
                raise ValueError('LOCAL_BAD_ROOM_STATE')
            members = [member for member in room.members if member != uid]
            leader = members[0] if room.leader == uid and members else room.leader
            state = 'waiting' if members else 'closed'
            conn.execute('DELETE FROM local_room_members WHERE room_id=? AND uid=?', (room_id, uid))
            for index, member in enumerate(members):
                conn.execute('UPDATE local_room_members SET position=? WHERE room_id=? AND uid=?', (index, room_id, member))
            conn.execute('UPDATE local_rooms SET leader=?,state=? WHERE room_id=?', (leader, state, room_id))
            conn.commit()
            room.members, room.leader, room.state = members, leader, state
            room.ready.discard(uid)
            room.loaded.discard(uid)
            room.pending.pop(uid, None)
            return room

    def begin_loading(self, room_id, uid):
        with self.lock:
            room = self.member(room_id, uid)
            if uid != room.leader or room.ready != set(room.members) or room.state != 'waiting':
                raise ValueError('LOCAL_ROOM_NOT_READY')
            self.set_state(room, 'loading')

    def set_state(self, room, state):
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE local_rooms SET state=? WHERE room_id=?', (state, room.room_id))
        room.state = state

    def loaded(self, room_id, uid):
        with self.lock:
            room = self.member(room_id, uid)
            if room.state != 'loading':
                raise ValueError('LOCAL_BAD_ROOM_STATE')
            room.loaded.add(uid)
            if room.loaded == set(room.members):
                self.set_state(room, 'running')
            return room.state

    def input(self, room_id, uid, messages):
        with self.lock:
            room = self.member(room_id, uid)
            if room.state != 'running':
                raise ValueError('LOCAL_BAD_ROOM_STATE')
            if len(messages) > 64:
                raise ValueError('LOCAL_INPUT_TOO_LARGE')
            if any(msg.get('uid') not in ('', uid) for msg in messages):
                raise ValueError('LOCAL_INPUT_IDENTITY_MISMATCH')
            if len(room.pending.get(uid, [])) + len(messages) > 256:
                raise ValueError('LOCAL_PENDING_INPUT_TOO_LARGE')
            room.pending.setdefault(uid, []).extend({**msg, 'uid': uid} for msg in messages)

    def tick(self, room_id):
        return self.tick_many([room_id])[room_id]

    def tick_many(self, room_ids):
        """一轮多房间关键帧同事务落盘，提交成功后才推进内存和广播。"""
        with self.lock:
            frames = {}
            for room_id in room_ids:
                room = self.rooms[room_id]
                if room.state != 'running':
                    raise ValueError('LOCAL_BAD_ROOM_STATE')
                frames[room_id] = {'FrameId': room.frame_id + 1, 'msgs': [msg for uid in room.members for msg in room.pending.get(uid, [])]}
            if not frames:
                return frames
            with sqlite3.connect(self.db) as conn:
                conn.executemany('INSERT INTO local_frames VALUES(?,?,?)', [(rid, frame['FrameId'], json.dumps(frame)) for rid, frame in frames.items()])
            for room_id, frame in frames.items():
                room = self.rooms[room_id]
                room.frame_id = frame['FrameId']
                room.history.append(frame)
                room.pending.clear()
            return frames

    def frames(self, room_id, uid, start, limit):
        with self.lock:
            self.member(room_id, uid)
            if start < 1 or not 1 <= limit <= 600:
                raise ValueError('LOCAL_INVALID_FRAME_RANGE')
            with sqlite3.connect(self.db) as conn:
                return [json.loads(row[0]) for row in conn.execute(
                    'SELECT payload FROM local_frames WHERE room_id=? AND frame_id>=? ORDER BY frame_id LIMIT ?',
                    (room_id, start, limit))]

    def finish(self, room_id, uid):
        with self.lock:
            room = self.member(room_id, uid)
            if uid != room.leader or room.state not in ('running', 'ended'):
                raise ValueError('LOCAL_BAD_ROOM_STATE')
            # 结束只标记状态；奖励在各玩家结算请求(3002)时按原生奖励表发放（见 NativeRooms.settle）。
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE local_rooms SET state='ended' WHERE room_id=?", (room_id,))
            room.state = 'ended'
            return {'room_id': room_id}
