import os
from functools import wraps

import psycopg2
from flask import Flask, redirect, render_template, request, session, url_for
from psycopg2.extras import RealDictCursor

from rules import (
    GRADE_LABELS,
    GRADE_ORDER,
    GRADE_SWATCHES,
    expected_grade,
    rule_ranges,
    weigh,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tea-cupping-dev-secret")

ACCOUNTS = {
    "taster": {"password": "tea123456", "role": "writer"},
    "observer": {"password": "look123456", "role": "reader"},
}


def db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def login_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrap


def fetch_rules(cur):
    """读比色规则表，按上限升序（浅→中→深）。"""
    cur.execute("SELECT grade, max_score, max_inclusive FROM color_rules ORDER BY max_score")
    rules = cur.fetchall()
    for rule, text in zip(rules, rule_ranges(rules)):
        rule["range_text"] = text
    return rules


def can_write():
    return session.get("role") == "writer"


@app.get("/health")
def health():
    return {"status": "ok", "service": "tea-blend-cupping"}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        account = ACCOUNTS.get(name)
        if not account or account["password"] != request.form.get("password", ""):
            error = "用户名或密码错误"
        else:
            session["user"] = name
            session["role"] = account["role"]
            return redirect(url_for("home"))
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def home():
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM cuppings ORDER BY id DESC")
        rows = cur.fetchall()
        rules = fetch_rules(cur)
    return render_template(
        "home.html",
        rows=rows,
        rules=rules,
        labels=GRADE_LABELS,
        swatches=GRADE_SWATCHES,
        can_write=can_write(),
    )


@app.post("/cuppings")
@login_required
def create():
    if not can_write():
        return ("仅审评员可提交拼配审评", 403)
    aroma = float(request.form["aroma"])
    taste = float(request.form["taste"])
    liquor = float(request.form["liquor"])
    lot = request.form["lot"].strip()
    grade = request.form.get("grade", "")
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        rules = fetch_rules(cur)
        known = {r["grade"] for r in rules}
        if grade not in known:
            return ("交评必须勾选汤色比色卡档位", 400)
        want = expected_grade(liquor, rules)
        if grade != want:
            return (
                f"汤色分 {liquor:g} 按规则应勾{GRADE_LABELS[want]}，"
                f"与所选{GRADE_LABELS[grade]}不符，整笔拒交",
                422,
            )
        verdict, note, score = weigh(aroma, taste, liquor)
        cur.execute(
            """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, grade, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (lot, aroma, taste, liquor, score, verdict, note, grade, session["user"]),
        )
        row = cur.fetchone()
        # 同一事务落比色台账，冻结当时汤色分、档位、批次、提交人、时刻
        cur.execute(
            """INSERT INTO color_ledger (cupping_id, lot, liquor, grade, created_by)
               VALUES (%s,%s,%s,%s,%s)""",
            (row["id"], lot, liquor, grade, session["user"]),
        )
        conn.commit()
    if request.headers.get("HX-Request"):
        return render_template("_row.html", row=row)
    return redirect(url_for("home"))


@app.get("/colorimetry")
@login_required
def colorimetry():
    lot = request.args.get("lot", "").strip()
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        rules = fetch_rules(cur)
        cur.execute("SELECT DISTINCT lot FROM color_ledger ORDER BY lot")
        lots = [r["lot"] for r in cur.fetchall()]
        if lot:
            cur.execute(
                "SELECT * FROM color_ledger WHERE lot = %s ORDER BY id DESC", (lot,)
            )
        else:
            cur.execute("SELECT * FROM color_ledger ORDER BY id DESC")
        entries = cur.fetchall()
    return render_template(
        "colorimetry.html",
        rules=rules,
        entries=entries,
        lots=lots,
        lot=lot,
        labels=GRADE_LABELS,
        swatches=GRADE_SWATCHES,
        can_write=can_write(),
    )


@app.post("/colorimetry/rules")
@login_required
def update_rules():
    if not can_write():
        return ("仅审评员可改比色规则", 403)
    try:
        bounds = {g: float(request.form[f"max_{g}"]) for g in GRADE_ORDER}
    except (KeyError, ValueError):
        return ("规则上限缺失或不是数字", 400)
    shallow, medium, deep = (bounds[g] for g in GRADE_ORDER)
    if not (0 < shallow < medium <= deep <= 10):
        return ("规则上限需满足 0 < 浅档 < 中档 ≤ 深档 ≤ 10", 400)
    with db() as conn, conn.cursor() as cur:
        for g in GRADE_ORDER:
            cur.execute(
                "UPDATE color_rules SET max_score = %s WHERE grade = %s", (bounds[g], g)
            )
        conn.commit()
    return redirect(url_for("colorimetry"))
