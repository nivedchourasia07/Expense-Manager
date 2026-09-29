from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json, os
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
)
CSRFProtect(app)

DATA_FILE = "data.json"
CATEGORIES = ["Food", "Transport", "Rent", "Entertainment", "Study", "Other"]

# ---------- Database (JSON file) ----------
def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                db = json.load(f)
            if "users" in db:
                return db
            if isinstance(db.get("students"), list) and isinstance(db.get("expenses"), list):
                legacy_expenses = []
                for expense in db["expenses"]:
                    if isinstance(expense, dict):
                        expense.setdefault("date", "")
                        expense.setdefault("category", "Other")
                        expense.setdefault("mode", "equal")
                        legacy_expenses.append(expense)
                return {
                    "users": {},
                    "legacy_data": {"students": db["students"], "expenses": legacy_expenses},
                }
        except (json.JSONDecodeError, OSError):
            pass
    return {"users": {}}   # users: {username: {password, students, expenses}}

def save_db(db):
    with open(DATA_FILE, "w") as f:
        json.dump(db, f, indent=2)

# ---------- Login helper ----------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ---------- Accounting Logic ----------
def compute_balances(user):
    balances = {s: 0.0 for s in user["students"]}
    for exp in user["expenses"]:
        if exp["payer"] in balances:
            balances[exp["payer"]] += exp["amount"]
        for person, share in exp["shares"].items():
            if person in balances:
                balances[person] -= share
    return balances

def suggest_settlements(balances):
    debtors = sorted([(n, -b) for n, b in balances.items() if b < -0.01], key=lambda x: -x[1])
    creditors = sorted([(n, b) for n, b in balances.items() if b > 0.01], key=lambda x: -x[1])
    settlements, i, j = [], 0, 0
    while i < len(debtors) and j < len(creditors):
        dname, damt = debtors[i]
        cname, camt = creditors[j]
        pay = round(min(damt, camt), 2)
        settlements.append({"from": dname, "to": cname, "amount": pay})
        debtors[i] = (dname, round(damt - pay, 2))
        creditors[j] = (cname, round(camt - pay, 2))
        if debtors[i][1] <= 0.01:
            i += 1
        if creditors[j][1] <= 0.01:
            j += 1
    return settlements

def monthly_summary(user):
    totals = defaultdict(float)
    for exp in user["expenses"]:
        if exp.get("mode") == "payment" or not exp.get("date"):
            continue
        totals[exp["date"][:7]] += exp["amount"]
    result = {}
    for key in sorted(totals):
        label = datetime.strptime(key, "%Y-%m").strftime("%b %Y")
        result[label] = round(totals[key], 2)
    return result

def category_summary(user):
    cats = defaultdict(float)
    for exp in user["expenses"]:
        if exp.get("mode") == "payment":
            continue
        cats[exp.get("category", "Other")] += exp["amount"]
    return dict(sorted(cats.items(), key=lambda x: -x[1]))

