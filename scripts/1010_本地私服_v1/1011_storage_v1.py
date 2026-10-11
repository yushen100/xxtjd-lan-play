"""SQLite 连接边界；提交、回滚和事务范围由业务调用者决定。"""
import sqlite3


class SQLiteStore:
    def __init__(self, path, timeout=15):
        self.path, self.timeout = path, timeout

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn
