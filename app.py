import os
import sys
import sqlite3
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, g, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from translations import TRANSLATIONS

SHOP_NAME = "Talha Tailor Shop"   # change to the client's shop name

GARMENT_TYPES = ["shalwar_kameez", "shirt", "waistcoat", "pant"]
MEASURE_FIELDS = ["length", "sleeve", "shoulder", "neck", "chest",
                  "waist", "hip", "ghera", "shalwar_length", "paincha"]
STYLE_FIELDS = ["style_front", "style_side", "style_collar_band", "style_cuff"]


def resource_path(relative):
    """Finds bundled files, both in normal Python and inside the .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def data_folder():
    """Where the client's database lives (safe from app updates)."""
    folder = os.path.join(os.environ.get("APPDATA", "."), "TailorShop")
    os.makedirs(folder, exist_ok=True)
    return folder


app = Flask(__name__,
            template_folder=resource_path("templates"),
            static_folder=resource_path("static"))
app.secret_key = os.urandom(24)  
app.config["SESSION_PERMANENT"] = False 
DB_PATH = os.path.join(data_folder(), "tailor.db")


# ---------- Database ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    address TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (date('now','localtime'))
);
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    garment_type TEXT NOT NULL,
    length REAL, sleeve REAL, shoulder REAL, neck REAL,
    chest REAL, waist REAL, hip REAL, ghera REAL,
    shalwar_length REAL, paincha REAL,
    style_front TEXT, style_side TEXT, style_collar_band TEXT, style_cuff TEXT,
    notes TEXT,
    updated_at TEXT DEFAULT (date('now','localtime'))
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    garment_type TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    total_amount REAL DEFAULT 0,
    order_date TEXT DEFAULT (date('now','localtime')),
    delivery_date TEXT,
    status TEXT DEFAULT 'New',
    notes TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    amount REAL NOT NULL,
    paid_on TEXT DEFAULT (date('now','localtime')),
    note TEXT
);
"""


def init_db():
    """Creates tables on first run and adds the default login."""
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                   ("admin", generate_password_hash("admin123")))
    db.commit()
    db.close()

def load_shop_name():
    """Restores a previously saved shop name, if any, when the app starts."""
    global SHOP_NAME
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT value FROM settings WHERE key = 'shop_name'").fetchone()
    if row and row[0]:
        SHOP_NAME = row[0]
    db.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()


# ---------- Language ----------
@app.context_processor
def inject_globals():
    """Makes t(), lang and other shared values available in every template."""
    lang = get_setting("lang", "en")

    def t(key):
        entry = TRANSLATIONS.get(key)
        return entry.get(lang, entry["en"]) if entry else key

    return {"t": t, "lang": lang,
            "direction": "rtl" if lang == "ur" else "ltr",
            "shop_name": SHOP_NAME,
            "garment_types": GARMENT_TYPES,
            "measure_fields": MEASURE_FIELDS,
            "style_fields": STYLE_FIELDS}


@app.route("/language/<code>")
def change_language(code):
    if code in ("en", "ur"):
        set_setting("lang", code)
    return redirect(request.referrer or url_for("home"))


# ---------- Helpers ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------- Login / Dashboard ----------
@app.route("/")
def home():
    return redirect(url_for("dashboard" if "user" in session else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("wrong_login", "error")      # a translation KEY, not plain text
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("logged_out", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    active = "status NOT IN ('Delivered','Cancelled')"
    today = "date('now','localtime')"
    stats = {
        "customers": db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "active": db.execute(f"SELECT COUNT(*) FROM orders WHERE {active}").fetchone()[0],
        "today": db.execute(f"SELECT COUNT(*) FROM orders WHERE {active} AND delivery_date = {today}").fetchone()[0],
        "overdue": db.execute(f"SELECT COUNT(*) FROM orders WHERE {active} AND delivery_date < {today}").fetchone()[0],
    }
    upcoming = db.execute(
        f"""SELECT o.id, o.garment_type, o.delivery_date, o.status, c.name AS customer_name
            FROM orders o JOIN customers c ON c.id = o.customer_id
            WHERE {active} AND o.delivery_date IS NOT NULL
            ORDER BY o.delivery_date ASC LIMIT 8""").fetchall()
    return render_template("dashboard.html", stats=stats, upcoming=upcoming,
                           today=db.execute(f"SELECT {today}").fetchone()[0])


# ---------- Customers ----------
def customer_values():
    """Reads the customer form fields."""
    return (request.form.get("name", "").strip(),
            request.form.get("phone", "").strip(),
            request.form.get("address", "").strip(),
            request.form.get("notes", "").strip())


@app.route("/customers")
@login_required
def customers():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? ORDER BY id DESC",
            (like, like)).fetchall()
    else:
        rows = db.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    return render_template("customers.html", customers=rows, q=q)


@app.route("/customers/add", methods=["GET", "POST"])
@login_required
def add_customer():
    if request.method == "POST":
        name, phone, address, notes = customer_values()
        if not name:
            flash("name_required", "error")
            return render_template("customer_form.html", customer=request.form, editing=False)
        db = get_db()
        db.execute("INSERT INTO customers (name, phone, address, notes) VALUES (?, ?, ?, ?)",
                   (name, phone, address, notes))
        db.commit()
        flash("customer_added", "success")
        return redirect(url_for("customers"))
    return render_template("customer_form.html", customer={}, editing=False)


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if customer is None:
        abort(404)
    if request.method == "POST":
        name, phone, address, notes = customer_values()
        if not name:
            flash("name_required", "error")
            return render_template("customer_form.html", customer=request.form, editing=True)
        db.execute("UPDATE customers SET name = ?, phone = ?, address = ?, notes = ? WHERE id = ?",
                   (name, phone, address, notes, customer_id))
        db.commit()
        flash("customer_updated", "success")
        return redirect(url_for("customers"))
    return render_template("customer_form.html", customer=customer, editing=True)


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
@login_required
def delete_customer(customer_id):
    db = get_db()
    db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    db.commit()
    flash("customer_deleted", "success")
    return redirect(url_for("customers"))


# ---------- Measurements ----------
MEASUREMENT_COLUMNS = (["customer_id", "garment_type"] + MEASURE_FIELDS
                       + STYLE_FIELDS + ["notes"])


def read_measurement_form():
    """Reads the measurement form into a dictionary."""
    def number(name):
        value = request.form.get(name, "").strip()
        try:
            return float(value) if value else None
        except ValueError:
            return None

    data = {
        "customer_id": request.form.get("customer_id", type=int),
        "garment_type": request.form.get("garment_type", ""),
        "notes": request.form.get("notes", "").strip(),
    }
    for field in MEASURE_FIELDS:
        data[field] = number(field)
    for field in STYLE_FIELDS:
        data[field] = request.form.get(field, "").strip()
    return data


def measurement_is_valid(db, data):
    """A measurement needs a real customer and a known garment type."""
    if data["garment_type"] not in GARMENT_TYPES or data["customer_id"] is None:
        return False
    row = db.execute("SELECT id FROM customers WHERE id = ?",
                     (data["customer_id"],)).fetchone()
    return row is not None


@app.route("/measurements")
@login_required
def measurements():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    rows = get_db().execute(
        """SELECT m.*, c.name AS customer_name, c.phone AS customer_phone
           FROM measurements m JOIN customers c ON c.id = m.customer_id
           WHERE c.name LIKE ? OR c.phone LIKE ?
           ORDER BY m.updated_at DESC, m.id DESC""", (like, like)).fetchall()
    return render_template("measurements.html", measurements=rows, q=q)


@app.route("/measurements/add", methods=["GET", "POST"])
@login_required
def add_measurement():
    db = get_db()
    all_customers = db.execute(
        "SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    if request.method == "POST":
        data = read_measurement_form()
        if not measurement_is_valid(db, data):
            flash("measurement_invalid", "error")
            return render_template("measurement_form.html", measurement=request.form,
                                   all_customers=all_customers, editing=False)
        placeholders = ", ".join("?" for _ in MEASUREMENT_COLUMNS)
        db.execute(
            f"INSERT INTO measurements ({', '.join(MEASUREMENT_COLUMNS)}) VALUES ({placeholders})",
            [data[c] for c in MEASUREMENT_COLUMNS])
        db.commit()
        flash("measurement_added", "success")
        return redirect(url_for("measurements"))
    preselected = request.args.get("customer_id", type=int)   # from the customers list
    return render_template("measurement_form.html", measurement={"customer_id": preselected},
                           all_customers=all_customers, editing=False)


@app.route("/measurements/<int:measurement_id>/edit", methods=["GET", "POST"])
@login_required
def edit_measurement(measurement_id):
    db = get_db()
    row = db.execute("SELECT * FROM measurements WHERE id = ?", (measurement_id,)).fetchone()
    if row is None:
        abort(404)
    all_customers = db.execute(
        "SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    if request.method == "POST":
        data = read_measurement_form()
        if not measurement_is_valid(db, data):
            flash("measurement_invalid", "error")
            return render_template("measurement_form.html", measurement=request.form,
                                   all_customers=all_customers, editing=True)
        assignments = ", ".join(f"{c} = ?" for c in MEASUREMENT_COLUMNS)
        db.execute(
            f"UPDATE measurements SET {assignments}, updated_at = date('now','localtime') WHERE id = ?",
            [data[c] for c in MEASUREMENT_COLUMNS] + [measurement_id])
        db.commit()
        flash("measurement_updated", "success")
        return redirect(url_for("measurements"))
    return render_template("measurement_form.html", measurement=row,
                           all_customers=all_customers, editing=True)


@app.route("/measurements/<int:measurement_id>/delete", methods=["POST"])
@login_required
def delete_measurement(measurement_id):
    db = get_db()
    db.execute("DELETE FROM measurements WHERE id = ?", (measurement_id,))
    db.commit()
    flash("measurement_deleted", "success")
    return redirect(url_for("measurements"))

# ---------- Orders ----------
ORDER_STATUSES = ["New", "Stitching", "Ready", "Delivered", "Cancelled"]


def order_totals(db, order_id):
    """Returns (total, paid, remaining) for one order."""
    order = db.execute("SELECT total_amount FROM orders WHERE id = ?", (order_id,)).fetchone()
    paid = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE order_id = ?",
        (order_id,)).fetchone()[0]
    total = order["total_amount"] if order else 0
    return total, paid, round(total - paid, 2)


def read_order_form():
    def number(name, default=0):
        value = request.form.get(name, "").strip()
        try:
            return float(value) if value else default
        except ValueError:
            return default

    return {
        "customer_id": request.form.get("customer_id", type=int),
        "garment_type": request.form.get("garment_type", ""),
        "quantity": int(number("quantity", 1)) or 1,
        "total_amount": number("total_amount", 0),
        "delivery_date": request.form.get("delivery_date", "").strip() or None,
        "status": request.form.get("status", "New"),
        "notes": request.form.get("notes", "").strip(),
        "advance": number("advance", 0),   # only used when creating a new order
    }


def order_is_valid(db, data):
    if data["garment_type"] not in GARMENT_TYPES or data["customer_id"] is None:
        return False
    if data["status"] not in ORDER_STATUSES or data["total_amount"] < 0:
        return False
    row = db.execute("SELECT id FROM customers WHERE id = ?",
                     (data["customer_id"],)).fetchone()
    return row is not None


@app.route("/orders")
@login_required
def orders():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    db = get_db()
    sql = """SELECT o.*, c.name AS customer_name, c.phone AS customer_phone,
                    COALESCE((SELECT SUM(amount) FROM payments WHERE order_id = o.id), 0) AS paid
             FROM orders o JOIN customers c ON c.id = o.customer_id
             WHERE (c.name LIKE ? OR c.phone LIKE ?)"""
    params = [f"%{q}%", f"%{q}%"]
    if status in ORDER_STATUSES:
        sql += " AND o.status = ?"
        params.append(status)
    sql += " ORDER BY o.id DESC"
    rows = db.execute(sql, params).fetchall()
    return render_template("orders.html", orders=rows, q=q, status=status,
                           statuses=ORDER_STATUSES, today=get_db().execute(
                               "SELECT date('now','localtime')").fetchone()[0])


@app.route("/orders/add", methods=["GET", "POST"])
@login_required
def add_order():
    db = get_db()
    all_customers = db.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    if request.method == "POST":
        data = read_order_form()
        if not order_is_valid(db, data):
            flash("order_invalid", "error")
            return render_template("order_form.html", order=request.form,
                                   all_customers=all_customers, statuses=ORDER_STATUSES, editing=False)
        cur = db.execute(
            """INSERT INTO orders (customer_id, garment_type, quantity, total_amount,
                                   delivery_date, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (data["customer_id"], data["garment_type"], data["quantity"], data["total_amount"],
             data["delivery_date"], data["status"], data["notes"]))
        order_id = cur.lastrowid
        if data["advance"] > 0:
            db.execute("INSERT INTO payments (order_id, amount, note) VALUES (?, ?, ?)",
                      (order_id, min(data["advance"], data["total_amount"]), "Advance"))
        db.commit()
        flash("order_added", "success")
        return redirect(url_for("view_order", order_id=order_id))
    preselected = request.args.get("customer_id", type=int)
    return render_template("order_form.html", order={"customer_id": preselected, "quantity": 1, "status": "New"},
                           all_customers=all_customers, statuses=ORDER_STATUSES, editing=False)


