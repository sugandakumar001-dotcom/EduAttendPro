import os
from datetime import date, datetime
from flask import Flask, render_template, redirect, url_for, request, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from notifications import notify_student
from notifications import notify_student

load_dotenv()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret")
db_url = os.getenv("DATABASE_URL", "sqlite:///eduattend.db")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="student")
        # Class and approval assignments
    class_name = db.Column(db.String(100))

    # For students: assigned Class Advisor
    # For advisors: assigned HOD
    advisor_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id")
    )

    hod_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id")
    )
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    attendance_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(20), nullable=False)
    periods = db.Column(db.Integer, default=1)

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    subject = db.Column(db.String(120), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text)

    # Overall leave status
    status = db.Column(
        db.String(30),
        default="Pending Advisor"
    )

    # Class Advisor approval
    advisor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    advisor_status = db.Column(
        db.String(20),
        default="Pending"
    )
    advisor_remarks = db.Column(db.Text)
    advisor_reviewed_at = db.Column(db.DateTime)

    # HOD approval
    hod_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    hod_status = db.Column(
        db.String(20),
        default="Waiting"
    )
    hod_remarks = db.Column(db.Text)
    hod_reviewed_at = db.Column(db.DateTime)

class NotificationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    channel = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default="queued")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

@app.context_processor
def inject_user(): return {"current_user": current_user()}

@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if user and user.check_password(request.form["password"]):
            session["user_id"] = user.id
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user: return redirect(url_for("login"))
    records = Attendance.query.filter_by(student_id=user.id).order_by(Attendance.attendance_date.desc()).limit(5).all()
    all_records = Attendance.query.filter_by(student_id=user.id).all()
    total = sum(x.periods for x in all_records)
    present = sum(x.periods for x in all_records if x.status == "Present")
    pct = round(present * 100 / total, 1) if total else 0
    leaves = LeaveRequest.query.filter_by(student_id=user.id).order_by(LeaveRequest.id.desc()).all()
    subjects = {}
    for row in all_records:
        item = subjects.setdefault(row.subject, [0, 0])
        item[1] += row.periods
        if row.status == "Present": item[0] += row.periods
    subject_stats = [{"name": k, "present": v[0], "total": v[1], "pct": round(v[0]*100/v[1],1) if v[1] else 0} for k,v in subjects.items()]
    return render_template("dashboard.html", user=user, records=records, pct=pct, present=present,
                           total=total, leaves=leaves, subject_stats=subject_stats)

@app.route("/attendance")
def attendance():
    user=current_user()
    if not user: return redirect(url_for("login"))
    rows=Attendance.query.filter_by(student_id=user.id).order_by(Attendance.attendance_date.desc()).all()
    return render_template("simple.html", title="My Attendance", rows=rows)


@app.route("/leaves", methods=["GET", "POST"])
def leaves():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":

        if user.role != "student":
            flash("Only students can submit leave requests.", "error")
            return redirect(url_for("leaves"))

        # Find assigned Class Advisor
        advisor = db.session.get(User, user.advisor_id)

        if not advisor or advisor.role != "advisor":
            flash("No Class Advisor assigned. Contact administrator.", "error")
            return redirect(url_for("leaves"))

        # Find assigned HOD
        hod = db.session.get(User, advisor.hod_id)

        if not hod or hod.role != "hod":
            flash("No HOD assigned to your Class Advisor.", "error")
            return redirect(url_for("leaves"))

        item = LeaveRequest(
            student_id=user.id,
            subject=request.form["subject"],
            start_date=date.fromisoformat(request.form["start_date"]),
            end_date=date.fromisoformat(request.form["end_date"]),
            reason=request.form.get("reason", ""),
            advisor_id=advisor.id,
            hod_id=hod.id,
            advisor_status="Pending",
            hod_status="Waiting",
            status="Pending Advisor"
        )

        db.session.add(item)
        db.session.commit()

        flash("Leave request submitted to your Class Advisor!", "success")
        return redirect(url_for("leaves"))

    # Display student's leave requests
    rows = LeaveRequest.query.filter_by(
        student_id=user.id
    ).order_by(LeaveRequest.id.desc()).all()

    return render_template("leaves.html", rows=rows)


@app.route("/profile")
def profile():
    user=current_user()
    if not user: return redirect(url_for("login"))
    return render_template("profile.html", user=user)

@app.route("/notifications")
def notifications():
    user=current_user()
    if not user: return redirect(url_for("login"))
    rows=NotificationLog.query.filter_by(student_id=user.id).order_by(NotificationLog.created_at.desc()).limit(100).all()
    return render_template("notifications.html", rows=rows)
