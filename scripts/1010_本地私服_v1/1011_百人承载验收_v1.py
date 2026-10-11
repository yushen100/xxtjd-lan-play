"""100 个真实 TCP 合成客户端、10 个十人房间；只使用独立数据库和临时端口。"""
import argparse
import concurrent.futures
import importlib.util
import json
import queue
import socket
import sqlite3
import statistics
import struct
import sys
import threading
import time
import uuid
import zlib
import platform
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--input-hz', type=int, default=30)
    args = parser.parse_args()
    if args.seconds < 10:
        parser.error('持续战斗至少 10 秒')
    if not 1 <= args.input_hz <= 60:
        parser.error('输入频率必须在 1–60 Hz')
    server = load('capacity_server', '1010_server_v1.py')
    folder = ROOT / 'drafts/1011_百人承载验收_v1' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    server.DB = folder / '角色.sqlite3'
    events = []
    server.record = lambda **fields: events.append(fields)
    server.init_db()
    server.economy = server.economy_module.Economy(server.DB, server.TABLES['StoreBaseVo'])
    server.bridge = server.rooms_module.NativeRooms(server.pb, server.DB, server.TABLES, lambda: True)
    f = load('capacity_features', '1011_features_v1.py')
    p = load('capacity_passport', '1010_passport_v1.py')
    c = load('capacity_crosscheck', '1011_crosscheck_v1.py')
    server.bridge.features = f.Features(server.economy, server.TABLES)
    server.bridge.features2 = f.Features2(server.bridge.features)
    server.bridge.passport = p.Passport(server.economy, server.TABLES)
    server.bridge.crosscheck = c.CrossCheck(server.pb, folder / '核对.jsonl', server.bridge.mail, server.bridge.revoke_room_reward)
    pb = server.pb
    latencies = []
    clients = []

    class Client:
        def __init__(self, port):
            self.sock = socket.create_connection(('127.0.0.1', port), timeout=30)
            self.sock.settimeout(60)
            self.seq = 0
            self.responses = {}
            self.lock = threading.Lock()
            self.frames = []
            self.reader_error = None
            self.closed = False
            self.thread = threading.Thread(target=self.read, daemon=True)
            self.thread.start()
            clients.append(self)

        def exact(self, length):
            out = b''
            while len(out) < length:
                piece = self.sock.recv(length-len(out))
                if not piece:
                    raise EOFError()
                out += piece
            return out

        def read(self):
            try:
                while True:
                    size = struct.unpack('<H', self.exact(2))[0]
                    message = pb.Message.FromString(self.exact(size))
                    if message.type == 1001:
                        self.frames.append((message.response.battleSyncResponse.FrameId, time.monotonic()))
                    with self.lock:
                        waiter = self.responses.get((message.type, message.seq))
                    if waiter:
                        waiter.put(message)
            except (OSError, EOFError) as exc:
                if not self.closed:
                    self.reader_error = repr(exc)

        def request(self, kind, uid='', fill=None, wait=True):
            self.seq += 1
            message = pb.Message(type=kind, uid=uid, seq=self.seq)
            if fill:
                fill(message)
            key = kind, self.seq
            waiter = queue.Queue()
            if wait:
                with self.lock:
                    self.responses[key] = waiter
            body = message.SerializeToString()
            checksum = zlib.crc32(body+b'VSzEI3') if kind == 3002 else 0
            start = time.monotonic()
            self.sock.sendall(struct.pack('<HI', len(body)+4, checksum)+body)
            if not wait:
                return
            reply = waiter.get(timeout=30)
            with self.lock:
                del self.responses[key]
            latencies.append((kind, 1000*(time.monotonic()-start)))
            assert not reply.response.errcode, (kind, reply.response.errcode)
            return reply

        def close(self):
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.thread.join(timeout=2)

    def parallel(items, action):
        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as pool:
            return list(pool.map(action, items))

    game_srv = server.TCPServer(('127.0.0.1', 0), server.TCPHandler)
    battle_srv = server.TCPServer(('127.0.0.1', 0), server.BattleTCPHandler)
    started = time.monotonic()
    success = False
    result = {}
    input_stop = threading.Event()
    input_workers = []
    input_counts = [0]*100
    input_errors = []
    try:
        for srv in (game_srv, battle_srv):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        game = parallel(range(100), lambda i: Client(game_srv.server_address[1]))
        accounts = [f'cap{i:03}' for i in range(100)]
        uids = ['local-'+account for account in accounts]
        parallel(range(100), lambda i: game[i].request(3000, fill=lambda m: setattr(m.request.loginRequest, 'ptid', accounts[i])))
        assert len(server.bridge.game_peers) == 100
        rooms = []
        for base in range(0, 100, 10):
            rid = game[base].request(3001, uids[base], lambda m: (setattr(m.request.teamCreateRequest, 'battleType', 4), setattr(m.request.teamCreateRequest, 'teamType', 1))).response.teamCreateResponse.teamId
            rooms.append(rid)
            for i in range(base+1, base+10):
                game[i].request(3035, uids[i], lambda m: (setattr(m.request.teamJoinRequest, 'teamId', rid), setattr(m.request.teamJoinRequest, 'battleType', 4)))
                game[i].request(2010, uids[i])
            game[base].request(2004, uids[base])
        battle = parallel(range(100), lambda i: Client(battle_srv.server_address[1]))
        parallel(range(100), lambda i: battle[i].request(1000, uids[i], lambda m: setattr(m.request.battleLoginRequest, 'battleId', rooms[i//10])))
        parallel(range(100), lambda i: battle[i].request(1005, uids[i], lambda m: (setattr(m.request.battleSelHeroRequest, 'heroId', 1), setattr(m.request.battleSelHeroRequest, 'skinId', 1))))
        parallel(range(100), lambda i: battle[i].request(1006, uids[i]))
        parallel(range(100), lambda i: battle[i].request(1008, uids[i], lambda m: setattr(m.request.battleLoadingRequest, 'percent', 100), wait=False))
        deadline = time.monotonic()+10
        while not all(server.bridge.rooms.rooms[rid].state == 'running' for rid in rooms):
            assert time.monotonic() < deadline, '全员加载超时'
            time.sleep(.02)
        ticker = threading.Thread(target=server.bridge.run, args=(server.record,), daemon=True)
        ticker.start()
        begin = time.monotonic()
        def input_worker(i):
            deadline = time.monotonic()
            try:
                while not input_stop.is_set():
                    battle[i].request(1001, uids[i], lambda m: (setattr(m.request.battleSyncRequest, 'battleId', rooms[i//10]), m.request.battleSyncRequest.msgs.add(uid=uids[i], hId=1, ctrl=1)), wait=False)
                    input_counts[i] += 1
                    deadline += 1/args.input_hz
                    input_stop.wait(max(0, deadline-time.monotonic()))
            except Exception as exc:
                input_errors.append(repr(exc))
        input_workers = [threading.Thread(target=input_worker, args=(i,), daemon=True) for i in range(100)]
        for worker in input_workers:
            worker.start()
        while time.monotonic()-begin < args.seconds:
            parallel(range(100), lambda i: game[i].request(3069, uids[i]))
            time.sleep(1)
        elapsed = time.monotonic()-begin
        input_stop.set()
        for worker in input_workers:
            worker.join(timeout=2)
        assert not input_errors, input_errors
        assert min(input_counts)/elapsed >= args.input_hz*.9, ('输入负载不足', input_counts)
        counts = [len(client.frames) for client in battle]
        fps = [count/elapsed for count in counts]
        gaps = [(b[1]-a[1])*1000 for client in battle for a,b in zip(client.frames, client.frames[1:])]
        assert min(fps) >= 27, ('低于目标帧率', min(fps))
        assert all(all(b[0] == a[0]+1 for a,b in zip(client.frames, client.frames[1:])) for client in battle), '关键帧不连续'
        # 战斗 TCP 重连后补帧。
        reconnect_event_start = len(events)
        battle[0].close()
        reconnect = Client(battle_srv.server_address[1])
        reconnect.request(1017, uids[0], lambda m: setattr(m.request.battleReLoginRequest, 'battleId', rooms[0]))
        recovered = reconnect.request(1018, uids[0], lambda m: (setattr(m.request.battleFrameListRequest, 'battleId', rooms[0]), setattr(m.request.battleFrameListRequest, 'startFrameId', 1), setattr(m.request.battleFrameListRequest, 'limit', 10)))
        assert len(recovered.response.battleFrameListResponse.frameList) == 10
        for index, rid in enumerate(rooms):
            (reconnect if index == 0 else battle[index*10]).request(1004, uids[index*10])
        def settle(i):
            return game[i].request(3002, uids[i], lambda m: setattr(m.request.battleEndRequest, 'battleId', rooms[i//10]))
        parallel(range(100), settle)
        parallel(range(100), settle)
        with sqlite3.connect(server.DB) as conn:
            assert conn.execute('SELECT COUNT(*) FROM local_rewards').fetchone()[0] == 100
            assert conn.execute('SELECT COUNT(*) FROM battle_boxes').fetchone()[0] == 100
            assert conn.execute('SELECT COUNT(*) FROM room_extras').fetchone()[0] == 100
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not [client.reader_error for client in clients if client.reader_error]
        expected_resets = [event for event in events[reconnect_event_start:] if event.get('event') == 'battle_connection_end' and event.get('error') == 'ConnectionResetError' and event.get('uid') == uids[0]]
        assert len(expected_resets) <= 1, expected_resets
        failures = [event for event in events if event.get('event') in ('battle_tick_error', 'connection_end', 'battle_connection_end') and event not in expected_resets]
        assert not failures, failures
        def percentile(values, fraction):
            return sorted(values)[min(len(values)-1, int((len(values)-1)*fraction))]
        result = {'clients': 100, 'simultaneous_tcp_connections': 200, 'rooms': 10, 'battle_seconds': round(elapsed, 2), 'min_frame_rate': round(min(fps), 2), 'frame_gap_p95_ms': round(percentile(gaps, .95), 2), 'request_p95_ms': round(percentile([v for _,v in latencies], .95), 2), 'settlement_p95_ms': round(percentile([v for k,v in latencies if k == 3002], .95), 2), 'tick_late_count': sum(e.get('event') == 'battle_tick_late' for e in events), 'passed': ['100人同时登录', '10个十人房间持续战斗', '帧连续且至少27关键帧每秒', '战斗断线重连补帧', '100人结算及重复请求不重复奖励', 'SQLite完整性检查'], 'real_multi_machine_verified': False, 'resource_download_load_tested': False}
        success = True
        result['intentional_reconnect_reset_count'] = len(expected_resets)
        result.update({'input_hz': args.input_hz, 'min_actual_input_hz': round(min(input_counts)/elapsed, 2), 'total_input_messages': sum(input_counts), 'platform': platform.platform(), 'logical_cpus': os.cpu_count()})
    finally:
        input_stop.set()
        (folder / '事件.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        server.bridge.stop_event.set()
        for client in clients:
            if not client.closed:
                client.close()
        for srv in (game_srv, battle_srv):
            srv.shutdown()
            srv.server_close()
        result.update({'success': success, 'database': str(server.DB), 'total_seconds': round(time.monotonic()-started, 2), 'test': '单机回环合成客户端；不等于真实100台电脑的游戏体验'})
        (folder / '结果.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if success:
            (ROOT / 'content/1011_百人承载验收_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
