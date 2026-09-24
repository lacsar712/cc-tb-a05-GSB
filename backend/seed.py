import os
import time

import psycopg2

from rules import DEFAULT_RULES, weigh


def connect():
    last = None
    for _ in range(30):
        try:
            return psycopg2.connect(os.environ["DATABASE_URL"])
        except psycopg2.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise last


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cuppings (
            id serial PRIMARY KEY,
            lot text NOT NULL,
            aroma double precision NOT NULL,
            taste double precision NOT NULL,
            liquor double precision NOT NULL,
            score double precision NOT NULL,
            verdict text NOT NULL,
            note text NOT NULL,
            created_by text NOT NULL
        )"""
    )
    # 老库升级：cuppings 补勾选档位列
    cur.execute("ALTER TABLE cuppings ADD COLUMN IF NOT EXISTS grade text")
    # 比色规则表：事后改表只影响新交评
    cur.execute(
        """CREATE TABLE IF NOT EXISTS color_rules (
            grade text PRIMARY KEY,
            max_score double precision NOT NULL,
            max_inclusive boolean NOT NULL
        )"""
    )
    cur.execute("SELECT COUNT(*) FROM color_rules")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO color_rules (grade, max_score, max_inclusive) VALUES (%s,%s,%s)",
            [(r["grade"], r["max_score"], r["max_inclusive"]) for r in DEFAULT_RULES],
        )
    # 比色台账：只增不改，冻结交评当时的事实
    cur.execute(
        """CREATE TABLE IF NOT EXISTS color_ledger (
            id serial PRIMARY KEY,
            cupping_id integer REFERENCES cuppings(id),
            lot text NOT NULL,
            liquor double precision NOT NULL,
            grade text NOT NULL,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    cur.execute("SELECT COUNT(*) FROM cuppings")
    if cur.fetchone()[0] == 0:
        for lot, aroma, taste, liquor in (("春茶-A", 8, 8, 7), ("夏茶-C", 5, 4, 6)):
            verdict, note, score = weigh(aroma, taste, liquor)
            cur.execute(
                """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (lot, aroma, taste, liquor, score, verdict, note, "taster"),
            )
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
