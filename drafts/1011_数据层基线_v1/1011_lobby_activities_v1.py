"""大厅业务处理器；不读取服务器全局对象，不管理 TCP 连接。"""
import json
import sqlite3
import time
import re
from pathlib import Path


def handle_feature_actions(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        f2 = services.bridge.features2
        def fill2(update, res):
            update.SetInParent()
            for it in res.get('added', []):
                update.addItemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['added'])
                update.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
            for it in res.get('items', []):
                update.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
        if message.type == 3388:
            d = answer.response.chipFuseInfoResponse; d.SetInParent()
            for fid, val in f2.fuse_info(uid):
                d.list.add(id=fid, value=val)
        elif message.type == 3386:
            req = message.request.chipFuseConvertRequest
            res = f2.fuse(uid, req.id, req.times)
            d = answer.response.chipFuseConvertResponse
            fill2(d.commonUpdate, res); d.specialDrop.id, d.specialDrop.value = res['special']
        elif message.type == 3387:
            answer.response.chipFuseNoticeLogResponse.SetInParent()
        elif message.type == 3374:
            req = message.request.easyConvertRequest
            fill2(answer.response.easyConvertResponse.commonUpdate, f2.easy_convert(uid, req.id, req.num))
        elif message.type == 3268:
            res = f2.crystal_draw(uid, message.request.crystalDrawRequest.isMulti)
            d = answer.response.crystalDrawResponse
            fill2(d.commonUpdate, res); d.crystalDrawFreeUnix = res['free_unix']
        elif message.type == 3317:
            # 战斗宝箱：先给出空槽位和计数，避免客户端空引用；开箱逻辑待实现
            info = f2.box_info(uid)
            d = answer.response.battleBoxInfoResponse; d.SetInParent()
            for ct, n in info['times']:
                d.battleTimesInfoList.add(battleTimes=n, conditionType=ct)
            for bid, bt in info['boxes']:
                d.commonBoxInfo.add(id=bid, boxTypeId=bt)
            for slot, bt, unix in info['slots']:
                d.commonBoxSlotInfo.add(slotId=slot, boxTypeId=bt, getUnix=unix, isOpen=True)
        elif message.type == 3318:
            pass   # 槽位本地默认全部开放
        elif message.type == 3319:
            f2.box_into_slot(uid, message.request.battleBoxIntoSlotRequest.id)
        elif message.type == 3375:
            key = message.request.eventDataInfoRequest.featureTimeKey
            d = answer.response.eventDataInfoResponse; d.SetInParent(); d.featureTimeKey = key
            try:
                ev = services.bridge.events.info(uid, key)
            except ValueError:
                ev = {'tasks': [], 'items': []}
            d.DrawNum = 0   # 本地规则：取消活动每日抽奖次数限制（客户端按已抽次数显示 x/50）
            for tid, con, took in ev['tasks']:
                d.list.add(id=tid, conNum=con, takeNum=took)
            for k, i, n in ev['items']:
                d.commonItemList.add(ItemType=k, ItemId=i, ItemNum=n)
        elif message.type == 3376:
            r = message.request.eventDrawRequest
            res = services.bridge.events.draw(uid, r.featureTimeKey, r.multi)
            d = answer.response.eventDrawResponse
            fill2(d.commonUpdate, res); d.drawId = res['draw_id']
        elif message.type == 3378:
            answer.response.EventNoticeLogResponse.SetInParent()   # 活动全服公告记录：本地无，返回空
        elif message.type == 3377:
            r = message.request.eventTaskTakeRequest
            fill2(answer.response.EventTaskTakeResponse.commonUpdate, services.bridge.events.take(uid, r.featureTimeKey, r.taskId))
        elif message.type == 3421:
            g = services.bridge.features3.glory_info(uid)
            d = answer.response.gloryRoadInfoResponse; d.SetInParent()
            d.curRankScore = d.maxRankScore = g['score']
            d.rewardIdList.extend(g['claimed']); d.openIdList.extend(g['opened'])
        elif message.type == 3422:
            fill2(answer.response.gloryRoadRewardResponse.commonUpdate, services.bridge.features3.glory_reward(uid, message.request.gloryRoadRewardRequest.id))
        elif message.type == 3059:
            sg = services.bridge.features3.sign_list(uid)
            d = answer.response.signListResponse; d.SetInParent(); d.allDays = sg['all']
            for day, k, i, n, st in sg['days']:
                d.signRewardList.add(day=day, item=services.pb.CommonItem(ItemType=k, ItemId=i, ItemNum=n), state=st)
            for sid, days, gift, st in sg['total']:
                d.signTotalRewardList.add(id=sid, totalDays=days, giftId=gift, state=st)
        elif message.type == 3060:
            fill2(answer.response.signRewardResponse.commonUpdate, services.bridge.features3.sign_reward(uid))
        elif message.type == 3277:
            fill2(answer.response.signTotalRewardResponse.commonUpdate, services.bridge.features3.sign_total_reward(uid, message.request.signTotalRewardRequest.id))
        elif message.type == 3320:
            fill2(answer.response.battleBoxAwardResponse.commonUpdate, f2.box_award(uid, message.request.battleBoxAwardRequest.slotId))
        elif message.type == 3321:
            f2.box_delete(uid, message.request.battleBoxDeleteRequest.id)
        elif message.type == 3396:
            info = f2.astro_info(uid)
            d = answer.response.astrologyInfoResponse; d.SetInParent()
            d.commonAstrologyInfo.Processing = False
            d.commonAstrologyInfo.ClientInfo = info['client_info']
            d.NextResetTimeUnix = info['next']; d.FreeTimes = info['free']
            d.AstrologyIdList.extend(info['ids']); d.commonUpdate.SetInParent()
            d.GiftId = f2.ASTRO_DROP
            for did, k, i, n in f2.astro_cells():
                d.commonAstrologyInfo.ItemList.add(DropId=did, Item=services.pb.CommonItem(ItemType=k, ItemId=i, ItemNum=n))
        elif message.type == 3397:
            res = f2.astro_award(uid)
            d = answer.response.astrologyAwardResponse
            fill2(d.commonUpdate, res); d.commonAstrologyInfo.Processing = False
            d.commonAstrologyInfo.ClientInfo = f2.astro_info(uid)['client_info']
            for did, k, i, n in f2.astro_cells():
                d.commonAstrologyInfo.ItemList.add(DropId=did, Item=services.pb.CommonItem(ItemType=k, ItemId=i, ItemNum=n))
            info = f2.astro_info(uid); d.NextResetTimeUnix = info['next']; d.FreeTimes = info['free']
        elif message.type == 3398:
            answer.response.astrologySetInfoResponse.ClientInfo = f2.astro_set(uid, message.request.astrologySetInfoRequest.ClientInfo)
        elif message.type == 3399:
            fill2(answer.response.astrologyExchangeResponse.commonUpdate, f2.astro_exchange(uid, message.request.astrologyExchangeRequest.AstrologyId))
        elif message.type == 3367:
            answer.response.onePayInfoResponse.num = f2.onepay_info(uid)
        else:
            res = f2.onepay_buy(uid, message.request.onePayBuyRequest.index)
            d = answer.response.onePayBuyResponse
            fill2(d.commonUpdate, res); d.num = 0
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_passport(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        pp = services.bridge.passport
        def upd_items(update, items):
            for it in items:
                update.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
        if message.type == 3345:
            info = pp.info(uid)
            d = answer.response.passportInfoResponse
            for k, v in info.items():
                setattr(d, k, v)
        elif message.type == 3346:
            r = pp.buy_level(uid, message.request.passportBuyLevelRequest.num)
            d = answer.response.passportBuyLevelResponse
            d.commonUpdate.SetInParent(); upd_items(d.commonUpdate, r['items']); d.newLevel = r['lv']
        elif message.type == 3347:
            r = pp.promote(uid)
            d = answer.response.passportPromotionResponse
            d.commonUpdate.SetInParent(); upd_items(d.commonUpdate, r['items']); d.newLevel = r['lv']; d.newGrade = r['grade']
        elif message.type == 3349:
            r = pp.take_all(uid)
            d = answer.response.passportLvAwardQuickGetResponse
            d.commonUpdate.SetInParent()
            for it in r['added']:
                d.commonUpdate.addItemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['added'])
            upd_items(d.commonUpdate, r['added'])
            d.awardNormal, d.awardGood, d.awardPerfect = r['normal'], r['good'], r['perfect']
        elif message.type == 3350:
            d = answer.response.passportShopInfoResponse
            d.SetInParent()
            for sid, n in pp.shop_info(uid):
                d.list.add(id=sid, buyNum=n)
        elif message.type == 3351:
            req = message.request.passportShopBuyRequest
            r = pp.shop_buy(uid, req.id, req.num)
            d = answer.response.passportShopBuyResponse
            d.commonUpdate.SetInParent(); upd_items(d.commonUpdate, r['items'])
            for it in r['added']:
                d.commonUpdate.addItemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['added'])
            upd_items(d.commonUpdate, r['added'])
        else:
            d = answer.response.passportTaskListResponse; d.SetInParent()
            tasks, wexp = services.bridge.passport.task_list(uid)
            for tid, fin, prog in tasks:
                d.list.add(id=tid, finishNum=fin, progress=prog)
            d.weekAddExp = wexp
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_activity_conditions(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    ex = services.bridge.exchange
    d = answer.response.limitActionListResponse if message.type == 3089 else answer.response.actionConditionListResponse
    d.SetInParent(); d.commonLinkage.SetInParent()
    if message.type == 3089:
        for aid, st, en, name, desc in ex.actions():
            d.limitActionList.add(actId=aid, startUnix=st, endUnix=en, sort=1, conditionId=aid, name=name, desc=desc, tag=0, redPoint=False)
        d.selectedId = 1
    for sub, cost, gain, tips, took in ex.conditions(uid):
        c = d.actionConditionList.add(actId=1, conditionId=1, subId=sub, conditionType=0, conNum=1, conNum2=0,
                                      costType=cost[0], costId=cost[1], costNum=cost[2],
                                      takeLimit=50, curConNum=1, curTakeNum=took, tips=tips)
        for k, i, n in gain:
            c.list.add(ItemType=k, ItemId=i, ItemNum=n)
    answer.response.errcode = ''
    return answer, jobs


def handle_activity_reward(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        res = services.bridge.exchange.take(uid, message.request.actionConditionAwardRequest.subId)
        u = answer.response.actionConditionAwardResponse.commonUpdate; u.SetInParent()
        for it in res['added']:
            u.addItemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['added'])
            u.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
        for it in res['items']:
            u.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_season_orbs(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    import datetime
    with services.connect_db() as conn:
        try:
            used = conn.execute("SELECT COUNT(*) FROM local_rewards WHERE uid=? AND day=? AND mode=12 AND silver>0",
                                (uid, datetime.date.today().isoformat())).fetchone()[0]
        except sqlite3.OperationalError:
            used = 0
    if message.type == 3243:
        answer.response.pVEAwardInfoResponse.takeNum = used
    else:
        answer.response.receivePVEAwardResponse.takeNum = used
        answer.response.receivePVEAwardResponse.commonUpdate.SetInParent()
    answer.response.errcode = ''
    return answer, jobs


def handle_season_info(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    data = answer.response.heroBattleInfoResponse
    data.todayChallengeNum, data.todayChallengeNumLimit = 0, 99
    data.challengeNum = 99
    answer.response.errcode = ''
    return answer, jobs


def handle_season_reward(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    if message.type == 3361:
        req = message.request.battleBubbleEquipRequest
        row = next((r for r in services.TABLES['FightShowToolBaseVo'] if r['BubbleId'] == req.bubbleId), None)
        if row is None:
            answer.response.errcode = 'LOCAL_BUBBLE_UNKNOWN'
        else:
            with services.connect_db() as conn:
                for hero_id in (list(req.heroIdList) or [0]):
                    conn.execute('INSERT OR REPLACE INTO bubble_equip VALUES(?,?,?,?)', (uid, row['Type'], hero_id, req.bubbleId))
            answer.response.battleBubbleEquipResponse.commonBattleBubbleList.extend(services.bubble_list(uid))
            answer.response.errcode = ''
    else:
        answer.response.battleBubbleListResponse.list.extend(services.bubble_list(uid))
        answer.response.errcode = ''
    return answer, jobs


def handle_read_only_lists(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    fields = {106: 'armyListResponse', 3079: 'armyUserOnlineListResponse', 3021: 'friendListResponse', 3056: 'chatListResponse',
              3058: 'chatPrivateRecentListResponse', 3072: 'itemNumListResponse',
              3229: 'clientJsonGetHeroBattleResponse', 3259: 'clientJsonGetLuckRechargeResponse',
              3266: 'mysteryStoreInfoResponse', 3364: 'battleBubbleListResponse',
              3389: 'hourlyActivityInfoResponse', 2012: 'competitionV2StatusResponse',
              3393: 'competitionV2AwardStatusResponse', 3325: 'battleTypeInfoResponse',
              3042: 'recentContactListResponse', 3066: None, 3230: None, 3379: None}
    if fields[message.type]:
        getattr(answer.response, fields[message.type]).SetInParent()
    if message.type == 3066:
        # 记录客户端错误上报内容（截断），便于定位界面异常。
        err = message.request.clientErrorRequest
        with open(services.ROOT / 'memory/1010_客户端错误上报_v1.log', 'a', encoding='utf-8') as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {account} type={err.errorType} file={err.fileName}\n{err.code[:2000]}\n---\n")
    if message.type == 3021:
        # 局域网：所有真人账号都是好友，在线的排前面。
        with services.connect_db() as fc:
            others = [dict(r) for r in fc.execute("SELECT uid,name FROM accounts WHERE account<>? AND uid LIKE 'local-%' AND uid NOT LIKE 'local-bot%'", (account,))]
        others.sort(key=lambda f: f['uid'] not in services.bridge.game_peers)
        for friend in others:
            answer.response.friendListResponse.list.add(uid=friend['uid'],name=friend['name'],
                level=services.LOCAL_LEVEL,headImage=services.profile(friend['uid'])['head_image'],headBoxId=services.profile(friend['uid'])['head_box'],rankId=services.LOCAL_RANK_ID,
                maxRankId=services.season_info(friend['uid'])['rank_id'],onlineStatus=1 if friend['uid'] in services.bridge.game_peers else 0,
                loginUnix=int(time.time()) if friend['uid'] in services.bridge.game_peers else int(time.time()) - 3600)
    if message.type == 3072:
        item_type = message.request.itemNumListRequest.ItemType
        for item in services.economy.items(services.state(account)['uid'], item_type):
            answer.response.itemNumListResponse.itemList.add(ItemType=item_type, ItemId=item['item_id'], ItemNum=item['count'])
    answer.response.errcode = ''
    return answer, jobs


def handle_official_awards(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        rows = sorted(services.TABLES['OfficialBaseVo'], key=lambda r: r['Id'])
        with services.connect_db() as oc:
            oc.execute('CREATE TABLE IF NOT EXISTS officials(uid TEXT PRIMARY KEY, oid INTEGER, spent INTEGER DEFAULT 0, awarded INTEGER DEFAULT 0)')
            top = rows[-1]['Id'] if not services.is_growth(uid) else rows[0]['Id']
            oc.execute('INSERT OR IGNORE INTO officials(uid,oid,awarded) VALUES(?,?,?)', (uid, top, top))
            o = dict(oc.execute('SELECT * FROM officials WHERE uid=?', (uid,)).fetchone())
            try:
                games = oc.execute('SELECT COUNT(*) FROM local_rewards WHERE uid=?', (uid,)).fetchone()[0]
            except sqlite3.OperationalError:
                games = 0
            prestige = max(0, games * 25 - o['spent'])
            cur = next(r for r in rows if r['Id'] == o['oid'])
            if message.type == 3270:
                if o['oid'] >= rows[-1]['Id']:
                    raise ValueError('LOCAL_TASK_NOT_DONE')
                need = cur['NeedPrestige']
                u = answer.response.upOfficialResponse.commonUpdate; u.SetInParent()
                if prestige < need:
                    if not message.request.upOfficialRequest.isUseDiam:
                        raise ValueError('LOCAL_TASK_NOT_DONE')
                    cost = -(-(need - prestige) // 10)   # 与客户端一致：差额威望 /10 向上取整 钻石
                    if oc.execute('SELECT diamonds FROM accounts WHERE uid=?', (uid,)).fetchone()[0] < cost:
                        raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
                    oc.execute('UPDATE accounts SET diamonds=diamonds-? WHERE uid=?', (cost, uid))
                    u.itemList.add(ItemType=10, ItemId=0, ItemNum=oc.execute('SELECT diamonds FROM accounts WHERE uid=?', (uid,)).fetchone()[0])
                    spend = prestige
                else:
                    spend = need
                oc.execute('UPDATE officials SET oid=oid+1, spent=spent+? WHERE uid=?', (spend, uid))
                o['oid'] += 1; prestige -= spend
                answer.response.upOfficialResponse.officialId = o['oid']
            elif message.type == 3271:
                wk = int(time.strftime('%Y%W'))
                if o['awarded'] >= wk:
                    raise ValueError('LOCAL_ALREADY_CLAIMED')
                gift = next(r for r in rows if r['Id'] == o['oid'])['GiftId']
                u = answer.response.officialAwardResponse.commonUpdate; u.SetInParent()
                for k, i, n in services.bridge.features2.roll_gift_groups(gift):
                    got = services.economy._grant(oc, uid, k, i, n) if k not in (1, 2, 10) else services.bridge.features._give(oc, uid, k, i, n)
                    u.addItemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['added'])
                    u.itemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['count'])
                oc.execute('UPDATE officials SET awarded=? WHERE uid=?', (wk, uid))
            d = answer.response.officialInfoResponse if message.type == 3269 else None
            if d is not None:
                d.officialId = o['oid']; d.itemOffercial = prestige; d.dbOffercial = prestige
                d.isCanAward = 1 if o['awarded'] < int(time.strftime('%Y%W')) else 0   # 每周一次俸禄
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs
