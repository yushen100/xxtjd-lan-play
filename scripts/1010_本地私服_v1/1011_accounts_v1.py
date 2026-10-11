"""账号、英雄和背包的数据访问；传入连接时不提交调用者的事务。"""


class AccountRepository:
    PROFILE_COLUMNS = frozenset(('head_image','head_box','chat_box'))

    def __init__(self, connect):
        self.connect = connect

    @staticmethod
    def account(conn, account):
        return conn.execute('SELECT * FROM accounts WHERE account=?',(account,)).fetchone()

    @staticmethod
    def account_by_uid(conn, uid):
        return conn.execute('SELECT * FROM accounts WHERE uid=?',(uid,)).fetchone()

    @staticmethod
    def inventory(conn, uid):
        return [dict(row) for row in conn.execute('SELECT item_id,count FROM inventory WHERE uid=? ORDER BY item_id',(uid,))]

    @staticmethod
    def heroes(conn, uid):
        return conn.execute('SELECT * FROM heroes WHERE uid=? ORDER BY hero_id',(uid,)).fetchall()

    @staticmethod
    def hero_count(conn, uid):
        return conn.execute('SELECT COUNT(*) FROM heroes WHERE uid=?',(uid,)).fetchone()[0]

    @staticmethod
    def equip_skin(conn, uid, hero_id, skin_id):
        return conn.execute('UPDATE heroes SET skin_id=? WHERE uid=? AND hero_id=?',(skin_id,uid,hero_id)).rowcount

    @staticmethod
    def has_skin(conn, uid, skin_id):
        return conn.execute('SELECT 1 FROM owned_skins WHERE uid=? AND skin_id=?',(uid,skin_id)).fetchone()

    def real_accounts(self):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT account, uid, name FROM accounts WHERE uid NOT LIKE 'local-bot-%'")]

    @staticmethod
    def ensure_profile(conn, uid):
        conn.execute('CREATE TABLE IF NOT EXISTS user_profile(uid TEXT PRIMARY KEY, head_image INTEGER DEFAULT 1, head_box INTEGER DEFAULT 0, chat_box INTEGER DEFAULT 0)')
        conn.execute('INSERT OR IGNORE INTO user_profile(uid) VALUES(?)',(uid,))

    def profile(self, uid):
        with self.connect() as conn:
            self.ensure_profile(conn,uid)
            return dict(conn.execute('SELECT * FROM user_profile WHERE uid=?',(uid,)).fetchone())

    def set_profile(self, uid, column, value):
        if column not in self.PROFILE_COLUMNS:
            raise ValueError('LOCAL_PROFILE_FIELD_INVALID')
        with self.connect() as conn:
            self.ensure_profile(conn,uid)
            conn.execute(f'UPDATE user_profile SET {column}=? WHERE uid=?',(value,uid))
