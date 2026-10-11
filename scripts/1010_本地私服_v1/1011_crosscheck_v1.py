"""防刷：多人交叉核对（本地规则）。
依据：帧同步对局，每个真人结算时都上报整局战报 BattleRecord（含所有玩家的队伍/击杀/死亡/助攻/评分与胜利队伍）。
- 输赢：以所有战报里“胜利队伍”的多数为准；票数相同为争议局：本局所有人都不发奖励（先结算的扣回），记入争议记录并给同局所有真人发邮件；自己战报自相矛盾（自报赢、但自己战报里胜利队伍不是本队）也按输。
- 击杀/助攻：取所有战报对该玩家的最小值，容差 KILL_TOL 内不算异常。
- MVP：多数战报认为他是本队评分最高者才算（票数相同不算）；被撤销的 MVP 让出本队名额。
- 先结算的人后来被别人的战报推翻：按纠正后的数据重算，补扣多拿的部分（扣到 0 为止），并发邮件说明。
所有异常写入 memory/1011_可疑对局_v1.jsonl。"""
import collections
import json
import time
from pathlib import Path
from types import SimpleNamespace

KILL_TOL = 2
FIELDS = ('isWin', 'isMvp', 'killNum', 'assistNum', 'shuchu', 'tuijin', 'shengcun', 'zhiyuan', 'round', 'beKilledNum')


MODE_NAME = {11: '排位赛', 13: '匹配赛', 1: '1V1', 2: '3V3', 3: '个人混战', 4: '5V5推塔', 5: '据点之争', 7: '歼灭战', 9: '5V5资源战', 22: '宝石争夺'}


