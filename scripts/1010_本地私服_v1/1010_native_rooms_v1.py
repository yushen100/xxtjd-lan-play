"""原生房间与战斗协议适配。仅本机可信测试，不提供伤害权威判定。"""
import importlib.util
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('local_rooms_core', Path(__file__).with_name('1010_rooms_v1.py'))
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)


settlement_module_spec = importlib.util.spec_from_file_location('settlement_module', Path(__file__).with_name('1011_settlement_v1.py'))
settlement_module = importlib.util.module_from_spec(settlement_module_spec)
settlement_module_spec.loader.exec_module(settlement_module)
rewards_spec = importlib.util.spec_from_file_location('room_mode_rules', Path(__file__).with_name('1010_rewards_v1.py'))
REWARDS = importlib.util.module_from_spec(rewards_spec)
rewards_spec.loader.exec_module(REWARDS)
storage_module_spec = importlib.util.spec_from_file_location('storage_module', Path(__file__).with_name('1011_storage_v1.py'))
storage_module = importlib.util.module_from_spec(storage_module_spec)
storage_module_spec.loader.exec_module(storage_module)
sync_module_spec = importlib.util.spec_from_file_location('sync_module', Path(__file__).with_name('1011_sync_v1.py'))
sync_module = importlib.util.module_from_spec(sync_module_spec)
sync_module_spec.loader.exec_module(sync_module)


