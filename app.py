import os
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

# --- AI MODEL TRAINING (TF-IDF + Multinomial Naive Bayes) ---
TRAIN_DATA = [
    ("wifi not working in computer lab", "IT & Lab Infrastructure", "Medium"),
    ("internet slow in library", "IT & Lab Infrastructure", "Medium"),
    ("projector broken in classroom", "IT & Lab Infrastructure", "Medium"),
    ("network down and lan cable cut in lab", "IT & Lab Infrastructure", "High"),
    ("short circuit spark in switchboard", "Electrical & Maintenance", "High"),
    ("water leak from ceiling pipe", "Electrical & Maintenance", "High"),
    ("exposed live wire near corridor", "Electrical & Maintenance", "High"),
    ("fan making noise and light flickering", "Electrical & Maintenance", "Low"),
    ("washroom dirty and overflowing", "Sanitation & Housekeeping", "High"),
    ("garbage bin full near canteen", "Sanitation & Housekeeping", "Medium"),
    ("dusty floor and benches in room", "Sanitation & Housekeeping", "Low"),
    ("door lock jammed in classroom", "General Campus Facility", "Medium"),
    ("broken window glass in corridor", "General Campus Facility", "High"),
    ("bench wooden plank loose", "General Campus Facility", "Low"),
]

vectorizer = TfidfVectorizer()
X_train = vectorizer.fit_transform([x[0] for x in TRAIN_DATA])

cat_model = MultinomialNB().fit(X_train, [x[1] for x in TRAIN_DATA])
prio_model = MultinomialNB().fit(X_train, [x[2] for x in TRAIN_DATA])


def analyze_issue_ai(text):
    X_test = vectorizer.transform([text])
    category = cat_model.predict(X_test)[0]
    confidence = round(max(cat_model.predict_proba(X_test)[0]) * 100, 1)
    priority = prio_model.predict(X_test)[0]

    # Fallback if user types random junk/gibberish
    if confidence < 30.0:
        return "General Campus Facility (Low Conf.)", "Low"
    return f"{category} ({confidence}% AI)", priority
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
    if request.method == 'POST':
        desc = request.form.get('description', '').strip()
        if not 5 <= len(desc) <= 2000:
            error = 'Description must be between 5 and 2000 characters.'
        else:
            category, priority = analyze_issue_ai(desc)
            conn.execute(
                "INSERT INTO issues (student, description, category, priority, status) VALUES (?, ?, ?, ?, ?)",
                (session['username'], desc, category, priority, 'Open')
            )
            conn.commit()

    issues = conn.execute(
        "SELECT * FROM issues WHERE student=? ORDER BY id DESC",
        (session['username'],)
    ).fetchall()
    conn.close()
    return render_template('student.html', issues=issues, stats=get_stats(issues), user=session['username'], error=error)


@app.route('/admin')
def admin_panel():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    issues = conn.execute("SELECT * FROM issues ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('admin.html', issues=issues, stats=get_stats(issues), user=session['username'])


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
