# S Campus Care

S Campus Care is a small Flask application for reporting campus facility issues. Students submit a description and track its status. An administrator reviews all reports and moves them through Open, In Progress, and Resolved. A local TF–IDF plus Multinomial Naive Bayes classifier suggests a category and priority.

## Requirements

- Windows, macOS, or Linux
- Python 3.12
- Internet access for the first dependency installation
- No separate database server or API key; SQLite and the classifier run locally

The ZIP's bundled `.venv` was created on another machine with Python 3.15.2 and contains an absolute interpreter path that is unavailable here. Create a new environment instead of trying to reuse that folder.

SciPy 1.15.3 is pinned because the newer SciPy wheel initially selected on this Windows machine was blocked by its Application Control policy when importing scikit-learn. The pinned version was installed and verified with the project tests.

## Setup (PowerShell)

From the directory containing `app.py`:

```powershell
py -3.12 --version
py -3.12 -m venv .venv312
.\.venv312\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
$env:CAMPUSCARE_SECRET_KEY = [guid]::NewGuid().ToString('N')
python app.py
```

Open <http://127.0.0.1:5001/>. On first startup, the app creates `campuscare.db` tables and the local demonstration accounts. It migrates an older `issues` table by adding a timestamp column while preserving existing reports. SQLite stores the database next to `app.py` unless `CAMPUSCARE_DATABASE` points to another path.

For repeat visits in the same PowerShell session, reuse the same `CAMPUSCARE_SECRET_KEY`; otherwise existing browser sessions are invalidated. Set `FLASK_DEBUG=1` only for local debugging. The app defaults to debug off.

## Verify

```powershell
python -m unittest discover -s tests -v
```

The tests use temporary SQLite databases. To check manually, sign in as a student, submit a report, and confirm it appears with a category, priority, status, and date. Sign out, sign in as admin, update the status, then sign in as the student again to confirm the update.

## Configuration and limits

| Variable | Purpose | Default |
| --- | --- | --- |
| `CAMPUSCARE_SECRET_KEY` | Flask session signing key | Random value per process (sessions reset after restart) |
| `CAMPUSCARE_DATABASE` | SQLite file path | `campuscare.db` beside `app.py` |
| `FLASK_DEBUG` | Enable Flask debug mode when set to `1` | Off |

The built-in accounts are for local demonstration only. Change them before any shared deployment. The app has no separate REST API, external integrations, notification service, or production identity provider. Do not expose this development server to the public internet.

## Project files

- `app.py` — routes, SQLite initialization, authentication, and classifier
- `templates/` — login and role-specific dashboards
- `tests/test_app.py` — route, persistence, validation, and migration checks
- `docs/PROJECT_AUDIT.md` — evidence-based reconnaissance and issue register
- `docs/ACADEMIC_REPORT.md` — project report, diagrams, and viva preparation