@app.route("/orders/<int:order_id>/edit", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        abort(404)
    all_customers = db.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    if request.method == "POST":
        data = read_order_form()
        if not order_is_valid(db, data):
            flash("order_invalid", "error")
            return render_template("order_form.html", order=request.form,
                                   all_customers=all_customers, statuses=ORDER_STATUSES, editing=True)
        db.execute(
            """UPDATE orders SET customer_id = ?, garment_type = ?, quantity = ?, total_amount = ?,
                                 delivery_date = ?, status = ?, notes = ? WHERE id = ?""",
            (data["customer_id"], data["garment_type"], data["quantity"], data["total_amount"],
             data["delivery_date"], data["status"], data["notes"], order_id))
        db.commit()
        flash("order_updated", "success")
        return redirect(url_for("view_order", order_id=order_id))
    return render_template("order_form.html", order=order, all_customers=all_customers,
                           statuses=ORDER_STATUSES, editing=True)


@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id):
    db = get_db()
    db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    db.commit()
    flash("order_deleted", "success")
    return redirect(url_for("orders"))


@app.route("/orders/<int:order_id>")
@login_required
def view_order(order_id):
    db = get_db()
    order = db.execute(
        """SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, c.address AS customer_address
           FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = ?""",
        (order_id,)).fetchone()
    if order is None:
        abort(404)
    payments = db.execute(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC", (order_id,)).fetchall()
    total, paid, remaining = order_totals(db, order_id)
    return render_template("order_view.html", order=order, payments=payments,
                           total=total, paid=paid, remaining=remaining)


@app.route("/orders/<int:order_id>/payments/add", methods=["POST"])
@login_required
def add_payment(order_id):
    db = get_db()
    order = db.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        abort(404)
    amount_raw = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip()
    try:
        amount = float(amount_raw)
    except ValueError:
        amount = 0
    _, _, remaining = order_totals(db, order_id)
    if amount <= 0:
        flash("payment_invalid", "error")
    elif amount > remaining + 0.01:
        flash("payment_too_much", "error")
    else:
        db.execute("INSERT INTO payments (order_id, amount, note) VALUES (?, ?, ?)",
                  (order_id, amount, note))
        db.commit()
        flash("payment_added", "success")
    return redirect(url_for("view_order", order_id=order_id))

# ---------- Settings ----------
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    global SHOP_NAME
    if request.method == "POST" and request.form.get("form") == "shop_name":
        new_name = request.form.get("shop_name", "").strip()
        if new_name:
            SHOP_NAME = new_name
            set_setting("shop_name", new_name)
            flash("shop_name_saved", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html")


@app.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    db = get_db()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    user = db.execute("SELECT * FROM users WHERE username = ?", (session["user"],)).fetchone()

    if not check_password_hash(user["password_hash"], current):
        flash("current_password_wrong", "error")
    elif len(new) < 4:
        flash("password_too_short", "error")
    elif new != confirm:
        flash("password_mismatch", "error")
    else:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                  (generate_password_hash(new), user["id"]))
        db.commit()
        flash("password_changed", "success")
    return redirect(url_for("settings_page"))

@app.route("/settings/username", methods=["POST"])
@login_required
def change_username():
    db = get_db()
    new_username = request.form.get("new_username", "").strip()
    current_password = request.form.get("current_password_for_username", "")
    user = db.execute("SELECT * FROM users WHERE username = ?", (session["user"],)).fetchone()

    if not check_password_hash(user["password_hash"], current_password):
        flash("current_password_wrong", "error")
    elif not new_username:
        flash("username_required", "error")
    elif db.execute("SELECT id FROM users WHERE username = ? AND id != ?",
                    (new_username, user["id"])).fetchone():
        flash("username_taken", "error")
    else:
        db.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user["id"]))
        db.commit()
        session["user"] = new_username
        flash("username_changed", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/backup")
@login_required
def backup():
    from flask import send_file
    return send_file(DB_PATH, as_attachment=True, download_name="tailor-shop-backup.db")

if __name__ == "__main__":
    import threading
    import webbrowser
    from waitress import serve

    init_db()
    load_shop_name()

    import time
    url = f"http://127.0.0.1:8000/?t={int(time.time())}"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print("Tailor Shop is running at http://127.0.0.1:8000  (keep this window open)")
    serve(app, host="127.0.0.1", port=8000)