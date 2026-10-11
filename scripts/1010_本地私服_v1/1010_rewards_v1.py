"""本地对局奖励规则（用户指定，2026-10-10）。所有数值集中在这里，直接改即可。

每局奖励 = 满额 × 表现系数 × 输赢系数 × 英雄价格系数 × 成长系数
- 表现系数 = 0.4 + 0.6 × 表现分（0~1），MVP 直接 1.0。治疗/支援不计分。
- 输赢系数：赢 1.0，输 0.5。PVE 以 isWin（通关）为准。
- 英雄价格系数（不论输赢）：银币英雄 1.5 > 金币英雄 1.25 > 其他（钻石/免费）1.0。
- 成长系数（不论输赢）：星级、阶级（觉醒）、天赋等级越低越高，1.5（全 0）→ 1.0（全满），线性。
满额按“赢 70 局且满表现（系数=1）”拿满推算，所以价格/成长系数大于 1 时会更快。
"""

# —— 模式分组 ——
GROUP_OF_MODE = {
    11: 'ranked', 13: 'match',
    1: 'pvp', 2: 'pvp', 3: 'pvp', 4: 'pvp', 5: 'pvp', 6: 'pvp', 7: 'pvp', 9: 'pvp', 22: 'pvp',
    12: 'pve_crystal', 18: 'pve_crystal',
    10: 'pve_chip', 17: 'pve_chip',
    23: 'pve_gene',
    20: 'pve_jade', 24: 'pve_jade', 14: 'pve_jade',
    15: 'all_out',
}
# 表现分类型：按实际玩法（匹配/排位随机到的玩法）判断
SCORE_TYPE = {1: 'team', 2: 'team', 3: 'team', 7: 'team', 4: 'obj', 5: 'obj', 6: 'obj', 9: 'obj', 22: 'obj'}
SCORE_WEIGHTS = {
    'team': {'kill': 0.40, 'assist': 0.25, 'shuchu': 0.20, 'shengcun': 0.15},
    'obj':  {'tuijin': 0.35, 'kill': 0.25, 'assist': 0.20, 'shengcun': 0.20},
    'pve':  {'objective': 0.50, 'shuchu': 0.30, 'shengcun': 0.20},
}
KILL_FULL = 10      # 击杀/助攻达到该值算满分
RADAR_FULL = 100    # 输出/推进/生存评分满分

S, G, D = 'silver', 'gold', 'diamonds'
CRYSTAL, NANO, BLUEPRINT, CARRIER, WAFER, GENE, JADE = (22, 0), (13, 19), (41, 0), (13, 205), (13, 206), (13, 220), (43, 0)
ENERGY, FRIEND, PASSCOIN = (13, 211), (13, 35), (13, 38)

# 每局满额（赢 + 表现系数 1.0 时）
FULL = {
    # 货币只靠排位：70 局 = 银币143.5万 / 金币35万 / 钻石8.4万（买齐全部英雄）
    'ranked': {S: 20500, G: 5000, D: 1200, CRYSTAL: 50, NANO: 50, BLUEPRINT: 50, CARRIER: 50, WAFER: 50, GENE: 50, JADE: 50},
    'match':  {S: 2000, G: 500, CRYSTAL: 50, NANO: 50, BLUEPRINT: 50, CARRIER: 50, WAFER: 50, GENE: 50, JADE: 50},
    'pvp':    {S: 2000, G: 500, CRYSTAL: 100, NANO: 100, BLUEPRINT: 100, CARRIER: 100, WAFER: 100, GENE: 100, JADE: 100},
    # 升级材料按组：每组 70 局拿满 137 名英雄所需；升级银币 636 万分摊到 4 组（每局 23000）
    'pve_crystal': {S: 23000, CRYSTAL: 2500, NANO: 2500},
    'pve_chip':    {S: 23000, BLUEPRINT: 2500, CARRIER: 2500, WAFER: 360},
    'pve_gene':    {S: 23000, GENE: 11800, ENERGY: 300},
    'pve_jade':    {S: 23000, JADE: 11800},
    # 全军突击：一局 = 4 组 PVE 各一局，70 局即可拿满全部材料
    'all_out': {S: 92000, CRYSTAL: 2500, NANO: 2500, BLUEPRINT: 2500, CARRIER: 2500, WAFER: 360, GENE: 11800, JADE: 11800, ENERGY: 300},
}
GEM = (13, 12)  # 宝石：专属抽英雄消耗
EXTRA_ALL = {FRIEND: 50, PASSCOIN: 50, GEM: 10}   # 每局都给的少量友情点/通行币（同样乘系数）

WIN_FACTOR, LOSE_FACTOR = 1.0, 0.5
PRICE_FACTOR = {'silver': 1.5, 'gold': 1.25, 'other': 1.0}
GROWTH_MIN, GROWTH_MAX = 1.0, 1.5

# —— 开放时间：只保留克隆大战（周末 12-14 点）与能源基地（每天 20-24 点）；其他模式全天、无次数限制 ——
OPEN_WINDOWS = {14: ('weekend', 12, 14), 23: ('everyday', 20, 24)}


def mode_open(mode, now=None):
    import time
    if mode not in OPEN_WINDOWS:
        return True
    t = time.localtime(now)
    days, start, end = OPEN_WINDOWS[mode]
    if days == 'weekend' and t.tm_wday < 5:
        return False
    return start <= t.tm_hour < end


def performance(play_mode, group, req):
    if getattr(req, 'isMvp', False):
        return 1.0, 1.0
    kind = 'pve' if group.startswith('pve') or group == 'all_out' else SCORE_TYPE.get(play_mode, 'team')
    parts = {
        'kill': min(1.0, req.killNum / KILL_FULL),
        'assist': min(1.0, req.assistNum / KILL_FULL),
        'shuchu': min(1.0, req.shuchu / RADAR_FULL),
        'tuijin': min(1.0, req.tuijin / RADAR_FULL),
        'shengcun': min(1.0, req.shengcun / RADAR_FULL),
        'objective': 1.0 if req.isWin else min(1.0, max(0, req.round) / 10),
    }
    score = sum(w * parts[k] for k, w in SCORE_WEIGHTS[kind].items())
    return score, 0.4 + 0.6 * score


def price_factor(hero_row):
    if hero_row and hero_row.get('Silver', 0) > 0:
        return PRICE_FACTOR['silver']
    if hero_row and hero_row.get('Gold', 0) > 0:
        return PRICE_FACTOR['gold']
    return PRICE_FACTOR['other']


def growth_factor(star, awake, talent_ratio):
    ratio = (min(star, 50) / 50 + min(awake, 50) / 50 + talent_ratio) / 3
    return GROWTH_MAX - (GROWTH_MAX - GROWTH_MIN) * ratio


def compute(room_mode, play_mode, req, hero_row, star, awake, talent_ratio):
    group = GROUP_OF_MODE.get(room_mode, 'pvp')
    score, perf = performance(play_mode, group, req)
    factor = perf * (WIN_FACTOR if req.isWin else LOSE_FACTOR) * price_factor(hero_row) * growth_factor(star, awake, talent_ratio)
    out = {}
    for key, num in list(FULL[group].items()) + list(EXTRA_ALL.items()):
        out[key] = int(round(num * factor))
    return {'group': group, 'score': round(score, 3), 'factor': round(factor, 3), 'reward': out}
