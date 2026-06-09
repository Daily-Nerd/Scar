import json

import psycopg2.pool

_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn="postgresql://app@db/app")


def get_session(session_id):
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM sessions WHERE id = %s", (session_id,))
            row = cur.fetchone()
            return json.loads(row[0]) if row else None
    finally:
        _pool.putconn(conn)


def put_session(session_id, data):
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (id, data) VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
                """,
                (session_id, json.dumps(data)),
            )
        conn.commit()
    finally:
        _pool.putconn(conn)


def delete_session(session_id):
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
        conn.commit()
    finally:
        _pool.putconn(conn)
