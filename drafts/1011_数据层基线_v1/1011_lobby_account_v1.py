"""大厅业务处理器；不读取服务器全局对象，不管理 TCP 连接。"""
import json
import sqlite3
import time
import re
from pathlib import Path


def handle_heartbeat(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    answer.uid = services.state(account)['uid']
    answer.response.errcode = ''
    answer.response.heartbeatResponse.now = int(time.time())
    return answer, jobs


def handle_chat(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    d = answer.response.seasonInfoResponse; d.SetInParent()
    info = services.season_info(uid)
    d.seasonId = info['season']; d.seasonEndUnix = info['end']; d.seasonEndIsShow = True
    d.award1CurMaxRankScore = info['score']; d.award1TargetMaxRankScore = services.SEASON_AWARD1['score']
    d.award1CurWinNum = info['wins']; d.award1TargetWinNum = services.SEASON_AWARD1['wins']
    d.award1.CopyFrom(services.pb.CommonItem(ItemType=services.SEASON_ORB[0], ItemId=services.SEASON_ORB[1], ItemNum=1))
    d.award2.CopyFrom(services.pb.CommonItem(ItemType=2, ItemId=0, ItemNum=info['gold']))
    d.curMaxRankId = info['rank_id']; d.curMaxRankScore = info['score']
    d.targetMaxRankScore = info['next_score']; d.award2TargetMaxRankId = services.SEASON_AWARD2_RANK
    d.seasonFightNum = info['fights']; d.seasonWinNum = info['wins']; d.seasonMaxMultiWinNum = info['streak']
    answer.response.errcode = ''
    return answer, jobs


def handle_profile(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    if message.type == 3067:
        services.set_profile(uid, 'head_image', message.request.headImageChangeRequest.id)
    elif message.type == 3075:
        services.set_profile(uid, 'head_box', message.request.headBoxChangeRequest.headBoxId)
    elif message.type == 3101:
        services.set_profile(uid, 'chat_box', message.request.chatBoxIdSetRequest.id)
    else:
        target = message.request.userInfoBaseRequest.uid or uid
        with services.connect_db() as pc:
            acc = services.accounts.account_by_uid(pc, target)
            heroes = services.accounts.hero_count(pc, target)
        prof = services.profile(target)
        d = answer.response.userInfoBaseResponse
        d.uid = target; d.name = acc['name'] if acc else target
        d.level = services.LOCAL_LEVEL; d.exp = 0; d.headImage = prof['head_image']
        d.headBoxId = prof['head_box']; d.chatBoxId = prof['chat_box']
        d.status = 1 if target in services.bridge.game_peers else 0
        d.heroNum = heroes; d.skinNum = sum(1 for k in services.TABLES['SkinIdBaseVo'] if k['IsOpen'] == 1)
        si = services.season_info(target); d.rankId = services.LOCAL_RANK_ID; d.maxRankId = si['rank_id']; d.rankScore = services.LOCAL_RANK_SCORE; d.maxRankScore = si['score']
        d.registerUnix = int(time.time()) - 86400; d.loginNum = 1
    name = services.MSG_NAMES.get(message.type, '')
    field = name[4:5].lower() + name[5:] + 'Response' if name else ''
    if field in answer.response.DESCRIPTOR.fields_by_name:
        getattr(answer.response, field).SetInParent()
    answer.response.errcode = ''
    return answer, jobs


def handle_unknown(services, message, account, uid, next_operation):
    answer = services.pb.Message(type=message.type, seq=message.seq)
    jobs = []
    name = services.MSG_NAMES.get(message.type, '')
    field = name[4:5].lower() + name[5:] + 'Response' if name else ''
    readonly = any(k in name for k in ('Info', 'List', 'Status', 'Query', 'Get', 'Rank', 'Report'))
    if readonly and field in answer.response.DESCRIPTOR.fields_by_name:
        getattr(answer.response, field).SetInParent()
        answer.response.errcode = ''
    elif readonly:
        answer.response.errcode = ''
    else:
        answer.response.errcode = f'LOCAL_UNSUPPORTED:{message.type}'
    return answer, jobs
