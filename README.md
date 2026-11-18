# EduAttend Pro – Flask starter

This is a separate starter package based on the supplied dashboard screenshot. It does **not** overwrite or automatically merge into your existing college application. Share your existing project ZIP/source tree to merge safely.

## 1. Requirements
- Python 3.10+
- MySQL 8 (optional initially; SQLite is configured for a safe first run)
- VS Code

## 2. Run on macOS
```bash
cd EduAttendPro
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app app init-db
python app.py
```
Open http://127.0.0.1:5000

Demo login: `student@example.com` / `Student@123`

## 3. Configure MySQL
Create a database and user, then set `DATABASE_URL` in `.env`, e.g.
`mysql+pymysql://eduattend_user:YOUR_PASSWORD@localhost/eduattend?charset=utf8mb4`
Then run:
```bash
flask --app app init-db
```
Never commit `.env` or share provider credentials.

## 4. Messaging setup
`notifications.py` contains optional Twilio SMS/WhatsApp and SMTP email adapters. Add real credentials to `.env` only after creating and verifying your provider accounts. WhatsApp business-initiated messages generally require approved templates and user opt-in; Indian SMS delivery may require DLT-compliant headers/templates and an approved route. Provider charges apply.
The adapters are not automatically triggered by attendance events yet. We will connect them to your actual attendance/leave workflows after inspecting your existing code, adding consent/preferences, a durable queue, retry rules and delivery webhooks. Do not send real student messages from test data.

## 5. Current scope
- Screenshot-inspired student dashboard and responsive sidebar
- Demo login, attendance display, leave request form, profile and notification history
- SQLAlchemy models for users, attendance, leave requests and notification logs
- Optional messaging adapter functions

## 6. Before production
Add CSRF protection, role-based admin/faculty routes, password reset, migrations, audit logging, background worker, provider webhooks, consent/opt-out handling, rate limits, backups and HTTPS. Demo credentials must be changed.
