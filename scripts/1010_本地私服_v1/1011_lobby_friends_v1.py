"""大厅业务处理器；不读取服务器全局对象，不管理 TCP 连接。"""
import json
import sqlite3
import time
import re
from pathlib import Path


def handle_friend_gift(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        req = message.request.storeBuyRequest
        result = services.economy.buy(services.state(account)['uid'], req.id, req.num, req.itemType,
                             next_operation('buy'))
        update = answer.response.storeBuyResponse.commonUpdate
        for reward in result['rewards']:
            if reward['added']:
                update.addItemList.add(ItemType=reward['item_type'], ItemId=reward['item_id'], ItemNum=reward['added'])
            if reward['item_type'] not in (5, 6):
                update.itemList.add(ItemType=reward['item_type'], ItemId=reward['item_id'], ItemNum=reward['count'])
        for key, kind in [('silver', 1), ('gold', 2), ('diamonds', 10), ('rank_score', 14)]:
            update.itemList.add(ItemType=kind, ItemId=0, ItemNum=result['balance'][key])
        arena = answer.response.storeBuyResponse.commonArenaUser
        arena.RankId = arena.MaxRankId = services.LOCAL_RANK_ID
        arena.RankScore = result['balance']['rank_score']
        arena.MaxRankScore = max(arena.RankScore, services.LOCAL_RANK_SCORE)
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_friend_info(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    for item in services.economy.counts(services.state(account)['uid']):
        answer.response.storeBuyInfoResponse.list.add(**item)
    answer.response.storeBuyInfoResponse.SetInParent()
    answer.response.errcode = ''
    return answer, jobs


def handle_friend_actions(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    answer.response.errcode = ''
    me = services.state(account)
    if message.type == 3055:
        req = message.request.chatSendRequest
        text = (req.text or '')[:200]
        prof = services.profile(uid)
        info = services.pb.ChatInfo(infoType=0, text=text, uid=uid, name=me['name'], level=services.LOCAL_LEVEL,
                           headImage=prof['head_image'], headBoxId=prof['head_box'], chatBoxId=prof['chat_box'],
                           rankId=services.LOCAL_RANK_ID, maxRankId=services.season_info(uid)['rank_id'], dateline=int(time.time()))
        if req.chatType == 2:      # 队伍：发给同队成员
            targets = services.ports.team_members(uid)
        elif req.chatType == 1:    # 私聊
            targets = {uid, req.privateUid}
        else:                      # 世界：所有在线
            targets = services.ports.online_uids()
        if req.chatType == 0:
            with services.connect_db() as bc3:
                services.ports.friends_mod.bump(bc3, uid, 'chat_world')
        with services.CHAT_LOCK:
            services.CHAT_LOG.append((req.chatType, uid, req.privateUid if req.chatType == 1 else '', info.SerializeToString()))
            del services.CHAT_LOG[:-200]
        push = services.pb.Message(type=3057); push.response.errcode = ''
        push.response.chatAsynResponse.chatType = req.chatType
        push.response.chatAsynResponse.chatInfo.CopyFrom(info)
        for uid in targets:
            services.ports.push(uid, push)
        answer.response.chatSendResponse.commonUpdate.SetInParent()
    elif message.type == 3056:
        req = message.request.chatListRequest
        d = answer.response.chatListResponse; d.SetInParent()
        with services.CHAT_LOCK:
            for ctype, frm, to, raw in services.CHAT_LOG:
                if ctype != req.chatType:
                    continue
                if ctype == 1 and {frm, to} != {uid, req.privateUid}:
                    continue
                d.chatInfoList.add().ParseFromString(raw)
    elif message.type == 3058:
        d = answer.response.chatPrivateRecentListResponse; d.SetInParent()
        with services.CHAT_LOCK:
            peers = {to if frm == uid else frm for c, frm, to, _ in services.CHAT_LOG if c == 1 and uid in (frm, to)}
        for acc in services.all_real_accounts():
            if acc['uid'] in peers:
                services.fill_friend(d.list.add(), acc)
    elif message.type == 3027:
        kw = message.request.friendAddSearchRequest.keyword.strip().lower()
        d = answer.response.friendAddSearchResponse; d.SetInParent()
        for acc in services.all_real_accounts():
            if acc['uid'] != uid and kw and (kw in acc['name'].lower() or kw in acc['account'].lower() or kw == acc['uid'].lower()):
                services.fill_friend(d.list.add(), acc)
    else:
        target = message.request.friendSendCoinRequest.uid
        if services.economy and target and target != uid:
            with services.economy.connection() as gc:
                services.economy._grant(gc, target, 13, 35, services.FRIEND_COIN_GIFT)
        answer.response.friendSendCoinResponse.commonUpdate.SetInParent()
    return answer, jobs


def handle_friend_tasks(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    fr = services.ports.friends
    def fill_bh(msg, bh):
        msg.heroId = bh['heroId']; msg.skinId = bh['skinId']; msg.proficiency = bh['proficiency']
        msg.starLevel = bh['starLevel']; msg.awakeLevel = bh['awakeLevel']
        msg.talentIdList.extend(bh['talentIdList'])
        if bh.get('borrowUid'):
            msg.borrowUid = bh['borrowUid']
    def fill_fh(msg, fh):
        msg.uid = fh['uid']; msg.name = fh['name']; msg.borrowCount = fh['borrowCount']
        msg.borrowState = fh.get('state', 2)
        if fh['hero']:
            fill_bh(msg.battleHero, fh['hero'])
    def fill_page(d, pg):
        d.SetInParent()
        for fh in pg['friends']:
            fill_fh(d.commonFriendHeroList.add(), fh)
        for fuid, bh, used in pg['mine']:
            b = d.friendHeroBorrowList.add(isUse=used); fill_bh(b.battleHero, bh)
            e = d.friendHeroBorrowMap.add(key=fuid); e.value.isUse = used; fill_bh(e.value.battleHero, bh)
        for lg in pg['logs']:
            d.friendHeroBorrowLogList.add(**lg)
    try:
        t = message.type
        if t == 3042:
            d = answer.response.recentContactListResponse; d.SetInParent()
            for acc in services.all_real_accounts():
                if acc['uid'] != uid:
                    services.fill_friend(d.list.add(), acc)
        elif t == 3405:
            fill_page(answer.response.friendHeroPageInfoResponse, fr.page(uid))
        elif t == 3406:
            fh = fr.set_hero(uid, message.request.friendHeroSetRequest.heroId)
            fill_fh(answer.response.friendHeroSetResponse.commonFriendHero, fh)
        elif t == 3407:
            pg = fr.borrow(uid, message.request.friendHeroBorrowRequest.friendUid)
            d = answer.response.friendHeroBorrowResponse; d.SetInParent()
            for fuid, bh, used in pg['mine']:
                b = d.friendHeroBorrowList.add(isUse=used); fill_bh(b.battleHero, bh)
                e = d.friendHeroBorrowMap.add(key=fuid); e.value.isUse = used; fill_bh(e.value.battleHero, bh)
        elif t == 3408:
            fill_fh(answer.response.friendHeroInfoResponse.commonFriendHero, fr.page(uid)['me'])
        elif t == 3409:
            fr.apply(uid, message.request.friendPartnerApplyRequest.partnerUid)
        elif t == 3410:
            r = message.request.friendPartnerReplyRequest
            fr.reply(uid, r.partnerUid, r.isAgree)
        elif t == 3411:
            d = answer.response.friendPartnerApplyListResponse; d.SetInParent()
            accs = {a['uid']: a for a in services.all_real_accounts()}
            for u in fr.apply_list(uid):
                if u in accs:
                    services.fill_friend(d.list.add(), accs[u])
        elif t == 3412:
            fr.delete(uid)
        elif t == 3413:
            tasks, partner = fr.task_list(uid)
            d = answer.response.friendTaskListResponse; d.SetInParent()
            for tid, num, got in tasks:
                d.list.add(id=tid, num=num, rewardNum=got)
            accs = {a['uid']: a for a in services.all_real_accounts()}
            if partner in accs:
                services.fill_friend(d.partner, accs[partner])
        else:
            got = fr.task_reward(uid, message.request.friendTaskRewardRequest.id)
            u = answer.response.friendTaskRewardResponse.commonUpdate
            u.addItemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['added'])
            u.itemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['count'])
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs
