import os
import re
import secrets
import sqlite3
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash, generate_password_hash
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB

app = Flask(__name__)
app.secret_key = os.environ.get('CAMPUSCARE_SECRET_KEY') or secrets.token_hex(32)
app.config['DATABASE'] = os.environ.get(
    'CAMPUSCARE_DATABASE', str(Path(__file__).with_name('campuscare.db'))
)


def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

# --- Priority model training (TF-IDF + Multinomial Naive Bayes) ---
TRAIN_DATA = [
    ("wifi not working in computer lab", "Medium"),
    ("internet slow in library", "Medium"),
    ("projector broken in classroom", "Medium"),
    ("network down and lan cable cut in lab", "High"),
    ("short circuit spark in switchboard", "High"),
    ("water leak from ceiling pipe", "High"),
    ("exposed live wire near corridor", "High"),
    ("fan making noise and light flickering", "Low"),
    ("washroom dirty and overflowing", "High"),
    ("garbage bin full near canteen", "Medium"),
    ("dusty floor and benches in room", "Low"),
    ("door lock jammed in classroom", "Medium"),
    ("broken window glass in corridor", "High"),
    ("bench wooden plank loose", "Low"),
]

vectorizer = TfidfVectorizer()
X_train = vectorizer.fit_transform([x[0] for x in TRAIN_DATA])

prio_model = MultinomialNB().fit(X_train, [x[1] for x in TRAIN_DATA])

# Strong equipment and service terms are more reliable than the tiny training
# set when a report names the affected item (for example, a fan in a lab).
CATEGORY_RULES = (
    ('Electrical & Maintenance', re.compile(
        r'\b(?:fan|fans|light|lights|bulb|bulbs|tube light|switch|switchboard|'
        r'socket|plug|wiring|wire|electricity|electrical|power supply|power cut|'
        r'short circuit|circuit breaker|fuse|voltage|generator|inverter|'
        r'air conditioner|air conditioning|heater|heating|boiler|plumbing|'
        r'pipe|tap|faucet|leak|leaking|ac)\b', re.IGNORECASE)),
    ('IT & Lab Infrastructure', re.compile(
        r'\b(?:wi[ -]?fi|internet|network|lan|computer|laptop|desktop|'
        r'projector|printer|server|software|login|keyboard|mouse|router|'
        r'modem|scanner|monitor|access point|smart board)\b', re.IGNORECASE)),
    ('Sanitation & Housekeeping', re.compile(
        r'\b(?:washroom|restroom|toilet|bathroom|garbage|trash|rubbish|'
        r'dust|dirty|cleaning|housekeeping|overflowing|drain|sewage|'
        r'bin|litter|odor|odour|smell|pest|rodent|insect|hygiene)\b', re.IGNORECASE)),
    ('General Campus Facility', re.compile(
        r'\b(?:door|window|glass|bench|desk|chair|table|furniture|ceiling|'
        r'floor|wall|roof|staircase|stairs|lift|elevator|classroom|room|'
        r'corridor|hallway|gate|parking|road|playground|building|lock)\b',
        re.IGNORECASE)),
)

NEGATIVE_FEEDBACK_PATTERN = re.compile(
    r'\b(?:not|never|no longer|stopped|quit|isn.t|aren.t|wasn.t|weren.t|'
    r'doesn.t|don.t|didn.t|cannot|can.t|unable to)\s+(?:(?:\w+)\s+){0,2}'
    r'(?:work(?:ing|s)?|function(?:ing|s)?|operat(?:e|ing)|run(?:ning)?)\b'
    r'|\b(?:not|isn.t|aren.t|wasn.t|weren.t|doesn.t|don.t|didn.t)\s+'
    r'(?:fine|good|great|okay|ok|functional|operational|clean|satisfactory)\b'
    r'|\b(?:broken|damaged|faulty|malfunction\w*|failed|failure|'
    r'leak(?:ing)?|dirty|slow|bad(?:ly)?|poor|unsatisfactory|defective|'
    r'intermittent\w*|irregular\w*|occasionally|sometimes|noisy|'
    r'flicker\w*|overflow\w*|clogged|jammed|stuck|cracked|unusable|'
    r'unavailable|spark|smoke|short circuit)\b',
    re.IGNORECASE,
)
POSITIVE_FEEDBACK_PATTERN = re.compile(
    r'\b(?:working(?:\s+(?:fine|properly|normally|well|perfectly))?|'
    r'functioning(?:\s+(?:properly|normally|well))?|'
    r'works?(?:\s+(?:fine|properly|normally|well|perfectly))?|'
    r'(?:is|are|seems?|looks?)\s+(?:fine|good|great|okay|ok|functional|'
    r'operational)|in good condition|all good|everything(?:\s+is)?\s+'
    r'(?:fine|good|great|okay|ok|working)|no\s+(?:issues?|problems?|'
    r'complaints?|faults?))\b',
    re.IGNORECASE,
)


def is_positive_feedback(text):
    """Keep positive status notes out of the actionable issue queue."""
    if NEGATIVE_FEEDBACK_PATTERN.search(text):
        return False
    return bool(POSITIVE_FEEDBACK_PATTERN.search(text))


def analyze_issue_ai(text):
    X_test = vectorizer.transform([text])
    category = next(
        (label for label, pattern in CATEGORY_RULES if pattern.search(text)),
        'General Campus Facility',
    )
    priority = prio_model.predict(X_test)[0]
    return category, priority
# ------------------------------------------------------------


def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student TEXT,
                    description TEXT,
                    category TEXT,
                    priority TEXT,
                    status TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student TEXT,
                    description TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    columns = {row[1] for row in c.execute('PRAGMA table_info(issues)')}
    if 'created_at' not in columns:
        c.execute('ALTER TABLE issues ADD COLUMN created_at TEXT')
        c.execute('UPDATE issues SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL')
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
              ('admin', generate_password_hash('admin123'), 'admin'))
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
              ('student', generate_password_hash('stud123'), 'student'))
    conn.commit()
    conn.close()


def get_stats(issues):
    return {
        'total': len(issues),
        'open': sum(1 for i in issues if i[5] == 'Open'),
        'progress': sum(1 for i in issues if i[5] == 'In Progress'),
        'resolved': sum(1 for i in issues if i[5] == 'Resolved')
    }


@app.route('/', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        role = request.form['role']
        uname = request.form['username']
        pwd = request.form['password']

        conn = get_db_connection()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE username=? AND role=?",
                (uname, role)
            ).fetchone()
            if user and (check_password_hash(user['password'], pwd)
                         if user['password'].startswith(('scrypt:', 'pbkdf2:'))
                         else user['password'] == pwd):
                # Upgrade accounts created by earlier versions without changing their login.
                if not user['password'].startswith(('scrypt:', 'pbkdf2:')):
                    conn.execute('UPDATE users SET password=? WHERE id=?',
                                 (generate_password_hash(pwd), user['id']))
                    conn.commit()
            else:
                user = None
        finally:
            conn.close()

        if user:
            session['username'] = user[1]
            session['role'] = user[3]
            return redirect(url_for('admin_panel' if role == 'admin' else 'student_panel'))
        else:
            error = f"Invalid credentials for {role.capitalize()} login!"
    return render_template('login.html', error=error)


@app.route('/student', methods=['GET', 'POST'])
def student_panel():
    if session.get('role') != 'student':
        return redirect(url_for('login'))

    conn = get_db_connection()
    error = None
    notice = None
    if request.method == 'POST':
        desc = request.form.get('description', '').strip()
        if not 5 <= len(desc) <= 2000:
            error = 'Description must be between 5 and 2000 characters.'
        else:
            if is_positive_feedback(desc):
                conn.execute(
                    "INSERT INTO feedback (student, description) VALUES (?, ?)",
                    (session['username'], desc)
                )
                notice = 'Thanks! Your feedback was saved separately from reported issues.'
            else:
                category, priority = analyze_issue_ai(desc)
                conn.execute(
                    "INSERT INTO issues (student, description, category, priority, status) VALUES (?, ?, ?, ?, ?)",
                    (session['username'], desc, category, priority, 'Open')
                )
                notice = 'Your campus issue was submitted.'
            conn.commit()

    issues = conn.execute(
        "SELECT * FROM issues WHERE student=? ORDER BY id DESC",
        (session['username'],)
    ).fetchall()
    feedback = conn.execute(
        "SELECT * FROM feedback WHERE student=? ORDER BY id DESC",
        (session['username'],)
    ).fetchall()
    conn.close()
    return render_template(
        'student.html', issues=issues, feedback=feedback,
        stats=get_stats(issues), user=session['username'], error=error,
        notice=notice,
    )


@app.route('/admin')
def admin_panel():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    issues = conn.execute("SELECT * FROM issues ORDER BY id DESC").fetchall()
    feedback = conn.execute("SELECT * FROM feedback ORDER BY id DESC").fetchall()
    conn.close()
    return render_template(
        'admin.html', issues=issues, feedback=feedback,
        stats=get_stats(issues), user=session['username'],
    )


@app.route('/update_issue/<int:issue_id>', methods=['POST'])
def update_issue(issue_id):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    new_status = request.form.get('status')
    if new_status not in {'Open', 'In Progress', 'Resolved'}:
        return redirect(url_for('admin_panel'))
    conn = get_db_connection()
    try:
        conn.execute("UPDATE issues SET status=? WHERE id=?", (new_status, issue_id))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('admin_panel'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    init_db()
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5001)
