"""大厅业务处理器；不读取服务器全局对象，不管理 TCP 连接。"""
import json
import sqlite3
import time
import re
from pathlib import Path


def handle_equip_skin(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    skin_id = message.request.skinEquipRequest.skinId
    with services.connect_db() as conn:
        owned_skin = services.accounts.has_skin(conn, uid, skin_id)
    skin = next((row for row in services.TABLES['SkinIdBaseVo']
                 if row['Id'] == skin_id), None)
    if skin is None:
        answer.response.errcode = 'LOCAL_SKIN_UNAVAILABLE'
    else:
        with services.connect_db() as conn:
            changed = services.accounts.equip_skin(conn, uid, skin['HeroId'], skin_id)
        if not changed:
            answer.response.errcode = 'LOCAL_HERO_UNAVAILABLE'
        else:
            hero = next(x for x in services.login_response(account, message).response.loginResponse.commonHeroList
                        if x.HeroId == skin['HeroId'])
            answer.response.skinEquipResponse.heroListUpdate.add().CopyFrom(hero)
            answer.response.errcode = ''
    return answer, jobs


def handle_upgrade_star(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        quick = message.type == 3249
        req = message.request.heroUpgradeStarQuickRequest if quick else message.request.heroUpgradeStarRequest
        result = services.economy.upgrade_star(uid, req.heroId, req.isUseUniversal, quick,
            next_operation('star'), services.TABLES['HeroStarBaseVo'])
        with services.connect_db() as bc2:
            services.bridge.friends_mod.bump(bc2, uid, 'star_up')
        data = answer.response.heroUpgradeStarQuickResponse if quick else answer.response.heroUpgradeStarResponse
        hero = next(x for x in services.login_response(account, message).response.loginResponse.commonHeroList if x.HeroId == req.heroId)
        data.HeroListUpdate.add().CopyFrom(hero)
        data.commonUpdate.itemList.add(ItemType=1,ItemId=0,ItemNum=result['silver'])
        for item in result['items']:
            data.commonUpdate.itemList.add(ItemType=item['item_type'],ItemId=item['item_id'],ItemNum=item['count'])
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_hero_growth(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        uid = uid
        op = next_operation('growth')
        def items_to(update, items):
            for item in items:
                update.itemList.add(ItemType=item['item_type'], ItemId=item['item_id'], ItemNum=item['count'])
        def hero_message(hero_id):
            return next(x for x in services.login_response(account, message).response.loginResponse.commonHeroList if x.HeroId == hero_id)
        if message.type == 3400:
            req = message.request.heroAwakeLevelUpRequest
            result = services.economy.awake_level_up(uid, req.HeroId, req.IsUseUniversal, req.IsQuick, req.UseDiamNum, op, services.TABLES['ICCostBaseVo'])
            data = answer.response.heroAwakeLevelUpResponse
            data.commonHero.CopyFrom(hero_message(req.HeroId))
            data.commonUpdate.SetInParent()
            items_to(data.commonUpdate, result['items'])
        elif message.type == 3424:
            services.economy.reset_proficiency(uid, message.request.heroResetProficiencyRequest.HeroId)
            answer.response.heroResetProficiencyResponse.commonUpdate.SetInParent()
        elif message.type == 3428:
            levels, use = services.economy.talent_info(uid, message.request.heroTalentInfoRequest.heroId)
            info = answer.response.heroTalentInfoResponse.info
            info.SetInParent()
            for tid, level in sorted(levels.items()):
                info.tidMap.add(key=tid, value=level)
            info.useTidList.extend(use)
        elif message.type == 3425:
            req = message.request.heroTalentUseRequest
            services.economy.talent_use(uid, req.heroId, list(req.talentTidList), services.TABLES['TalentBaseVo'])
        elif message.type == 3426:
            req = message.request.heroTalentLvUpRequest
            result = services.economy.talent_lv_up(uid, req.heroId, req.tid, req.isUseCommonTalentChip, op, services.TABLES['TalentBaseVo'])
            answer.response.heroTalentLvUpResponse.commonUpdate.SetInParent()
            items_to(answer.response.heroTalentLvUpResponse.commonUpdate, result['items'])
        elif message.type == 3427:
            cost = next(r['stringValue'] for r in services.TABLES['VariableBaseVo'] if r['Name'] == 'Talent')
            result = services.economy.talent_clear(uid, message.request.heroTalentClearRequest.heroId, op, cost)
            answer.response.heroTalentClearResponse.commonUpdate.SetInParent()
            items_to(answer.response.heroTalentClearResponse.commonUpdate, result['items'])
        elif message.type == 3053:
            target = message.request.userInfoHeroRequest.uid or uid
            data = answer.response.userInfoHeroResponse
            data.SetInParent()
            with services.connect_db() as conn:
                rows = conn.execute('SELECT hero_id,proficiency,skin_id,star,awake FROM heroes WHERE uid=? ORDER BY hero_id', (target,)).fetchall()
            for index, (hero_id, prof, skin, star, awake) in enumerate(rows):
                data.list.add(heroId=hero_id, equipSkinId=skin, proficiency=prof, index=index + 1,
                              maxIndex=index + 1, starLevel=star, awakeLevel=awake)
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_convert_currency(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        req = message.request.friendShopBuyRequest
        result = services.economy.friend_shop_buy(uid, req.id, req.num, services.TABLES['FriendShopBaseVo'])
        upd = answer.response.friendShopBuyResponse.commonUpdate
        for item in result['items']:
            upd.itemList.add(ItemType=item['item_type'], ItemId=item['item_id'], ItemNum=item['count'])
        got = result['added']
        upd.addItemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['added'])
        upd.itemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['count'])
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_rank_rewards(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    with services.connect_db() as rc:
        rc.execute(services.BATTLE_RECORD_SQL)
        if message.type == 3051:
            req = message.request.userInfoBattleRequest
            target = req.uid or uid
            rows = [dict(r) for r in rc.execute('SELECT * FROM battle_records WHERE uid=? AND (?=0 OR battle_type=?) ORDER BY id DESC', (target, req.battleType, req.battleType))]
            data = answer.response.userInfoBattleResponse
            month = int(time.time()) - 30 * 86400
            recent = [r for r in rows if r['unix'] >= month]
            data.fightNum = len(rows); data.winNum = sum(r['win'] for r in rows)
            data.recentlyFightNum = len(recent); data.recentlyWinNum = sum(r['win'] for r in recent)
            data.mvpNum = sum(1 for r in rows if r['mvp'] and r['win']); data.loseMvpNum = sum(1 for r in rows if r['mvp'] and not r['win'])
            data.noDeadNum = sum(1 for r in rows if r['dead'] == 0)
            data.maxMultiKillNum = max([r['multi'] for r in rows] or [0])
            data.tripleKillNum = sum(r['triple'] for r in rows); data.quadraKillNum = sum(r['quadra'] for r in rows); data.pentaKillNum = sum(r['penta'] for r in rows)
            data.legendaryNum = sum(1 for r in rows if r['kill'] >= 8 and r['dead'] == 0)
            if recent:
                for key in ('shuchu', 'tuijin', 'shengcun', 'zhiyuan'):
                    setattr(data, key, int(sum(r[key] for r in recent) / len(recent)))
            for r in recent[:50]:
                data.list.add(heroId=r['hero'], equipSkinId=r['skin'], killNum=r['kill'], deadNum=r['dead'], assistNum=r['assist'],
                              battleUnix=r['unix'], id=r['id'], isWin=bool(r['win']), isMvp=bool(r['mvp']),
                              isTripleKill=bool(r['triple']), isQuadraKill=bool(r['quadra']), isPentaKill=bool(r['penta']))
        elif message.type == 3052:
            target = message.request.userInfoScoreRequest.uid or uid
            data = answer.response.userInfoScoreResponse
            for i, (bt, n, w) in enumerate(rc.execute('SELECT battle_type, COUNT(*), SUM(win) FROM battle_records WHERE uid=? GROUP BY battle_type', (target,))):
                data.list.add(battleType=bt, score=0, index=i + 1, fightNum=n, winNum=w or 0)
        else:
            row = rc.execute('SELECT record FROM battle_records WHERE id=?', (message.request.userInfoBattleRecordRequest.id,)).fetchone()
            data = answer.response.userInfoBattleRecordResponse
            data.SetInParent()
            if row and row[0]:
                data.record.ParseFromString(row[0])
    answer.response.errcode = ''
    return answer, jobs