# ==========================================
# CLASS ADVISOR DASHBOARD
# ==========================================
# ==========================================
# ADMIN ASSIGNMENT DASHBOARD
# ==========================================

@app.route("/admin/assignments")
def admin_assignments():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("dashboard"))

    students = User.query.filter_by(role="student").order_by(User.name).all()
    advisors = User.query.filter_by(role="advisor").order_by(User.name).all()
    hods = User.query.filter_by(role="hod").order_by(User.name).all()

    return render_template(
        "admin_assignments.html",
        students=students,
        advisors=advisors,
        hods=hods
    )


# ==========================================
# ASSIGN HOD TO ADVISOR
# ==========================================

@app.route("/admin/assign-hod", methods=["POST"])
def assign_hod():
    user = current_user()

    if not user or user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("login"))

    advisor = db.session.get(User, request.form.get("advisor_id", type=int))
    hod = db.session.get(User, request.form.get("hod_id", type=int))

    if not advisor or advisor.role != "advisor":
        flash("Invalid Advisor selected.", "error")
        return redirect(url_for("admin_assignments"))

    if not hod or hod.role != "hod":
        flash("Invalid HOD selected.", "error")
        return redirect(url_for("admin_assignments"))

    advisor.hod_id = hod.id
    db.session.commit()

    flash("HOD successfully assigned to Advisor.", "success")
    return redirect(url_for("admin_assignments"))


# ==========================================
# ASSIGN ADVISOR TO STUDENT
# ==========================================

@app.route("/admin/assign-advisor", methods=["POST"])
def assign_advisor():
    user = current_user()

    if not user or user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("login"))

    student = db.session.get(User, request.form.get("student_id", type=int))
    advisor = db.session.get(User, request.form.get("advisor_id", type=int))
    class_name = request.form.get("class_name", "").strip()

    if not student or student.role != "student":
        flash("Invalid student selected.", "error")
        return redirect(url_for("admin_assignments"))

    if not advisor or advisor.role != "advisor":
        flash("Invalid Advisor selected.", "error")
        return redirect(url_for("admin_assignments"))

    if not class_name:
        flash("Please enter a class name.", "error")
        return redirect(url_for("admin_assignments"))

    if not advisor.hod_id:
        flash("Assign an HOD to this Advisor first.", "error")
        return redirect(url_for("admin_assignments"))

    student.advisor_id = advisor.id
    student.class_name = class_name

    db.session.commit()

    flash("Class Advisor successfully assigned to student.", "success")
    return redirect(url_for("admin_assignments"))
@app.route("/advisor/dashboard")
def advisor_dashboard():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "advisor":
        flash("Access denied. Advisor account required.", "error")
        return redirect(url_for("dashboard"))

    pending = LeaveRequest.query.filter_by(
        advisor_id=user.id,
        status="Pending Advisor"
    ).order_by(LeaveRequest.id.desc()).all()

    reviewed = LeaveRequest.query.filter(
        LeaveRequest.advisor_id == user.id,
        LeaveRequest.status.in_(["Pending HOD", "Approved", "Rejected"])
    ).order_by(LeaveRequest.id.desc()).all()

    return render_template(
        "advisor_dashboard.html",
        pending=pending,
        reviewed=reviewed
    )


# ==========================================
# ADVISOR APPROVE / REJECT
# ==========================================

@app.route("/advisor/review/<int:leave_id>", methods=["POST"])
def advisor_review(leave_id):
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "advisor":
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    leave = LeaveRequest.query.filter_by(
        id=leave_id,
        advisor_id=user.id,
        status="Pending Advisor"
    ).first_or_404()

    action = request.form.get("action")
    remarks = request.form.get("remarks", "").strip()

    if action == "approve":
        if not leave.hod_id:
            flash("This request has no assigned HOD. Contact the administrator.", "error")
            return redirect(url_for("advisor_dashboard"))

        leave.advisor_status = "Approved"
        leave.advisor_remarks = remarks
        leave.advisor_reviewed_at = datetime.utcnow()
        leave.status = "Pending HOD"

        flash("Leave approved and forwarded to HOD.", "success")

    elif action == "reject":
        if not remarks:
            flash("Please enter a rejection reason.", "error")
            return redirect(url_for("advisor_dashboard"))

        leave.advisor_status = "Rejected"
        leave.advisor_remarks = remarks
        leave.advisor_reviewed_at = datetime.utcnow()
        leave.status = "Rejected"

        flash("Leave request rejected.", "success")

    else:
        flash("Invalid action.", "error")
        return redirect(url_for("advisor_dashboard"))

    db.session.commit()

    return redirect(url_for("advisor_dashboard"))

