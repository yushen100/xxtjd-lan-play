"""大厅业务处理器；不读取服务器全局对象，不管理 TCP 连接。"""
import json
import sqlite3
import time
import re
from pathlib import Path

repos = None   # 数据访问层（1011_repos_v1），由大厅路由 1011_lobby_v1 加载时注入


def handle_mail(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    try:
        with services.connect_db() as mc:
            repos.ensure_schema(mc, services.MAIL_SQL)
            if message.type == 3043:
                d = answer.response.mailListResponse; d.SetInParent()
                for m in repos.mail_list(mc, uid):
                    cm = d.list.add(id=m['id'], title=m['title'], content=m['content'], cTime=m['ctime'], isRead=bool(m['is_read']))
                    setattr(cm, 'from', m['sender'])
                    if not m['claimed']:
                        for k, i, n in m['items']:
                            cm.Rewards.add(ItemType=k, ItemId=i, ItemNum=n)
            elif message.type == 3044:
                repos.mail_mark_read(mc, uid, message.request.mailReadRequest.mailId)
                answer.response.mailReadResponse.SetInParent() if 'mailReadResponse' in answer.response.DESCRIPTOR.fields_by_name else None
            elif message.type in (3045, 3046):
                ids = list(message.request.mailRewardRequest.mailIdList if message.type == 3045 else message.request.mailDelRequest.mailIdList)
                resp = answer.response.mailRewardResponse if message.type == 3045 else answer.response.mailDelResponse
                resp.commonUpdate.SetInParent()
                for mid in ids:
                    m = repos.mail_get(mc, uid, mid)
                    if not m:
                        continue
                    if not m['claimed'] and services.economy:
                        for k, i, n in m['items']:
                            if k == 6:
                                if repos.add_skin(mc, uid, i):
                                    resp.commonUpdate.addItemList.add(ItemType=6, ItemId=i, ItemNum=1)
                                    continue
                                k, i, n = 43, 0, 100   # 已有该皮肤：转万能玄玉
                            got = services.economy._grant(mc, uid, k, i, n)
                            resp.commonUpdate.addItemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['added'])
                            resp.commonUpdate.itemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['count'])
                        repos.mail_mark_claimed(mc, mid)
                    if message.type == 3046:
                        repos.mail_delete(mc, uid, mid)
            elif message.type == 3086:
                # 赠送皮肤：按直购价扣自己的钻石，皮肤通过邮件发给好友
                req = message.request.skinBuyForFriendRequest
                row = next((k for k in services.TABLES['SkinIdBaseVo'] if k['Id'] == req.skinId), None)
                price = services.skin_price(row)
                if repos.account_name(mc, req.friendUid) is None or req.friendUid == uid:
                    raise ValueError('LOCAL_FRIEND_UNKNOWN')
                if repos.balance(mc, uid, 10) < price:
                    raise ValueError('LOCAL_BALANCE_INSUFFICIENT')
                repos.add_balance(mc, uid, 10, -price)
                me = {'name': repos.account_name(mc, uid), 'diamonds': repos.balance(mc, uid, 10)}
                services.send_mail(mc, req.friendUid, me['name'], f'{me["name"]} 送你一款皮肤',
                          f'你的好友 {me["name"]} 送给你皮肤【{row["Name"]}】。\n'
                          f'留言：{(req.text or "送你一款皮肤！")[:200]}\n'
                          '点击“领取”即可获得；如果你已经拥有这款皮肤，会自动换成万能玄玉×100。', [(6, req.skinId, 1)])
                u = answer.response.skinBuyForFriendResponse.commonUpdate
                u.itemList.add(ItemType=10, ItemId=0, ItemNum=me['diamonds'])
            elif message.type == 3391:
                d = answer.response.seasonShopInfoResponse; d.SetInParent()
                for item_id, num in repos.season_buys(mc, uid):
                    d.list.add(id=item_id, buyNum=num)
            else:
                req = message.request.seasonShopBuyRequest
                row = next((r for r in services.TABLES['SeasonShopBaseVo'] if r['Id'] == req.id), None)
                season = services.season_bounds()[0]
                if not row or row['Term'] > season:
                    raise ValueError('LOCAL_SEASON_LOCKED')
                if repos.season_bought(mc, uid, req.id):
                    raise ValueError('LOCAL_ALREADY_OWNED')
                costs = [(services.SEASON_ORB[0], services.SEASON_ORB[1], row['Price'])] + ([(1, 0, row['Silver'])] if row['Term'] < season and row['Silver'] else [])
                keys = services.economy._pay(mc, uid, costs)
                u = answer.response.seasonShopBuyResponse.commonUpdate
                got = services.economy._grant(mc, uid, row['ItemType'], row['ItemId'], row['ItemNum'])
                u.addItemList.add(ItemType=got['item_type'], ItemId=got['item_id'], ItemNum=got['added'])
                for it in services.economy._snapshot(mc, uid, keys):
                    u.itemList.add(ItemType=it['item_type'], ItemId=it['item_id'], ItemNum=it['count'])
                repos.season_record_buy(mc, uid, req.id)
        answer.response.errcode = ''
    except ValueError as exc:
        answer.response.errcode = str(exc)
    return answer, jobs
