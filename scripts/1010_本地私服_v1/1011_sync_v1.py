"""同步消息边界：先提交帧，再生成投递任务；不处理结算或房间匹配。"""


class FrameSync:
    def __init__(self, pb, tick_many, recipients):
        self.pb, self.tick_many, self.recipients = pb, tick_many, recipients

    def end_notice(self):
        notice = self.pb.Message(type=1004)
        notice.response.errcode = ''
        return notice

    def frame_message(self, frame):
        from google.protobuf.json_format import ParseDict
        message = self.pb.Message(type=1001)
        message.response.errcode = ''
        ParseDict(frame, message.response.battleSyncResponse)
        return message

    def collect(self, room_ids):
        frames = self.tick_many(room_ids)
        jobs = []
        for room_id, frame in frames.items():
            message = self.frame_message(frame)
            jobs.extend((peer, message) for peer in self.recipients(room_id))
        return jobs