# ==========================================
# HOD DASHBOARD
# ==========================================

@app.route("/hod/dashboard")
def hod_dashboard():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "hod":
        flash("HOD access required.", "error")
        return redirect(url_for("dashboard"))

    pending = LeaveRequest.query.filter_by(
        hod_id=user.id,
        status="Pending HOD",
        advisor_status="Approved"
    ).order_by(LeaveRequest.id.desc()).all()

    reviewed = LeaveRequest.query.filter(
        LeaveRequest.hod_id == user.id,
        LeaveRequest.status.in_(["Approved", "Rejected"])
    ).order_by(LeaveRequest.id.desc()).all()

    return render_template(
        "hod_dashboard.html",
        pending=pending,
        reviewed=reviewed,
        students=User.query
    )


# ==========================================
# HOD FINAL APPROVAL
# ==========================================


@app.route("/hod/review/<int:leave_id>", methods=["POST"])
def hod_review(leave_id):
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "hod":
        flash("HOD access required.", "error")
        return redirect(url_for("dashboard"))

    leave = LeaveRequest.query.filter_by(
        id=leave_id,
        hod_id=user.id,
        status="Pending HOD",
        advisor_status="Approved"
    ).first_or_404()

    action = request.form.get("action")
    remarks = request.form.get("remarks", "").strip()

    if action == "approve":
        leave.hod_status = "Approved"
        leave.status = "Approved"

    elif action == "reject":
        if not remarks:
            flash("Please enter a rejection reason.", "error")
            return redirect(url_for("hod_dashboard"))

        leave.hod_status = "Rejected"
        leave.status = "Rejected"

    else:
        flash("Invalid action.", "error")
        return redirect(url_for("hod_dashboard"))

    leave.hod_id = user.id
    leave.hod_remarks = remarks
    leave.hod_reviewed_at = datetime.utcnow()

    # Get student details
    student = db.session.get(User, leave.student_id)

    if not student:
        flash("Student account not found.", "error")
        return redirect(url_for("hod_dashboard"))

    # Prepare notification message
    message = (
        f"Dear {student.name}, your leave request for "
        f"{leave.subject} ({leave.start_date} to {leave.end_date}) "
        f"has been {leave.status} by the HOD."
    )

    if remarks:
        message += f"\nHOD Remarks: {remarks}"

    # Save in-app notification
    notification = NotificationLog(
        student_id=student.id,
        channel="in-app",
        message=message,
        status="sent"
    )

    db.session.add(notification)
    db.session.commit()

    # Send email, SMS and WhatsApp
    try:
        results = notify_student(
            student,
            "EduAttend Pro - Leave Request Update",
            message
        )
    except Exception as exc:
        results = {
            "email": "failed: " + str(exc),
            "sms": "failed: " + str(exc),
            "whatsapp": "failed: " + str(exc)
        }

    # Save individual delivery results
    for channel, result in results.items():
        log = NotificationLog(
            student_id=student.id,
            channel=channel,
            message=message,
            status=str(result)[:30]
        )
        db.session.add(log)

    db.session.commit()

    flash(
        f"Final HOD decision submitted. Notification results: {results}",
        "success"
    )

    return redirect(url_for("hod_dashboard"))



@app.cli.command("init-db")
def init_db():
    db.create_all()
    if not User.query.filter_by(email="student@example.com").first():
        u=User(name="Jordan", email="student@example.com", phone="+919876543210", role="student")
        u.set_password("Student@123")
        db.session.add(u); db.session.commit()
        samples=[
          ("Mathematics","Present",2),("Physics","Present",1),("Chemistry","Absent",2),("English","Present",1),
          ("Mathematics","Present",2),("Physics","Present",1),("Chemistry","Present",2)]
        for i,(sub,status,periods) in enumerate(samples):
            db.session.add(Attendance(student_id=u.id,subject=sub,status=status,periods=periods))
        db.session.add(LeaveRequest(student_id=u.id,subject="Mathematics",start_date=date(2026,11,20),end_date=date(2026,11,21),reason="Personal work"))
        db.session.commit()
    if not User.query.filter_by(email="admin@example.com").first():
        admin = User(
            name="Admin",
            email="admin@example.com",
            phone="+919876543210",
            role="admin"
        )
        admin.set_password("Admin@123")
        db.session.add(admin)
        db.session.commit()
print("Database initialized. Demo: student@example.com / Student@123")

if __name__ == "__main__":
    with app.app_context(): db.create_all()
    app.run(debug=True)
