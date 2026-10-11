"""原生 TCP 帧边界；不认识账号、房间、奖励或数据库。"""
import struct
import zlib


def encode_reply(message):
    payload = message.SerializeToString()
    if len(payload) > 65535:
        raise ValueError('本地响应超过单帧长度')
    return struct.pack('<H', len(payload)) + payload


def read_messages(stream, decode, *, battle=False):
    buffer = b''
    while True:
        part = stream.recv(8192)
        if not part:
            return
        buffer += part
        while len(buffer) >= 2:
            size = struct.unpack('<H', buffer[:2])[0]
            if size < 4:
                raise ValueError('帧长度不足')
            if len(buffer) < size + 2:
                break
            crc = struct.unpack('<I', buffer[2:6])[0]
            payload = buffer[6:size + 2]
            buffer = buffer[size + 2:]
            if battle and crc != 0:
                raise ValueError('战斗TCP帧校验字段应为零')
            message = decode(payload)
            expected = zlib.crc32(payload + b'VSzEI3') if not battle and message.type == 3002 else 0
            if crc != expected:
                raise ValueError('帧校验失败')
            if battle and not 1000 <= message.type < 2000:
                raise ValueError('非战斗消息')
            yield message
