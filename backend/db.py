"""PyMySQL 連線輔助:每次請求取得短連線,回傳 dict rows。"""
from contextlib import contextmanager

import pymysql
from dbutils.pooled_db import PooledDB

import config

_pool = None


def _connect_kwargs(with_db: bool = True):
    return {
        "host": config.DB_HOST,
        "port": config.DB_PORT,
        "user": config.DB_USER,
        "password": config.DB_PASSWORD,
        "database": config.DB_NAME if with_db else None,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    }


def _get_pool():
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            mincached=0,
            maxcached=config.DB_POOL_SIZE,
            maxconnections=config.DB_POOL_SIZE,
            blocking=True,
            ping=1,
            **_connect_kwargs(),
        )
    return _pool


def get_connection(with_db: bool = True):
    # init_db 建立 schema 前需要不指定 database，該特殊連線不進 pool。
    if not with_db:
        return pymysql.connect(**_connect_kwargs(with_db=False))
    return _get_pool().connection()


@contextmanager
def db_cursor(commit: bool = False):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql, args=None):
    with db_cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def query_one(sql, args=None):
    with db_cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchone()


def execute(sql, args=None):
    """執行寫入,回傳 lastrowid。"""
    with db_cursor(commit=True) as cur:
        cur.execute(sql, args or ())
        return cur.lastrowid