class NativeRooms:
    GAME_TYPES = {3001, 3033, 3035, 3036, 2001, 2002, 2004, 2005, 2006, 2007, 2008, 2010, 3047, 3002}

    @staticmethod
    def is_human(uid):
        return bool(uid) and uid.startswith('local-') and not uid.startswith('local-bot-')

    def __init__(self, pb, db, tables, resources_ready):
        self.pb, self.db, self.tables = pb, db, tables
        self.resources_ready = resources_ready
        self.store = storage_module.SQLiteStore(db)
        self.settlement = settlement_module.SettlementService(db, tables, self.hero, self.seat, self.play_mode)
        self.sync = sync_module.FrameSync(pb, self.rooms_core_tick, self.frame_recipients)
        self.rooms = core.Rooms(db)
        self.lock = threading.RLock()
        self.game_peers, self.battle_peers = {}, {}
        self.game_sessions = {}
        self.uid_rooms, self.selected, self.lobby_ready = {}, {}, set()
        self.matched = set()
        # 座位：room_id -> {uid: [tId, pos]}；tId 只取1/2（房间两排），pos 为本排1-5号位。
        self.seats = {}
        self.queue = {}  # mode -> [(room_id, 入队时间)]，匹配类组队等待撮合
        self.stop_event = threading.Event()

    def register_game(self, uid, peer):
        with self.lock:
            self.game_sessions.setdefault(uid, []).append(peer)
            self.game_peers[uid] = peer

    def unregister(self, uid, peer):
        with self.lock:
            sessions = self.game_sessions.get(uid, [])
            if peer in sessions:
                sessions.remove(peer)
            if sessions:
                self.game_peers[uid] = sessions[-1]
            else:
                self.game_sessions.pop(uid, None)
                self.game_peers.pop(uid, None)
            if self.battle_peers.get(uid) is peer:
                self.battle_peers.pop(uid)

    def user(self, uid):
        with self.store.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM accounts WHERE uid=?', (uid,)).fetchone()
            if row is None:
                raise ValueError('LOCAL_LOGIN_REQUIRED')
            return dict(row)

    def room_for(self, uid):
        room_id = self.uid_rooms.get(uid)
        if room_id is None:
            raise ValueError('LOCAL_ROOM_ACCESS_DENIED')
        return self.rooms.member(room_id, uid)

    def answer(self, message, uid):
        reply = self.pb.Message(type=message.type, uid=uid, seq=message.seq)
        reply.response.errcode = ''
        return reply

    def free_seat(self, room, team=None):
        taken = {tuple(v) for uid, v in self.seats.get(room.room_id, {}).items() if uid in room.members}
        teams = [team] if team else sorted((1, 2), key=lambda t: sum(1 for v in taken if v[0] == t))
        for t in teams:
            for pos in range(1, 6):
                if (t, pos) not in taken:
                    return [t, pos]
        raise ValueError('LOCAL_ROOM_FULL')

    def seat(self, room, uid):
        """返回成员座位；首次出现的成员（含从数据库恢复的房间）按人数少的一排补位。"""
        seats = self.seats.setdefault(room.room_id, {})
        for gone in [u for u in seats if u not in room.members]:
            seats.pop(gone)
        if uid not in seats:
            seats[uid] = self.free_seat(room)
        return seats[uid]

    def hero(self, uid):
        if uid not in self.selected:
            import random
            if uid.startswith('local-bot-'):
                # 机器人随机英雄与该英雄任一皮肤。
                pairs = [(r['HeroId'], r['Id']) for r in self.tables['SkinIdBaseVo']
                         if r['IsOpen'] == 1 and any(h['Id'] == r['HeroId'] for h in self.tables['HeroBaseVo'])]
                self.selected[uid] = random.choice(pairs)
            else:
                first = self.tables['HeroBaseVo'][0]['Id']
                skin = next(row['Id'] for row in self.tables['SkinIdBaseVo'] if row['HeroId'] == first and row['Type'] == 1)
                self.selected[uid] = (first, skin)
        return self.selected[uid]

    def push(self, room, message, registry):
        return [(registry[uid], message) for uid in room.members if uid in registry]

    def team_list(self, room):
        message = self.pb.Message(type=2002)
        message.response.errcode = ''
        message.response.teamListResponse.SetInParent()
        for index, uid in enumerate(room.members):
            user = self.user(uid)
            message.response.teamListResponse.userlist.add(
                uid=uid, name=user['name'], headImage=1, pos=self.seat(room, uid)[1],
                tId=self.seat(room, uid)[0], isLeader=uid == room.leader, isReady=uid in self.lobby_ready,
                isRobot=uid.startswith('local-bot-'))
        return message

    def match_info(self, room):
        message = self.pb.Message(type=2003)
        message.response.errcode = ''
        info = message.response.matchBattleInfoResponse
        info.battleId, info.ip, info.port = room.room_id, getattr(self, 'host_ip', '127.0.0.1'), getattr(self, 'battle_port', 18800)
        info.battleType, info.randBattleType = room.mode, self.play_mode(room)
        info.teamType = getattr(room, 'team_type', 1)
        info.isBanPick = bool(getattr(room, 'ban_pick', False))
        for index, uid in enumerate(room.members):
            hero_id, skin_id = self.hero(uid)
            player = info.userlist.add(uid=uid, tId=self.seat(room, uid)[0], pos=self.seat(room, uid)[1],
                                       isRobot=uid.startswith('local-bot-'))
            player.battleData.name = self.user(uid)['name']
            player.battleData.headImage = 1
            player.battleData.level = self.local_level()
            player.battleData.rankId = self.local_rank()[0]
            player.battleData.maxRankId = player.battleData.rankId
            player.battleData.heroId, player.battleData.skinId = hero_id, skin_id
            if not uid.startswith('local-bot-'):
                star, awake, prof, talents = self.growth(uid, hero_id)
                player.battleData.starLevel, player.battleData.awakeLevel = star, awake
                player.battleData.proficiency = prof
                player.battleData.talentIdList.extend(talents)
            player.battleData.medalInfo.SetInParent()
            player.battleData.heroIdList.extend(row['Id'] for row in self.tables['HeroBaseVo'])
            if not uid.startswith('local-bot-'):
                player.battleData.petNum = sum(1 for r in self.tables['FightShowToolBaseVo'] if r['Type'] == 3)
                with self.store.connect() as conn:
                    try:
                        rows = conn.execute('SELECT bubble_type,bubble_id FROM bubble_equip WHERE uid=? AND hero_id IN (0,?) ORDER BY hero_id', (uid, hero_id)).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
                for bubble_type, bubble_id in dict(rows).items():
                    player.battleData.commonBattleBubbleMap.add(key=bubble_type, value=bubble_id)
        return message

    def battle_status(self, room, status):
        message = self.pb.Message(type=1007)
        message.response.errcode = ''
        data = message.response.battleReadyStatusResponse
        data.status = status
        data.banPickStep.SetInParent()
        bp = getattr(room, 'bp', None)
        if bp:
            step = bp['steps'][bp['idx']] if bp['idx'] < len(bp['steps']) else ('ready', 0, [])
            kind, team, positions = step
            data.banPickStep.banPickStatus = {'ban': 0, 'pick': 1, 'ready': 2}[kind]
            data.banPickStep.tId = team
            data.banPickStep.posList.extend(positions)
            for t, hero in sorted(bp['bans'].items()):
                data.heroIdBanMap.add(key=t, value=hero)
            # heroSelList 是克隆之战“全队统一英雄”字段，BP 不使用（发空列表会让选人界面越界崩溃）。
        for index, uid in enumerate(room.members):
            hero_id, skin_id = self.hero(uid)
            player = data.userList.add(uid=uid, status=status, pos=self.seat(room, uid)[1],
                                       heroId=hero_id, skinId=skin_id, tId=self.seat(room, uid)[0],
                                       isRobot=uid.startswith('local-bot-'))
            if not uid.startswith('local-bot-'):
                star, awake, prof, talents = self.growth(uid, hero_id)
                player.starLevel, player.awakeLevel, player.proficiency = star, awake, prof
                player.talentIdList.extend(talents)
                player.petNum = sum(1 for r in self.tables['FightShowToolBaseVo'] if r['Type'] == 3)
            player.medalInfo.SetInParent()
        return message

    BP_STEP_SECONDS = 18 + 3  # 客户端 BattleBanProxy 每步倒计时18秒，另给3秒网络宽限

    def bp_start(self, room):
        """BP 顺序（本地规则，参照常见BP）：双方各禁一次 → 蛇形选人 1-2-2-…-1 → 全员调整军备后准备。"""
        teams = {t: sorted(self.seat(room, u)[1] for u in room.members if self.seat(room, u)[0] == t) for t in (1, 2)}
        steps = [('ban', 1, teams[1]), ('ban', 2, teams[2])]
        order, take = [], {1: list(teams[1]), 2: list(teams[2])}
        turn, size = 1, 1
        while take[1] or take[2]:
            if take[turn]:
                group = take[turn][:size]
                take[turn] = take[turn][size:]
                steps.append(('pick', turn, group))
            turn, size = 3 - turn, 2
        steps.append(('ready', 0, []))
        room.bp = {'steps': steps, 'idx': 0, 'bans': {}, 'picked': set(), 'ready': set(),
                   'deadline': time.time() + self.BP_STEP_SECONDS}
        for u in room.members:
            self.selected.pop(u, None)
        return self.bp_autoplay(room)

    def bp_turn_members(self, room):
        kind, team, positions = room.bp['steps'][room.bp['idx']]
        return kind, [u for u in room.members if self.seat(room, u)[0] == team and self.seat(room, u)[1] in positions]

    def bp_random_hero(self, room):
        import random
        banned = set(room.bp['bans'].values())
        taken = {self.hero(u)[0] for u in room.bp['picked']}
        playable = {r['HeroId'] for r in self.tables['SkinIdBaseVo'] if r['IsOpen'] == 1}
        pool = [h['Id'] for h in self.tables['HeroBaseVo'] if h['Id'] in playable and h['Id'] not in banned and h['Id'] not in taken]
        hero = random.choice(pool)
        skins = [r['Id'] for r in self.tables['SkinIdBaseVo'] if r['HeroId'] == hero and r['IsOpen'] == 1]
        return hero, (random.choice(skins) if skins else 0)

    def bp_advance(self, room):
        room.bp['idx'] += 1
        room.bp['deadline'] = time.time() + self.BP_STEP_SECONDS
        return self.bp_autoplay(room)

    def bp_autoplay(self, room):
        """机器人与超时自动处理；推进到需要真人操作的步骤。返回待推送消息。"""
        bp = room.bp
        while bp['idx'] < len(bp['steps']):
            kind, members = self.bp_turn_members(room)
            if kind == 'ready':
                for u in room.members:
                    if u.startswith('local-bot-'):
                        bp['ready'].add(u)
                break
            humans = [u for u in members if not u.startswith('local-bot-')]
            if kind == 'ban':
                if humans:
                    break
                bp['bans'].setdefault(bp['steps'][bp['idx']][1], self.bp_random_hero(room)[0])
                bp['idx'] += 1
                continue
            for u in members:
                if u.startswith('local-bot-') and u not in bp['picked']:
                    self.selected[u] = self.bp_random_hero(room)
                    bp['picked'].add(u)
            if all(u in bp['picked'] for u in members):
                bp['idx'] += 1
                continue
            break
        bp['deadline'] = time.time() + self.BP_STEP_SECONDS
        return self.push(room, self.battle_status(room, 0), self.battle_peers) + self.bp_check_ready(room)

    def bp_check_ready(self, room):
        bp = room.bp
        if bp['idx'] < len(bp['steps']) - 1 or not set(room.members) <= bp['ready']:
            return []
        if room.state != 'waiting':
            room.bp = None
            return []
        for u in room.members:
            self.rooms.set_ready(room.room_id, u)
        self.rooms.begin_loading(room.room_id, room.leader)
        room.loaded.update(b for b in room.members if b.startswith('local-bot-'))
        room.bp = None
        return self.push(room, self.battle_status(room, 2), self.battle_peers)

    def bp_timeout(self, room):
        bp = room.bp
        kind, members = self.bp_turn_members(room)
        if kind == 'ban':
            # 超时视为不禁
            return self.bp_advance(room)
        if kind == 'pick':
            for u in members:
                if u not in bp['picked']:
                    if u not in self.selected:
                        self.selected[u] = self.bp_random_hero(room)
                    bp['picked'].add(u)
            return self.bp_advance(room)
        bp['ready'].update(room.members)
        return self.bp_check_ready(room)

    def add_bot(self, room, team):
        number = next(n for n in range(1, 11) if f'local-bot-{room.room_id}-{n}' not in room.members)
        bot = f'local-bot-{room.room_id}-{number}'
        with self.store.connect() as conn:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(accounts)')}
            if 'account' in columns:
                conn.execute('INSERT OR IGNORE INTO accounts(account,uid,name,silver,gold,diamonds,friendship) VALUES(?,?,?,?,?,?,?)',
                             (bot, bot, f'本地机器人{number}', 0, 0, 0, 0))
            else:
                conn.execute('INSERT OR IGNORE INTO accounts(uid,name,silver) VALUES(?,?,0)', (bot, f'本地机器人{number}'))
        seat = self.free_seat(room, team)
        self.rooms.join(room.room_id, bot)
        self.seats.setdefault(room.room_id, {})[bot] = seat
        self.lobby_ready.add(bot)

    MATCH_WAIT_SECONDS = 15  # 本地规则：排队超过15秒仍凑不齐真人则补机器人开局

    # 官网：匹配赛/排位赛 从 歼灭战、5V5推塔、5V5资源战、据点之争、宝石争夺 中随机一种
    RANDOM_POOL = (7, 4, 9, 5, 22)

    def play_mode(self, room):
        if room.mode in (11, 13):
            if not getattr(room, 'rand_mode', None):
                import random
                room.rand_mode = random.choice(self.RANDOM_POOL)
            return room.rand_mode
        return room.mode

    def duration(self, room):
        """对局时长（秒）：取实际玩法 OpenBaseVo.BattleTime；表里为0的用官网/默认值。"""
        mode = self.play_mode(room)
        conf = next((r for r in self.tables['OpenBaseVo'] if r['FightStype'] == mode or r['Id'] == mode), None) or {}
        return conf.get('BattleTime') or {14: 600, 101: 600}.get(mode, 300)

    def mode_shape(self, mode):
        """(总人数, 每队人数)。来自 OpenBaseVo.PeoNum/TeamNum；每队不超过5（房间两排）。"""
        conf = next((r for r in self.tables['OpenBaseVo'] if r['FightStype'] == mode or r['Id'] == mode), None) or {}
        total = min(10, conf.get('PeoNum') or 10)
        per = min(5, conf.get('TeamNum') or 5, total)
        return total, per

    def matchmake(self, now):
        """把同模式排队的组队按人数拼成一局；真人不够且最早的队伍等满 MATCH_WAIT_SECONDS 才补机器人。"""
        jobs = []
        for mode, waiting in list(self.queue.items()):
            waiting = [(r, t) for r, t in waiting if r in self.rooms.rooms and self.rooms.rooms[r].state == 'waiting']
            self.queue[mode] = waiting
            if not waiting:
                continue
            total, per = self.mode_shape(mode)
            teams = 1 if per >= total else 2
            # 贪心装箱：每个组队整体放进一个有空位的队
            plan, slots = [], {t: per if teams == 2 else total for t in range(1, teams + 1)}
            for rid, t0 in waiting:
                size = len(self.rooms.rooms[rid].members)
                target = max(slots, key=lambda k: slots[k])
                if slots[target] >= size:
                    plan.append((rid, target)); slots[target] -= size
            humans = sum(len(self.rooms.rooms[r].members) for r, _ in plan)
            full = humans >= total
            oldest = min(t for _, t in waiting)
            if not full and now - oldest < self.MATCH_WAIT_SECONDS:
                continue
            if not plan:
                continue
            host_id = plan[0][0]
            host = self.rooms.rooms[host_id]
            self.seats[host_id] = {}
            for u in host.members:
                self.seats[host_id][u] = self.free_seat(host, plan[0][1] if teams == 2 else 1)
            for rid, team in plan[1:]:
                other = self.rooms.rooms[rid]
                for u in list(other.members):
                    self.rooms.leave(rid, u)
                    self.rooms.join(host_id, u)
                    self.uid_rooms[u] = host_id
                    self.seats[host_id][u] = self.free_seat(host, team if teams == 2 else 1)
            for t in range(1, teams + 1):
                cap = per if teams == 2 else total
                while sum(1 for u in host.members if self.seat(host, u)[0] == t) < min(cap, 5) and len(host.members) < total:
                    self.add_bot(host, t)
            used = {r for r, _ in plan}
            self.queue[mode] = [(r, t) for r, t in waiting if r not in used]
            if host.mode in (11, 13):
                # 匹配/排位：整局（含拼进来的所有好友）共用一个玩法，开局时随机决定；
                # 尽量避开本局任一真人上一局玩过的玩法，避免连续同一种
                import random
                last = getattr(self, 'last_play_mode', {})
                humans = [u for u in host.members if not u.startswith('local-bot-')]
                recent = {last[u] for u in humans if u in last}
                pool = [m for m in self.RANDOM_POOL if m not in recent] or list(self.RANDOM_POOL)
                host.rand_mode = random.choice(pool)
                for u in humans:
                    last[u] = host.rand_mode
                self.last_play_mode = last
            self.matched.add(host_id)
            jobs += self.push(host, self.team_list(host), self.game_peers)
            jobs += self.push(host, self.match_info(host), self.game_peers)
        return jobs

    def detach(self, uid):
        self.uid_rooms.pop(uid, None)
        self.lobby_ready.discard(uid)
        self.selected.pop(uid, None)
        self.battle_peers.pop(uid, None)

    def note_borrow(self, uid, hero_id, borrow_uid='', lender=None):
        """记录本局是否用了助力英雄（选人 1005 和 BP 选人 1012 都走这里）。"""
        if not hasattr(self, 'borrowing'):
            self.borrowing = {}
        if lender is None and getattr(self, 'friends', None):
            with self.store.connect() as conn:
                owned = conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()
            if borrow_uid or not owned:
                lender = self.friends.borrowed_unused(uid, hero_id, borrow_uid or None)
        if lender:
            self.borrowing[uid] = (lender, hero_id)
        else:
            self.borrowing.pop(uid, None)

    def growth(self, uid, hero_id):
        """出战英雄的星级/觉醒/熟练度/已装配天赋，进战斗时下发给客户端计算加成。助力英雄用好友的养成数据。"""
        b = getattr(self, 'borrowing', {}).get(uid)
        if b and b[1] == hero_id:
            uid = b[0]
        with self.store.connect() as conn:
            try:
                row = conn.execute('SELECT star,awake,proficiency FROM heroes WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchone()
            except sqlite3.OperationalError:
                row = None
            try:
                use = [r[0] for r in conn.execute('SELECT tid FROM hero_talent_use WHERE uid=? AND hero_id=? ORDER BY slot', (uid, hero_id))]
                levels = dict(conn.execute('SELECT tid,level FROM hero_talents WHERE uid=? AND hero_id=?', (uid, hero_id)).fetchall())
            except sqlite3.OperationalError:
                use, levels = [], {}
        star, awake, prof = row or (0, 0, 0)
        # talentIdList 是 TalentBaseVo.Id（含等级），由 Tid+当前等级换算。
        ids = []
        for tid in use:
            lv = levels.get(tid, 1)
            match = next((r['Id'] for r in self.tables['TalentBaseVo'] if r['Tid'] == tid and r['Lv'] == lv), None)
            if match:
                ids.append(match)
        return star, awake, prof, ids

    def local_level(self):
        """与登录一致：ExpBaseVo 末项（Exp=-1）即最高等级。"""
        return max(row['Id'] for row in self.tables['ExpBaseVo'])

    def local_rank(self):
        """与登录一致：RankBaseVo 最高军衔及其军功。"""
        row = max(self.tables['RankBaseVo'], key=lambda r: r['Id'])
        return row['Id'], row['Num']

    def game(self, message, uid):
        """返回响应及待推送消息；先发响应，再推送状态，避免顺序混乱。"""
        with self.lock:
            reply = self.answer(message, uid)
            jobs = []
            kind = message.type
            if kind == 3036:
                req = message.request.teamRefuseRequest
                room = self.room_for(req.applyUid)
                if req.teamId != room.room_id or not self.is_human(uid):
                    raise ValueError('LOCAL_INVITE_ACCESS_DENIED')
                notice = self.pb.Message(type=3037)
                notice.response.errcode = ''
                notice.response.teamByRefuseResponse.refuseUid = uid
                notice.response.teamByRefuseResponse.refuseName = self.user(uid)['name']
                peer = self.game_peers.get(req.applyUid)
                return reply, [(peer, notice)] if peer else []
            if kind == 3033:
                req = message.request.teamApplyRequest
                room = self.room_for(uid)
                if req.teamId != room.room_id or uid != room.leader or room.state != 'waiting':
                    raise ValueError('LOCAL_BAD_ROOM_STATE')
                if not self.is_human(req.byApplyUid) or req.byApplyUid == uid:
                    raise ValueError('LOCAL_INVITE_ACCESS_DENIED')
                if getattr(room, 'team_type', 1) == 0:
                    if room.mode == 3:
                        raise ValueError('LOCAL_1V1_NO_PARTY')
                    if len(room.members) >= self.mode_shape(room.mode)[1]:
                        raise ValueError('LOCAL_PARTY_FULL')
                    if any(r == room.room_id for r, _ in self.queue.get(room.mode, [])):
                        raise ValueError('LOCAL_ALREADY_MATCHING')
                peer = self.game_peers.get(req.byApplyUid)
                if peer is None:
                    raise ValueError('LOCAL_PLAYER_OFFLINE')
                notice = self.pb.Message(type=3034)
                notice.response.errcode = ''
                data = notice.response.teamByApplyResponse
                data.teamId, data.battleType = room.room_id, room.mode
                data.teamType = getattr(room, 'team_type', 1)  # 组队(0)与开房间(1)界面不同，按房间实际类型下发
                data.uid, data.name, data.headImage = uid, self.user(uid)['name'], 1
                data.rankId = self.local_rank()[0]
                data.maxRankId = data.rankId
                return reply, [(peer, notice)]
            if kind == 3001:
                previous = self.uid_rooms.get(uid)
                if previous and self.rooms.rooms[previous].state not in ('closed', 'ended'):
                    stale = self.rooms.rooms[previous]
                    # 客户端重启或返回大厅后旧等待房间残留：未开战时自动退出，不影响进行中对局。
                    if stale.state != 'waiting' or stale.room_id in self.matched:
                        # 客户端已回到大厅（中途退出/结算后）而旧对局仍在进行：只解除该账号与旧房间的绑定，旧对局照常走完。
                        self.detach(uid)
                    else:
                        self.rooms.leave(stale.room_id, uid)
                        self.uid_rooms.pop(uid, None)
                        self.lobby_ready.discard(uid)
                        self.selected.pop(uid, None)
                        humans = [m for m in stale.members if not m.startswith('local-bot-')]
                        if not humans:
                            self.rooms.set_state(stale, 'closed')
                            for member in stale.members:
                                self.lobby_ready.discard(member)
                        else:
                            jobs = self.push(stale, self.team_list(stale), self.game_peers)
                req = message.request.teamCreateRequest
                # 禁选(BanPick)模式本地按普通选人处理。
                if req.battleType not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 18, 20, 22, 23, 24, 101):
                    raise ValueError(f'LOCAL_MODE_UNSUPPORTED:{req.battleType}:{int(req.isBanPick)}')
                if not REWARDS.mode_open(req.battleType):
                    raise ValueError('LOCAL_MODE_CLOSED')  # 克隆大战仅周末12-14点、能源基地每天20-24点
                room = self.rooms.create(uid, 'client-seeded', req.battleType)
                self.seats[room.room_id] = {uid: [1, 1]}
                room.team_type = req.teamType
                room.ban_pick = bool(req.isBanPick)  # 0=匹配（开局时自动补机器人），1=自建房
                self.uid_rooms[uid] = room.room_id
                self.lobby_ready.add(uid)
                reply.response.teamCreateResponse.teamId = room.room_id
                jobs = jobs + self.push(room, self.team_list(room), self.game_peers)
            elif kind == 3035:
                req = message.request.teamJoinRequest
                room = self.rooms.rooms.get(req.teamId)
                if room is None or room.mode != req.battleType:
                    raise ValueError('LOCAL_ROOM_NOT_JOINABLE')
                previous = self.uid_rooms.get(uid)
                if previous and previous != room.room_id and self.rooms.rooms[previous].state not in ('closed', 'ended'):
                    old = self.rooms.rooms[previous]
                    if old.state == 'waiting' and previous not in self.matched:
                        self.rooms.leave(previous, uid)
                    self.detach(uid)
                self.rooms.join(room.room_id, uid)
                self.uid_rooms[uid] = room.room_id
                jobs = self.push(room, self.team_list(room), self.game_peers)
            else:
                room = self.room_for(uid)
                if kind == 2002:
                    reply.response.teamListResponse.CopyFrom(self.team_list(room).response.teamListResponse)
                elif kind == 2006:
                    if uid != room.leader or room.state != 'waiting' or room.room_id in self.matched:
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    if len(room.members) >= 10:
                        raise ValueError('LOCAL_ROOM_FULL')
                    requested = message.request.teamRobotAddRequest.tId
                    if requested not in (1, 2):
                        raise ValueError('LOCAL_BOT_TEAM_SLOT_UNSUPPORTED')
                    bot_seat = self.free_seat(room, requested)
                    number = next(n for n in range(1, 11) if f'local-bot-{room.room_id}-{n}' not in room.members)
                    bot = f'local-bot-{room.room_id}-{number}'
                    with self.store.connect() as conn:
                        columns = {row[1] for row in conn.execute('PRAGMA table_info(accounts)')}
                        if 'account' in columns:
                            conn.execute('INSERT OR IGNORE INTO accounts(account,uid,name,silver,gold,diamonds,friendship) VALUES(?,?,?,?,?,?,?)',
                                         (bot, bot, f'本地机器人{number}', 0, 0, 0, 0))
                        else:
                            conn.execute('INSERT OR IGNORE INTO accounts(uid,name,silver) VALUES(?,?,0)',
                                         (bot, f'本地机器人{number}'))
                    self.rooms.join(room.room_id, bot)
                    self.seats.setdefault(room.room_id, {})[bot] = bot_seat
                    self.lobby_ready.add(bot)
                    reply.response.teamRobotAddResponse.commonUpdate.SetInParent()
                    jobs = self.push(room, self.team_list(room), self.game_peers)
                elif kind == 2007:
                    if uid != room.leader or room.state != 'waiting' or room.room_id in self.matched:
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    req = message.request.teamRobotRemoveRequest
                    bot = next((m for m in room.members if self.seat(room, m) == [req.tId, req.pos]), None)
                    if bot is None or not bot.startswith('local-bot-'):
                        raise ValueError('LOCAL_INVALID_BOT_SLOT')
                    self.rooms.leave(room.room_id, bot)
                    self.seats.get(room.room_id, {}).pop(bot, None)
                    self.lobby_ready.discard(bot)
                    jobs = self.push(room, self.team_list(room), self.game_peers)
                elif kind == 2008:
                    req = message.request.teamPosChangeRequest
                    if room.state != 'waiting' or room.room_id in self.matched:
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    if req.tId not in (1, 2) or not 1 <= req.pos <= 5:
                        raise ValueError('LOCAL_INVALID_SEAT')
                    self.seat(room, uid)
                    if any(self.seat(room, m) == [req.tId, req.pos] for m in room.members if m != uid):
                        raise ValueError('LOCAL_SEAT_TAKEN')
                    self.seats[room.room_id][uid] = [req.tId, req.pos]
                    jobs = self.push(room, self.team_list(room), self.game_peers)
                elif kind == 2010:
                    if room.state != 'waiting' or room.room_id in self.matched:
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    self.lobby_ready.add(uid)
                    jobs = self.push(room, self.team_list(room), self.game_peers)
                elif kind == 2004:
                    if uid != room.leader or room.state != 'waiting' or room.room_id in self.matched:
                        raise ValueError('LOCAL_ROOM_NOT_READY')
                    if not set(room.members).issubset(self.lobby_ready):
                        raise ValueError('LOCAL_ROOM_NOT_READY')
                    if not self.resources_ready():
                        raise ValueError('LOCAL_RESOURCE_MISSING')
                    if getattr(room, 'team_type', 1) == 0:
                        # 匹配类：进入队列等待真人，由 tick 中的撮合器开局。
                        if any(r == room.room_id for r, _ in self.queue.get(room.mode, [])):
                            raise ValueError('LOCAL_ALREADY_MATCHING')
                        self.queue.setdefault(room.mode, []).append((room.room_id, time.time()))
                    else:
                        self.matched.add(room.room_id)
                        jobs = self.push(room, self.match_info(room), self.game_peers)
                elif kind == 2001 and room.state == 'waiting':
                    if room.room_id in self.matched:
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    self.rooms.leave(room.room_id, uid)
                    self.uid_rooms.pop(uid, None)
                    self.lobby_ready.discard(uid)
                    self.selected.pop(uid, None)
                    jobs = self.push(room, self.team_list(room), self.game_peers)
                elif kind == 2005:
                    if room.state != 'waiting' or uid != room.leader:
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    self.matched.discard(room.room_id)
                    self.queue[room.mode] = [(r, t) for r, t in self.queue.get(room.mode, []) if r != room.room_id]
                    for member in room.members:
                        self.battle_peers.pop(member, None)
                    room.ready.clear()
                    jobs = self.push(room, self.team_list(room), self.game_peers)
                elif kind == 2001:
                    if room.state not in ('waiting', 'ended'):
                        raise ValueError('LOCAL_BAD_ROOM_STATE')
                    if uid != room.leader:
                        raise ValueError('LOCAL_LEADER_REQUIRED')
                    self.rooms.set_state(room, 'closed')
                    for member in room.members:
                        self.uid_rooms.pop(member, None)
                        self.lobby_ready.discard(member)
                    self.matched.discard(room.room_id)
                elif kind == 3047:
                    req = message.request.battleStartRequest
                    if req.battleId != room.room_id or room.state != 'running' or req.heroId != self.hero(uid)[0]:
                        raise ValueError('LOCAL_BAD_BATTLE')
                    reply.response.battleStartResponse.commonUpdate.SetInParent()
                elif kind == 3002:
                    if message.request.battleEndRequest.battleId != room.room_id:
                        raise ValueError('LOCAL_BATTLE_ACCESS_DENIED')
                    # 原生单真人模式不发1004，由大厅结算请求完成本地对局。
                    # 至少经过五分钟的服务端帧，避免提前提交取得奖励。
                    # 胜负由客户端按模式规则判定（如1V1先到10分、排位1000分），可能早于时长结束；
                    # 对局实际进行满30秒后接受客户端的结束上报（本地可信环境），提前更早的请求仍拒绝。
                    if room.state == 'running' and room.frame_id >= 30 * core.FRAME_RATE:
                        self.finish_room(room, room.leader)
                        jobs = self.push(room, self.battle_status(room, 4), self.battle_peers) + self.push(room, self.end_notice(), self.battle_peers)
                    if room.state != 'ended':
                        raise ValueError('LOCAL_BATTLE_NOT_ENDED')
                    result = self.settle(room, uid, message.request.battleEndRequest)
                    b = getattr(self, 'borrowing', {}).pop(uid, None)
                    if b and getattr(self, 'friends', None):
                        try:
                            self.friends.use_borrowed(uid, b[1], b[0])   # 助力英雄只能用一局
                        except Exception:
                            pass
                    user = self.user(uid)
                    data = reply.response.battleEndResponse
                    data.arenaUser.RankId = data.arenaUser.MaxRankId = self.local_rank()[0]
                    data.arenaUser.RankScore = user.get('rank_score', self.local_rank()[1])
                    data.arenaUser.MaxRankScore = max(data.arenaUser.RankScore, self.local_rank()[1])
                    data.userLevelUpdate.level = self.local_level()
                    data.userLevelUpdate.exp = 0
                    data.commonUpdate.itemList.add(ItemType=1, ItemId=0, ItemNum=user['silver'])
                    for kind, item, count in result['items']:
                        data.commonUpdate.itemList.add(ItemType=kind, ItemId=item, ItemNum=count)
                    data.addCoin = result['silver']
                    data.addRankScore = result['rank_score']
                    if room.mode in (11, 13):   # 荣耀之路军功：匹配/排位 胜 +12、负 +4
                        data.addMaxRankScore = 12 if message.request.battleEndRequest.isWin else 4
                    data.addProficiency = self.PROFICIENCY_PER_BATTLE
            return reply, jobs

    def battle(self, message, peer):
        from google.protobuf.json_format import MessageToDict
        with self.lock:
            uid = getattr(peer, 'uid', None)
            if message.type in (1000, 1017):
                uid = message.uid
                room = self.room_for(uid)
                request_id = message.request.battleLoginRequest.battleId if message.type == 1000 else message.request.battleReLoginRequest.battleId
                if request_id != room.room_id or room.room_id not in self.matched or uid not in self.game_peers:
                    raise ValueError('LOCAL_BATTLE_ACCESS_DENIED')
                peer.uid = uid
                self.battle_peers[uid] = peer
                humans = [m for m in room.members if not m.startswith('local-bot-')]
                if getattr(room, 'ban_pick', False) and getattr(room, 'bp', None) is None and room.state == 'waiting' \
                        and all(h in self.battle_peers for h in humans) and not getattr(room, 'bp_done', False):
                    room.bp_done = True
                    return self.answer(message, uid), self.bp_start(room)
                return self.answer(message, uid), self.push(room, self.battle_status(room, 0 if room.state == 'waiting' else 2), self.battle_peers)
            if not uid:
                raise ValueError('LOCAL_LOGIN_REQUIRED')
            room = self.room_for(uid)
            reply, jobs = self.answer(message, uid), []
            bp = getattr(room, 'bp', None)
            if message.type in (1011, 1012, 1013, 1016):
                if not bp:
                    return reply, []  # BP 已结束（超时自动推进），迟到的操作静默忽略，不弹错
                if message.type == 1016:
                    # 禁选/选人阶段的“想禁/想选”意向：原样转给同队真人（客户端在头像旁显示提示），并回给自己。
                    # 原来直接回空响应，客户端拿到空 uid 去比对，空位条目会报空引用。
                    req = message.request.battleBanPickSwitchRequest
                    info = reply.response.battleBanPickSwitchResponse
                    info.uid, info.switchType = uid, req.switchType
                    info.switchHeroId, info.switchSkinId, info.switchBorrowUid = req.switchHeroId, req.switchSkinId, req.switchBorrowUid
                    team = self.seat(room, uid)[0]
                    push = self.pb.Message(type=1016)
                    push.CopyFrom(reply)
                    push.seq = 0
                    mates = [m for m in room.members if m != uid and self.seat(room, m)[0] == team and m in self.battle_peers]
                    return reply, [(self.battle_peers[m], push) for m in mates]
                kind, members = self.bp_turn_members(room)
                if message.type == 1011:
                    if kind != 'ban' or uid not in members:
                        return reply, []  # 非本人回合/已超时：静默忽略
                    bp['bans'][self.seat(room, uid)[0]] = message.request.battleBanRequest.heroId
                    return reply, self.bp_advance(room)
                if message.type == 1012:
                    req = message.request.battlePickRequest
                    if kind != 'pick' or uid not in members or uid in bp['picked']:
                        return reply, []  # 非本人回合/已超时：静默忽略
                    if req.heroId in bp['bans'].values() or any(self.hero(u)[0] == req.heroId for u in bp['picked']):
                        raise ValueError('LOCAL_HERO_TAKEN')
                    self.selected[uid] = (req.heroId, req.skinId)
                    self.note_borrow(uid, req.heroId, req.borrowUid)
                    bp['picked'].add(uid)
                    if all(u in bp['picked'] for u in members):
                        return reply, self.bp_advance(room)
                    return reply, self.push(room, self.battle_status(room, 0), self.battle_peers)
                if message.type == 1013:
                    if kind != 'ready':
                        return reply, []  # 非本人回合/已超时：静默忽略
                    bp['ready'].add(uid)
                    return reply, self.push(room, self.battle_status(room, 0), self.battle_peers) + self.bp_check_ready(room)
            if message.type == 1005:
                req = message.request.battleSelHeroRequest
                if room.state != 'waiting':
                    raise ValueError('LOCAL_BAD_ROOM_STATE')
                # 已确认/已在BP中选定英雄后，只允许换同一英雄的皮肤（原版选人界面确认后仍可换皮肤）。
                locked = uid in room.ready or (bp and uid in bp['picked'])
                if locked and self.selected.get(uid, (None,))[0] != req.heroId:
                    raise ValueError('LOCAL_BAD_ROOM_STATE')
                with self.store.connect() as conn:
                    owned = conn.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, req.heroId)).fetchone()
                lender = None
                if getattr(self, 'friends', None) and (req.borrowUid or not owned):
                    lender = self.friends.borrowed_unused(uid, req.heroId, req.borrowUid or None)
                if (not owned and not lender) or not any(row['Id'] == req.skinId and row['HeroId'] == req.heroId for row in self.tables['SkinIdBaseVo']):
                    raise ValueError('LOCAL_HERO_UNAVAILABLE')
                self.selected[uid] = (req.heroId, req.skinId)
                self.note_borrow(uid, req.heroId, req.borrowUid, lender)
                jobs = self.push(room, self.battle_status(room, 0), self.battle_peers)
            elif message.type == 1006 and bp:
                # BP 房间里客户端若走了普通确认：选人已做则当作准备。
                if uid in self.selected:
                    bp['picked'].add(uid)
                bp['ready'].add(uid)
                kind, members = self.bp_turn_members(room)
                if kind == 'pick' and all(u in bp['picked'] for u in members):
                    jobs = self.bp_advance(room)
                else:
                    jobs = self.push(room, self.battle_status(room, 0), self.battle_peers)
            elif message.type == 1006:
                self.rooms.set_ready(room.room_id, uid)
                for bot in room.members:
                    if bot.startswith('local-bot-'):
                        self.rooms.set_ready(room.room_id, bot)
                if room.ready == set(room.members):
                    self.rooms.begin_loading(room.room_id, room.leader)
                    room.loaded.update(bot for bot in room.members if bot.startswith('local-bot-'))
                    jobs = self.push(room, self.battle_status(room, 2), self.battle_peers)
            elif message.type == 1008:
                percent = message.request.battleLoadingRequest.percent
                if not 0 <= percent <= 100 or room.state != 'loading':
                    raise ValueError('LOCAL_BAD_ROOM_STATE')
                update = self.pb.Message(type=1008)
                update.response.errcode = ''
                update.response.battleLoadingResponse.uid, update.response.battleLoadingResponse.percent = uid, percent
                jobs = self.push(room, update, self.battle_peers)
                if percent == 100 and self.rooms.loaded(room.room_id, uid) == 'running':
                    begin = self.pb.Message(type=1003)
                    begin.response.errcode = ''
                    begin.response.battleBeginResponse.startTime = int(time.time())
                    begin.response.battleBeginResponse.duration = self.duration(room)
                    jobs += self.push(room, begin, self.battle_peers)
                return None, jobs  # 只发送带uid/percent的广播，空1008响应会触发原生空引用。
            elif message.type == 1001:
                req = message.request.battleSyncRequest
                if req.battleId != room.room_id:
                    raise ValueError('LOCAL_BATTLE_ACCESS_DENIED')
                self.rooms.input(room.room_id, uid, [MessageToDict(msg, preserving_proto_field_name=True) for msg in req.msgs])
                return None, []  # 同步响应由定时关键帧广播产生，不伪造即时帧。
            elif message.type == 1018:
                req = message.request.battleFrameListRequest
                if req.battleId != room.room_id:
                    raise ValueError('LOCAL_BATTLE_ACCESS_DENIED')
                frames = self.rooms.frames(room.room_id, uid, max(1, req.startFrameId), req.limit)
                data = reply.response.battleFrameListResponse
                for frame in frames:
                    data.frameList.add().CopyFrom(self.frame_message(frame).response.battleSyncResponse)
                data.isEnd = not frames or frames[-1]['FrameId'] >= room.frame_id
            elif message.type == 1004:
                if room.state == 'ended':
                    return reply, []  # 已由房主或服务端超时结束，非房主的结束请求只确认。
                self.finish_room(room, uid)
                jobs = self.push(room, self.battle_status(room, 4), self.battle_peers)
                jobs += self.push(room, self.end_notice(), self.battle_peers)
            elif message.type == 1009:
                # 战斗服结束通知常比大厅 3002 早到：对局满 30 秒就直接结束房间；否则静默确认，不再弹错
                if message.request.battleServerBattleEndRequest.battleId == room.room_id and room.state == 'running' \
                        and room.frame_id >= 30 * core.FRAME_RATE:
                    self.finish_room(room, room.leader)
                    jobs = self.push(room, self.battle_status(room, 4), self.battle_peers) + self.push(room, self.end_notice(), self.battle_peers)
            elif message.type not in (1010,):
                raise ValueError('LOCAL_UNSUPPORTED')
            return reply, jobs

    PROFICIENCY_PER_BATTLE = 10  # 本地规则：每局结算给出战英雄固定熟练度，非官方公式。

    def finish_room(self, room, uid):
        self.rooms.finish(room.room_id, uid)
        with self.store.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS proficiency_log(room_id INTEGER, uid TEXT, PRIMARY KEY(room_id, uid))')
            for member in room.members:
                if member.startswith('local-bot-'):
                    continue
                if conn.execute('INSERT OR IGNORE INTO proficiency_log VALUES(?,?)', (room.room_id, member)).rowcount:
                    try:
                        conn.execute('UPDATE heroes SET proficiency=proficiency+? WHERE uid=? AND hero_id=?',
                                     (self.PROFICIENCY_PER_BATTLE, member, self.hero(member)[0]))
                    except sqlite3.OperationalError:
                        pass  # 合成测试库的 heroes 表没有熟练度列。

    SERVICE_NAMES = ('passport', 'features', 'features2', 'features3', 'events', 'exchange', 'friends', 'friends_mod', 'crosscheck', 'mailer')

    def attach(self, **services):
        """开服时一次性注入房间用到的外部业务（通行证、宝箱、好友助力、交叉核对、发邮件等），名单之外的拒绝。"""
        unknown = set(services) - set(self.SERVICE_NAMES)
        if unknown:
            raise TypeError(f'房间不接受这些依赖：{sorted(unknown)}')
        for name, value in services.items():
            setattr(self, name, value)

    def settle(self, room, uid, req):
        self.settlement.configure(crosscheck=getattr(self, 'crosscheck', None),
            features2=getattr(self, 'features2', None), passport=getattr(self, 'passport', None),
            mailer=getattr(self, 'mailer', None))
        result = self.settlement.settle(room, uid, req)
        self.last_reward = self.settlement.last_reward
        return result

    def recompute_reward(self, p, req_like):
        return self.settlement.recompute_reward(p, req_like)

    def deduct_reward(self, conn, uid, diff):
        return self.settlement.deduct_reward(conn, uid, diff)

    def revoke_room_reward(self, conn, room_id, uid, reward):
        return self.settlement.revoke_room_reward(conn, room_id, uid, reward)

    def mail(self, conn, uid, title, content):
        return self.settlement.mail(conn, uid, title, content)

    def end_notice(self):
        return self.sync.end_notice()

    def frame_message(self, frame):
        return self.sync.frame_message(frame)

    def rooms_core_tick(self, room_ids):
        return self.rooms.tick_many(room_ids)

    def frame_recipients(self, room_id):
        room = self.rooms.rooms[room_id]
        return [self.battle_peers[u] for u in room.members if u in self.battle_peers]

    @staticmethod
    def deliver(jobs):
        for peer, message in jobs:
            try:
                peer.send_message(message)
            except OSError:
                continue  # 客户端可从数据库请求补帧，不回滚已经提交的帧。

    def tick(self):
        jobs = []
        with self.lock:
            running = []
            jobs += self.matchmake(time.time())
            for room in list(self.rooms.rooms.values()):
                if getattr(room, 'bp', None) and time.time() > room.bp['deadline']:
                    try:
                        jobs += self.bp_timeout(room)
                    except ValueError:
                        room.bp = None  # 状态已被其他流程推进，放弃BP
                    continue
                if room.state == 'running':
                    humans = [m for m in room.members if not m.startswith('local-bot-')]
                    # 多真人对局客户端到时不发1004，由服务端按1003下发的300秒时长判定结束（另加3秒宽限）。
                    if len(humans) > 1 and room.frame_id >= (self.duration(room) + 3) * core.FRAME_RATE:
                        self.finish_room(room, room.leader)
                        jobs += self.push(room, self.battle_status(room, 4), self.battle_peers)
                        # 客户端 BattleProxy 监听1004后调用 GameManager.endGame 进入结算。
                        jobs += self.push(room, self.end_notice(), self.battle_peers)
                        continue
                    running.append(room.room_id)
            jobs += self.sync.collect(running)
        self.deliver(jobs)

    def run(self, report):
        interval = 1 / core.FRAME_RATE
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            deadline += interval
            try:
                self.tick()
            except Exception as exc:
                report(event='battle_tick_error', error=type(exc).__name__)
            remaining = deadline - time.monotonic()
            if remaining < -interval * 3:
                report(event='battle_tick_late')
                deadline = time.monotonic()
            self.stop_event.wait(max(0, remaining))
