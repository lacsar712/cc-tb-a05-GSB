import os
from functools import wraps

import psycopg2
from flask import Flask, abort, redirect, render_template, request, session, url_for
from psycopg2.extras import RealDictCursor

from rules import SHADE_LABELS, SHADE_TIERS, band_text, validate_shade, weigh

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tea-cupping-dev-secret")
app.jinja_env.globals["shade_labels"] = SHADE_LABELS
app.jinja_env.globals["band_text"] = band_text

LEDGER_PAGE_SIZE = 10

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


def writer_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "writer":
            abort(403)
        return fn(*args, **kwargs)

    return wrap


def load_rules(cur):
    cur.execute("SELECT tier, upper, upper_inclusive, updated_by, updated_at FROM shade_rules")
    return cur.fetchall()


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
        rules = load_rules(cur)
    return render_template(
        "home.html",
        rows=rows,
        rules=rules,
        can_write=session.get("role") == "writer",
        tiers=SHADE_TIERS,
    )


@app.post("/cuppings")
@writer_required
def create():
    aroma = float(request.form["aroma"])
    taste = float(request.form["taste"])
    liquor = float(request.form["liquor"])
    lot = request.form["lot"].strip()
    shade = request.form.get("shade", "").strip()

    # 交评必须勾选比色卡档位
    if shade not in SHADE_LABELS:
        return ("交评前必须在比色卡上勾选汤色档位", 400)

    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        rules = load_rules(cur)
        ok, expected = validate_shade(liquor, shade, rules)
        # 档位与汤色分不符：整笔拒交，审评表与比色台账均不增行
        if not ok:
            conn.rollback()
            return (
                f"档位与汤色分不符：汤色 {liquor:g} 分应选{SHADE_LABELS[expected]}，"
                f"不能勾{SHADE_LABELS[shade]}，整笔已拒交",
                400,
            )

        verdict, note, score = weigh(aroma, taste, liquor)
        cur.execute(
            """INSERT INTO cuppings
                   (lot, aroma, taste, liquor, shade, score, verdict, note, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING *""",
            (lot, aroma, taste, liquor, shade, score, verdict, note, session["user"]),
        )
        row = cur.fetchone()
        # 冻结快照落比色台账；此后规则表如何改都与本行无关
        cur.execute(
            """INSERT INTO shade_ledger
                   (cupping_id, lot, liquor, shade, submitted_by, submitted_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (row["id"], lot, liquor, shade, session["user"], row["created_at"]),
        )
        conn.commit()

    if request.headers.get("HX-Request"):
        return render_template("_row.html", row=row)
    return redirect(url_for("home"))


@app.get("/cuppings/<int:cupping_id>")
@login_required
def detail(cupping_id):
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM cuppings WHERE id = %s", (cupping_id,))
        row = cur.fetchone()
    if row is None:
        abort(404)
    return render_template("detail.html", row=row, shade_label=SHADE_LABELS.get(row["shade"], ""))


@app.get("/shades")
@login_required
def shades():
    lot = request.args.get("lot", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        rules = load_rules(cur)
        cur.execute("SELECT DISTINCT lot FROM shade_ledger ORDER BY lot")
        lots = [r["lot"] for r in cur.fetchall()]

        where = ""
        params: list = []
        if lot:
            where = "WHERE lot = %s"
            params.append(lot)
        cur.execute(f"SELECT COUNT(*) AS n FROM shade_ledger {where}", params)
        total = cur.fetchone()["n"]

        cur.execute(
            f"""SELECT * FROM shade_ledger {where}
                ORDER BY submitted_at DESC, id DESC
                LIMIT %s OFFSET %s""",
            params + [LEDGER_PAGE_SIZE, (page - 1) * LEDGER_PAGE_SIZE],
        )
        ledger = cur.fetchall()

    total_pages = max(1, (total + LEDGER_PAGE_SIZE - 1) // LEDGER_PAGE_SIZE)
    return render_template(
        "shades.html",
        rules=rules,
        rules_by_tier={r["tier"]: r for r in rules},
        ledger=ledger,
        lots=lots,
        lot=lot,
        page=page,
        total=total,
        total_pages=total_pages,
        can_write=session.get("role") == "writer",
        tiers=SHADE_TIERS,
    )


@app.post("/shades/rules/<tier>")
@writer_required
def update_rule(tier):
    if tier not in SHADE_LABELS or tier == "dark":
        abort(404)
    raw_upper = request.form.get("upper", "").strip()
    inclusive = request.form.get("upper_inclusive") == "on"
    try:
        upper = float(raw_upper)
    except ValueError:
        return ("档位上界必须是数字", 400)
    if not 0 <= upper <= 10:
        return ("档位上界须在 0 到 10 之间", 400)

    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT tier, upper FROM shade_rules ORDER BY upper")
        current = {r["tier"]: r["upper"] for r in cur.fetchall()}
        new_bounds = dict(current)
        new_bounds[tier] = upper
        if new_bounds["light"] is not None and new_bounds["medium"] is not None:
            if new_bounds["light"] > new_bounds["medium"]:
                conn.rollback()
                return ("浅档上界不能高于中档上界，区间不可倒挂", 400)
        cur.execute(
            """UPDATE shade_rules
                  SET upper = %s, upper_inclusive = %s,
                      updated_by = %s, updated_at = now()
                WHERE tier = %s""",
            (upper, inclusive, session["user"], tier),
        )
        conn.commit()
    return redirect(url_for("shades", lot=request.args.get("lot", "")))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