class CrossCheck:
    item_names = {}   # (类型, ID) -> 名称，由服务器注入（ItemBaseVo）
    names = staticmethod(lambda uid: uid)   # uid -> 玩家名，由服务器注入

    def fmt_items(self, d):
        base = {'silver': '银币', 'gold': '金币', 'diamonds': '钻石'}
        out = []
        for k, v in d.items():
            if not v:
                continue
            key = tuple(k) if isinstance(k, list) else k
            out.append(f'{base.get(key) or self.item_names.get(key, str(key))}×{v}')
        return '、'.join(out) or '无'

    def report_table(self, reps):
        lines = []
        for u, r in reps:
            me = next((x for x in r.battleRecordUserList if x.uid == u), None)
            side = f'{me.tid}队' if me else '?'
            lines.append(f'· {self.names(u)}（{side}）上报：{r.winTeam}队胜' + (f'，自己 {me.killNum}杀/{me.deadNum}死/{me.assistNum}助攻' if me else ''))
        return '\n'.join(lines)

    def __init__(self, pb, log_path, mailer=None, revoke=None):
        self.pb = pb
        self.mailer = mailer
        self.revoke = revoke
        self.log_path = Path(log_path)

    def tables(self, conn):
        # executescript 会隐式提交调用方事务；逐条建表才能一起回滚结算。
        conn.execute('CREATE TABLE IF NOT EXISTS room_reports(room_id INTEGER, uid TEXT, record BLOB, PRIMARY KEY(room_id, uid))')
        conn.execute('CREATE TABLE IF NOT EXISTS reward_log(room_id INTEGER, uid TEXT, params TEXT, reward TEXT, PRIMARY KEY(room_id, uid))')
        conn.execute('CREATE TABLE IF NOT EXISTS report_rosters(room_id INTEGER PRIMARY KEY, members TEXT NOT NULL)')

    def log(self, **kw):
        kw['time'] = int(time.time())
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(kw, ensure_ascii=False) + '\n')

    def reports(self, conn, room_id):
        out = []
        for uid, blob in conn.execute('SELECT uid, record FROM room_reports WHERE room_id=?', (room_id,)):
            try:
                out.append((uid, self.pb.BattleRecord.FromString(blob)))
            except Exception as exc:
                raise ValueError('LOCAL_REPORT_CORRUPT') from exc
        return out

    def consensus(self, reps, uid):
        """返回 {'win': bool|None, 'kill': int|None, 'assist': int|None, 'top': bool|None, 'n': 报告数}"""
        wins = collections.Counter(r.winTeam for _, r in reps if r.winTeam)
        tids, kills, assists, top = [], [], [], []
        for _, r in reps:
            users = list(r.battleRecordUserList)
            me = next((x for x in users if x.uid == uid), None)
            if not me:
                continue
            tids.append(me.tid); kills.append(me.killNum); assists.append(me.assistNum)
            best = max((x.score for x in users if x.tid == me.tid), default=me.score)
            top.append(me.score >= best)
        if not tids:
            return {'win': None, 'kill': None, 'assist': None, 'top': None, 'n': 0}
        tid = collections.Counter(tids).most_common(1)[0][0]
        win = None
        if wins:
            ranked = wins.most_common()
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                win = None    # 战报对“哪队赢”各执一词且票数相同：争议局——不改任何人的输赢（不让诚实一方受罚），记录并邮件通知
            else:
                win = tid == ranked[0][0]
        # MVP：多数战报认为他是本队最高分才算；票数相同（如两人各执一词）不算。避免一份伪造战报单方面否掉别人。
        yes, no = top.count(True), top.count(False)
        return {'win': win, 'kill': min(kills), 'assist': min(assists), 'top': yes > no, 'n': len(tids),
                'disputed': len(wins) > 1 and wins.most_common()[0][1] == wins.most_common()[1][1]}

    def correct(self, req_like, cons, others):
        """按共识修正（原地修改），返回修正说明列表。others=是否有别人的战报。"""
        notes = []
        if cons['win'] is not None and bool(req_like.isWin) and not cons['win']:
            req_like.isWin = False; notes.append('自报胜利，战报为失败')
        if cons['kill'] is not None and req_like.killNum > cons['kill'] + KILL_TOL:
            notes.append(f'击杀 {req_like.killNum}→{cons["kill"]}'); req_like.killNum = cons['kill']
        if cons['assist'] is not None and req_like.assistNum > cons['assist'] + KILL_TOL:
            notes.append(f'助攻 {req_like.assistNum}→{cons["assist"]}'); req_like.assistNum = cons['assist']
        if others and cons['top'] is False and bool(req_like.isMvp):
            req_like.isMvp = False; notes.append('自报MVP，战报不是本队最高分')
        return notes

    def before_settle(self, conn, room_id, uid, req, expected_uids=None):
        """存下本人战报，并按当前所有战报修正本人的上报数据。"""
        self.tables(conn)
        if expected_uids is not None:
            members = sorted(set(expected_uids))
            if uid not in members:
                raise ValueError('LOCAL_REPORT_ACCESS_DENIED')
            conn.execute('INSERT OR IGNORE INTO report_rosters VALUES(?,?)', (room_id, json.dumps(members)))
        if req.HasField('record'):
            conn.execute('INSERT OR REPLACE INTO room_reports VALUES(?,?,?)', (room_id, uid, req.record.SerializeToString()))
        reps = self.reports(conn, room_id)
        cons = self.consensus(reps, uid)
        roster = conn.execute('SELECT members FROM report_rosters WHERE room_id=?', (room_id,)).fetchone()
        complete = not roster or set(json.loads(roster[0])).issubset({u for u, _ in reps})
        notes = self.correct(req, cons, any(u != uid for u, _ in reps)) if complete else []
        if notes:
            self.log(room=room_id, uid=uid, stage='结算前修正', notes=notes, reports=cons['n'])
        self.check_dispute(conn, room_id, reps)
        return notes

    def is_disputed(self, conn, room_id):
        conn.execute('CREATE TABLE IF NOT EXISTS disputes(room_id INTEGER PRIMARY KEY, time INTEGER, detail TEXT)')
        return bool(conn.execute('SELECT 1 FROM disputes WHERE room_id=?', (room_id,)).fetchone())

    def check_dispute(self, conn, room_id, reps):
        wins = collections.Counter(r.winTeam for _, r in reps if r.winTeam)
        if len(wins) < 2 or wins.most_common()[0][1] != wins.most_common()[1][1]:
            return
        # 三人对局的前两份战报打平不是最终争议，等待实际真人名单报齐。
        roster = conn.execute('SELECT members FROM report_rosters WHERE room_id=?', (room_id,)).fetchone()
        if roster and not set(json.loads(roster[0])).issubset({u for u, _ in reps}):
            return
        conn.execute('CREATE TABLE IF NOT EXISTS disputes(room_id INTEGER PRIMARY KEY, time INTEGER, detail TEXT)')
        detail = {u: r.winTeam for u, r in reps}
        if conn.execute('SELECT 1 FROM disputes WHERE room_id=?', (room_id,)).fetchone():
            conn.execute('UPDATE disputes SET detail=? WHERE room_id=?', (json.dumps(detail), room_id))
            return
        conn.execute('INSERT INTO disputes VALUES(?,?,?)', (room_id, int(time.time()), json.dumps(detail)))
        taken = {}
        if self.revoke:   # 争议局不发任何东西：已经先结算的人全部扣回
            for other, reward in conn.execute('SELECT uid, reward FROM reward_log WHERE room_id=?', (room_id,)).fetchall():
                old_r = {tuple(k) if isinstance(k, list) else k: v for k, v in json.loads(reward)}
                taken[other] = {k: v for k, v in old_r.items() if v > 0}
                self.revoke(conn, room_id, other, taken[other])
                conn.execute('UPDATE reward_log SET reward=? WHERE room_id=? AND uid=?', ('[]', room_id, other))
        self.log(room=room_id, uid='全体', stage='争议对局', notes=['各玩家战报的胜利队伍不一致：' + '，'.join(f'{u}报{t}队胜' for u, t in detail.items())])
        if self.mailer:
            humans = {u for u, _ in reps}
            for _, r in reps:
                humans |= {x.uid for x in r.battleRecordUserList if not x.isRobot and not x.uid.startswith('local-bot-')}
            when = time.strftime('%Y-%m-%d %H:%M')
            for u in humans:
                mine = taken.get(u)
                self.mailer(conn, u, f'争议对局通知（第{room_id}局）',
                            f'对局：第 {room_id} 局，{when} 结算\n'
                            f'情况：同局真人玩家上报的“哪一队获胜”不一致，且双方票数相同，系统无法判断真实结果。\n'
                            f'各玩家上报：\n{self.report_table(reps)}\n'
                            '处理：本局记为争议对局，按规则本局所有玩家都不发任何奖励——银币、金币、钻石、材料、军功、战斗宝箱、通行证经验都不发；'
                            '本局也不计入荣耀之路、赛季段位和赛季宝珠。战绩照常保留。\n'
                            + (f'已对本局奖励执行回收：{self.fmt_items(mine)}（另含本局军功、战斗宝箱、通行证经验）。余额不足或已兑换的部分请房主查看 reward_recovery_pending；已领取通行证等级奖励需要人工核对。\n' if mine else '你在本局没有领到奖励，无需扣回。\n')
                            + '查看：大厅右上角“违规举报”按钮（已改为争议对局查看）。\n'
                            '如果是网络掉线、中途退出等原因造成的误判，请联系房主核对。')

    def save(self, conn, room_id, uid, req, params, reward):
        self.tables(conn)
        snap = {k: (bool(getattr(req, k)) if k in ('isWin', 'isMvp') else int(getattr(req, k))) for k in FIELDS}
        conn.execute('INSERT OR REPLACE INTO reward_log VALUES(?,?,?,?)', (room_id, uid, json.dumps({**params, 'req': snap}),
                                                                         json.dumps([[list(k) if isinstance(k, tuple) else k, v] for k, v in reward.items()])))

    def recheck_others(self, conn, room_id, uid, compute, deduct, mail):
        """新战报到达后，复查同房间已结算的其他真人。"""
        reps = self.reports(conn, room_id)
        roster = conn.execute('SELECT members FROM report_rosters WHERE room_id=?', (room_id,)).fetchone()
        if roster and not set(json.loads(roster[0])).issubset({u for u, _ in reps}):
            return
        for other, params, reward in conn.execute('SELECT uid, params, reward FROM reward_log WHERE room_id=? AND uid!=?', (room_id, uid)).fetchall():
            p = json.loads(params)
            old = SimpleNamespace(**p['req'])
            new = SimpleNamespace(**p['req'])
            notes = self.correct(new, self.consensus(reps, other), True)
            if not notes:
                continue
            old_r = {tuple(k) if isinstance(k, list) else k: v for k, v in json.loads(reward)}
            new_r = compute(p, new)
            diff = {k: v - new_r.get(k, 0) for k, v in old_r.items() if v - new_r.get(k, 0) > 0}
            deduct(conn, other, diff)
            conn.execute('UPDATE reward_log SET params=?, reward=? WHERE room_id=? AND uid=?',
                         (json.dumps({**p, 'req': vars(new)}), json.dumps([[list(k) if isinstance(k, tuple) else k, v] for k, v in new_r.items()]), room_id, other))
            if not new.isWin and old.isWin:
                # 胜→负：战绩、军功（胜+20/负-10，差 30）一起改回；荣耀之路/赛季/宝珠按战绩实时计算会自动跟着变
                got = conn.execute('SELECT rank_score FROM local_rewards WHERE room_id=? AND uid=?', (room_id, other)).fetchone()
                fix = (got[0] if got else 0) + 10
                conn.execute('UPDATE local_rewards SET win=0, rank_score=-10 WHERE room_id=? AND uid=?', (room_id, other))
                conn.execute('UPDATE battle_records SET win=0 WHERE room_id=? AND uid=?', (room_id, other))
                try:
                    conn.execute('UPDATE accounts SET rank_score=MAX(0, rank_score-?) WHERE uid=?', (fix, other))
                except Exception:
                    pass
            if not new.isMvp and old.isMvp:
                conn.execute('UPDATE battle_records SET mvp=0 WHERE room_id=? AND uid=?', (room_id, other))
                try:   # 让出“每队一个 MVP”的名额
                    conn.execute('DELETE FROM room_mvps WHERE room_id=? AND uid=?', (room_id, other))
                except Exception:
                    pass
            when = time.strftime('%Y-%m-%d %H:%M')
            mail(conn, other, f'对局数据核对（第{room_id}局）',
                 f'对局：第 {room_id} 局，{when} 复核\n'
                 f'情况：你上报的本局数据与同局其他真人玩家的战报不一致（多数战报为准，击杀/助攻允许 ±{KILL_TOL} 的误差）。\n'
                 f'修正内容：{"；".join(notes)}\n'
                 f'各玩家上报：\n{self.report_table(reps)}\n'
                 f'处理：已按修正后的数据重新计算本局奖励，多发部分已扣回：{self.fmt_items(diff)}'
                 + ('；本局改记为失败，军功按失败重算' if (not new.isWin and old.isWin) else '') + '。\n'
                 '查看：大厅右上角“违规举报”按钮（争议对局查看）。如有疑问请联系房主。')
            self.log(room=room_id, uid=other, stage='结算后补扣', notes=notes, deducted={str(k): v for k, v in diff.items()})