# ---------- Public Pages ----------
@app.route("/")
def landing():
    if "username" in session:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        db = load_db()
        if not username or not password:
            flash("Please fill in all fields.", "error")
        elif len(username) < 3 or len(username) > 32:
            flash("Username must be between 3 and 32 characters.", "error")
        elif username in db["users"]:
            flash("That username is already taken.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif len(password) > 128:
            flash("Password must be 128 characters or fewer.", "error")
        else:
            legacy_data = db.pop("legacy_data", None) if not db["users"] else None
            db["users"][username] = {
                "password": generate_password_hash(password),
                "students": legacy_data["students"] if legacy_data else [],
                "expenses": legacy_data["expenses"] if legacy_data else [],
            }
            save_db(db)
            session.clear()
            session["username"] = username
            flash("Welcome! Your account is ready. 🎉", "success")
            return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        db = load_db()
        user = db["users"].get(username)
        if user and check_password_hash(user["password"], password):
            session.clear()
            session["username"] = username
            flash("Welcome back! 👋", "success")
            return redirect(url_for("dashboard"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))

# ---------- Dashboard ----------
@app.route("/dashboard")
@login_required
def dashboard():
    db = load_db()
    user = db["users"][session["username"]]
    balances = compute_balances(user)
    settlements = suggest_settlements(balances)
    months = monthly_summary(user)
    cats = category_summary(user)
    total_spent = round(sum(e["amount"] for e in user["expenses"] if e.get("mode") != "payment"), 2)
    total_owed = round(sum(-b for b in balances.values() if b < 0), 2)
    return render_template("dashboard.html",
                           user=user, balances=balances, settlements=settlements,
                           months=months, cats=cats, categories=CATEGORIES,
                           total_spent=total_spent, total_owed=total_owed,
                           months_max=max(months.values()) if months else 1,
                           cats_max=max(cats.values()) if cats else 1)

# ---------- Actions ----------
@app.route("/add_student", methods=["POST"])
@login_required
def add_student():
    db = load_db()
    user = db["users"][session["username"]]
    name = request.form.get("name", "").strip()
    if name and name not in user["students"]:
        user["students"].append(name)
        save_db(db)
        flash(f"{name} added to your group!", "success")
    return redirect(url_for("dashboard"))

@app.route("/add_expense", methods=["POST"])
@login_required
def add_expense():
    db = load_db()
    user = db["users"][session["username"]]
    description = request.form.get("description", "").strip()
    payer = request.form.get("payer")
    mode = request.form.get("mode")
    category = request.form.get("category", "Other")
    participants = request.form.getlist("participants")
    try:
        amount = float(request.form.get("amount", 0))
    except ValueError:
        amount = 0

    if not description or amount <= 0 or not participants or payer not in user["students"]:
        flash("Please fill the expense form correctly.", "error")
        return redirect(url_for("dashboard"))

    shares = {}
    if mode == "equal":
        share = round(amount / len(participants), 2)
        shares = {p: share for p in participants}
    else:
        try:
            values = [float(x) for x in request.form.get("shares", "").split(",")]
        except ValueError:
            values = []
        if len(values) == len(participants):
            for p, v in zip(participants, values):
                shares[p] = round(amount * v / 100, 2) if mode == "percent" else round(v, 2)

    if shares:
        user["expenses"].append({
            "description": description, "amount": round(amount, 2), "payer": payer,
            "mode": mode, "category": category,
            "date": datetime.now().strftime("%Y-%m-%d"), "shares": shares
        })
        save_db(db)
        flash("Expense added! ✅", "success")
    else:
        flash("Shares didn't match the participants. Try again.", "error")
    return redirect(url_for("dashboard"))

@app.route("/record_payment", methods=["POST"])
@login_required
def record_payment():
    db = load_db()
    user = db["users"][session["username"]]
    payer = request.form.get("payer")
    receiver = request.form.get("receiver")
    try:
        amount = round(float(request.form.get("amount", 0)), 2)
    except ValueError:
        amount = 0
    if (payer and receiver and payer != receiver and amount > 0
            and payer in user["students"] and receiver in user["students"]):
        user["expenses"].append({
            "description": f"Payment: {payer} → {receiver}",
            "amount": amount, "payer": payer, "mode": "payment",
            "category": "Payment",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "shares": {receiver: amount}
        })
        save_db(db)
        flash("Payment recorded! 💵", "success")
    else:
        flash("Invalid payment — check the names and amount.", "error")
    return redirect(url_for("dashboard"))

@app.route("/delete_expense/<int:idx>", methods=["POST"])
@login_required
def delete_expense(idx):
    db = load_db()
    user = db["users"][session["username"]]
    if 0 <= idx < len(user["expenses"]):
        user["expenses"].pop(idx)
        save_db(db)
        flash("Entry deleted.", "success")
    return redirect(url_for("dashboard"))

@app.route("/reset", methods=["POST"])
@login_required
def reset():
    db = load_db()
    user = db["users"][session["username"]]
    user["students"] = []
    user["expenses"] = []
    save_db(db)
    flash("All your data has been cleared.", "success")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True)