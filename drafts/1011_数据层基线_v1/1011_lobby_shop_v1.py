"""大厅业务处理器；不读取服务器全局对象，不管理 TCP 连接。"""
import json
import sqlite3
import time
import re
from pathlib import Path


def handle_special_shop(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        ft = services.bridge.features
        def fill(update, res):
            update.SetInParent()
            for it in res.get('added', []):
                update.addItemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['added'])
                update.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
            for it in res.get('items', []):
                update.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
        if message.type == 3244:
            info = ft.lottery_info(uid)
            d = answer.response.heroLotteryInfoResponse
            d.SetInParent()
            for lid, k, i, n, lt in info['list']:
                d.heroLotteryList.add(id=lid, item=services.pb.CommonItem(ItemType=k, ItemId=i, ItemNum=n), lotteryType=lt)
            d.value, d.DrawNum = info['value'], info['draws']
        elif message.type == 3245:
            req = message.request.heroLotteryRewardRequest
            res = ft.lottery_draw(uid, req.lotteryType, req.multi)
            d = answer.response.heroLotteryRewardResponse
            d.idList.extend(res['ids']); fill(d.commonUpdate, res); d.value = res['value']
        elif message.type == 3360:
            res = ft.exclusive_exchange(uid, message.request.heroExclusiveExchangeRequest.id)
            fill(answer.response.heroExclusiveExchangeResponse.commonUpdate, res)
        elif message.type == 3061:
            info = ft.daily_list(uid)
            d = answer.response.taskDailyListResponse
            d.SetInParent()
            for tid, num, done in info['tasks']:
                d.list.add(id=tid, num=num, rewardState=done)
            d.taskWeeklyAc.ac = info['week_ac']
            for bid, done in info['boxes']:
                d.taskWeeklyAc.list.add(id=bid, rewardState=done)
        elif message.type == 3062:
            res = ft.daily_reward(uid, message.request.taskDailyRewardRequest.id)
            d = answer.response.taskDailyRewardResponse
            d.todayAc = res['today_ac']; fill(d.commonUpdate, res)
        else:
            res = ft.weekly_box(uid, message.request.taskWeeklyAcRewardRequest.id)
            fill(answer.response.taskWeeklyAcRewardResponse.commonUpdate, res)
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_purchase(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        money = {1: 'silver', 2: 'gold', 10: 'diamonds'}
        with services.connect_db() as bc:
            if message.type == 3003:
                req = message.request.heroBuyRequest
                row = next((h for h in services.TABLES['HeroBaseVo'] if h['Id'] == req.heroId), None)
                pay = int(req.itemType) or 1
                price = row and {1: row['Silver'], 2: row['Gold'], 10: row['Diam']}.get(pay, 0)
                if row and row['IsBuy'] == 7:   # 通行证英雄：只能通行币在通行证商店换
                    raise ValueError('LOCAL_PASS_ONLY')
                if row and row['IsBuy'] in (3, 5, 6) and row['Diam']:   # 限定英雄统一定价，只能钻石买（与客户端补丁一致）
                    price = services.limited_hero_price(row) if pay == 10 else 0
                if not row or not price or pay not in money:
                    raise ValueError('LOCAL_PRICE_UNSUPPORTED')
                if bc.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, req.heroId)).fetchone():
                    raise ValueError('LOCAL_ALREADY_OWNED')
            else:
                req = message.request.skinBuyRequest
                row = next((k for k in services.TABLES['SkinIdBaseVo'] if k['Id'] == req.skinId), None)
                pay, price = 10, services.skin_price(row)
                if not bc.execute('SELECT 1 FROM heroes WHERE uid=? AND hero_id=?', (uid, row['HeroId'])).fetchone():
                    raise ValueError('LOCAL_NEED_HERO')
                bc.execute('CREATE TABLE IF NOT EXISTS owned_skins(uid TEXT NOT NULL, skin_id INTEGER NOT NULL, PRIMARY KEY(uid,skin_id))')
                if bc.execute('SELECT 1 FROM owned_skins WHERE uid=? AND skin_id=?', (uid, req.skinId)).fetchone():
                    raise ValueError('LOCAL_ALREADY_OWNED')
            col = money[pay]
            if bc.execute(f'SELECT {col} FROM accounts WHERE uid=?', (uid,)).fetchone()[0] < price:
                raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
            bc.execute(f'UPDATE accounts SET {col}={col}-? WHERE uid=?', (price, uid))
            if message.type == 3003:
                services.add_hero(bc, uid, req.heroId)
                if row['IsBuy'] != 0 and pay == 10:   # 限定英雄：有 5 折券就自动用，返还一半钻石
                    bc.execute('CREATE TABLE IF NOT EXISTS hero_coupons(uid TEXT PRIMARY KEY, num INTEGER)')
                    cp = bc.execute('SELECT num FROM hero_coupons WHERE uid=?', (uid,)).fetchone()
                    if cp and cp[0] > 0:
                        bc.execute('UPDATE hero_coupons SET num=num-1 WHERE uid=?', (uid,))
                        bc.execute('UPDATE accounts SET diamonds=diamonds+? WHERE uid=?', (price // 2, uid))
            else:
                bc.execute('INSERT OR IGNORE INTO owned_skins VALUES(?,?)', (uid, req.skinId))
            left = bc.execute(f'SELECT {col} FROM accounts WHERE uid=?', (uid,)).fetchone()[0]
        if message.type == 3003:
            d = answer.response.heroBuyResponse
            h = d.heroListUpdate.add(HeroId=req.heroId, IsBuy=True)
            sk = next((k['Id'] for k in services.TABLES['SkinIdBaseVo'] if k['HeroId'] == req.heroId and k['Type'] == 1), 0)
            h.SkinId = sk
            d.commonUpdate.addItemList.add(ItemType=5, ItemId=req.heroId, ItemNum=1)
        else:
            d = answer.response.skinBuyResponse
            d.commonUpdate.addItemList.add(ItemType=6, ItemId=req.skinId, ItemNum=1)
        d.commonUpdate.itemList.add(ItemType=pay, ItemId=0, ItemNum=left)
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs


def handle_item_shop(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    kind = 16 if message.type == 3365 else 37
    fid = (message.request.headBoxBuyRequest if kind == 16 else message.request.chatBoxBuyRequest).id
    if services.economy:
        with services.economy.connection() as bc:
            services.economy._grant(bc, uid, kind, fid, 1)
    resp = answer.response.headBoxBuyResponse if kind == 16 else answer.response.chatBoxBuyResponse
    resp.commonUpdate.itemList.add(ItemType=kind, ItemId=fid, ItemNum=1)
    resp.commonUpdate.addItemList.add(ItemType=kind, ItemId=fid, ItemNum=1)
    answer.response.errcode = ''
    return answer, jobs
