from A2rchi.utils.config_loader import Config_Loader
from flask_login import UserMixin

import os
import sqlite3

# load global config
global_config = Config_Loader().config["global"]


class User(UserMixin):
    def __init__(self, id_, email):
        self.id = id_
        self.email = email

    @staticmethod
    def get(user_id):
        # get cursor to db
        DB_PATH = os.path.join(global_config['DATA_PATH'], "flask_sqlite_db")
        db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = db.cursor()

        # execute query
        user = cursor.execute(
            "SELECT * FROM user WHERE id = ?", (user_id,)
        ).fetchone()
        cursor.close()
        db.close()

        if not user:
            return None

        return User(id_=user[0], email=user[1])

    @staticmethod
    def create(id_, email):
        # get cursor to db
        DB_PATH = os.path.join(global_config['DATA_PATH'], "flask_sqlite_db")
        db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = db.cursor()

        cursor.execute(
            "INSERT INTO user (id, email) "
            "VALUES (?, ?)",
            (id_, email),
        )
        db.commit()

        cursor.close()
        db.close()

