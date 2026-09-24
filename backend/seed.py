import os
import time

import psycopg2

from rules import DEFAULT_RULES, SHADE_TIERS, expected_shade, weigh


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
    # 比色卡功能上线后的列（对已有卷做幂等迁移）
    cur.execute("ALTER TABLE cuppings ADD COLUMN IF NOT EXISTS shade text NOT NULL DEFAULT ''")
    cur.execute(
        "ALTER TABLE cuppings ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now()"
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS shade_rules (
            tier text PRIMARY KEY,
            upper double precision,
            upper_inclusive boolean,
            updated_by text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    for tier, _label in SHADE_TIERS:
        cfg = DEFAULT_RULES[tier]
        cur.execute(
            """INSERT INTO shade_rules (tier, upper, upper_inclusive, updated_by)
               VALUES (%s,%s,%s,'seed')
               ON CONFLICT (tier) DO NOTHING""",
            (tier, cfg["upper"], cfg["upper_inclusive"]),
        )

    # 比色台账：只追加、不更新不删除；汤色分/档位/批次/提交人/时刻均在落账时冻结
    cur.execute(
        """CREATE TABLE IF NOT EXISTS shade_ledger (
            id serial PRIMARY KEY,
            cupping_id bigint NOT NULL,
            lot text NOT NULL,
            liquor double precision NOT NULL,
            shade text NOT NULL,
            submitted_by text NOT NULL,
            submitted_at timestamptz NOT NULL
        )"""
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_shade_ledger_lot ON shade_ledger (lot)")

    cur.execute("SELECT COUNT(*) FROM cuppings")
    if cur.fetchone()[0] == 0:
        for lot, aroma, taste, liquor in (("春茶-A", 8, 8, 7), ("夏茶-C", 5, 4, 6)):
            verdict, note, score = weigh(aroma, taste, liquor)
            cur.execute(
                """INSERT INTO cuppings (lot, aroma, taste, liquor, shade, score, verdict, note, created_by)
                   VALUES (%s,%s,%s,%s,'',%s,%s,%s,%s)""",
                (lot, aroma, taste, liquor, score, verdict, note, "taster"),
            )

    # 统一回填：凡未带档位的审评（种子行或功能上线前的旧行），
    # 按当前规则补档位并补落冻结台账（shade 非空后不再重复，幂等）
    cur.execute("SELECT tier, upper, upper_inclusive FROM shade_rules")
    rules = [
        {"tier": tier, "upper": upper, "upper_inclusive": inclusive}
        for tier, upper, inclusive in cur.fetchall()
    ]
    cur.execute("SELECT id, lot, liquor, created_by, created_at FROM cuppings WHERE shade = ''")
    for cid, lot, liquor, created_by, created_at in cur.fetchall():
        shade = expected_shade(liquor, rules)
        cur.execute("UPDATE cuppings SET shade = %s WHERE id = %s", (shade, cid))
        cur.execute(
            """INSERT INTO shade_ledger
                   (cupping_id, lot, liquor, shade, submitted_by, submitted_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (cid, lot, liquor, shade, created_by, created_at),
        )

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
