"""每连接独立的有界发送队列，慢客户端不阻塞全局帧调度。"""
import queue
import threading

class BoundedSender:
    def __init__(self, write, disconnect, max_messages=64, max_bytes=1024*1024):
        self.write, self.disconnect = write, disconnect
        self.queue = queue.Queue(maxsize=max_messages)
        self.max_bytes = max_bytes
        self.pending_bytes = 0
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def send(self, packet):
        with self.lock:
            if self.closed.is_set():
                raise OSError('连接发送器已关闭')
            if self.pending_bytes + len(packet) > self.max_bytes or self.queue.full():
                overflow = True
            else:
                self.pending_bytes += len(packet)
                self.queue.put_nowait(packet)
                overflow = False
        if overflow:
            self.close()
            raise OSError('客户端接收过慢，已断开；允许重连补帧')

    def _run(self):
        try:
            while not self.closed.is_set():
                try:
                    packet = self.queue.get(timeout=.2)
                except queue.Empty:
                    continue
                try:
                    self.write(packet)
                finally:
                    with self.lock:
                        self.pending_bytes -= len(packet)
        except OSError:
            self.close()

    def close(self):
        with self.lock:
            first = not self.closed.is_set()
            self.closed.set()
        if first:
            self.disconnect()
