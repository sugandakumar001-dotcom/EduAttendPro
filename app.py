import os
from datetime import date, datetime, timedelta
import secrets
import io
import base64
import qrcode
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from notifications import notify_student, send_email

load_dotenv()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret")
db_url = os.getenv("DATABASE_URL", "sqlite:///eduattend.db")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
def generate_qr_code(data):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    qr.add_data(data)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return f"data:image/png;base64,{encoded}"


def qr_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"])
def generate_student_qr_token(student_id):
    serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"],
        salt="student-id-qr"
    )
    return serializer.dumps({"student_id": student_id})

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
class QRSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    token = db.Column(db.String(100), unique=True, nullable=False)

    subject = db.Column(db.String(120), nullable=False)

    created_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    expires_at = db.Column(db.DateTime, nullable=False)

    active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
@app.route("/admin/create-faculty", methods=["POST"])
def create_faculty():
    user = current_user()

    if not user or user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("login"))

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")

    if not name or not email or not password:
        flash("Please fill all required fields.", "error")
        return redirect(url_for("admin_assignments"))

    if User.query.filter_by(email=email).first():
        flash("This email already exists.", "error")
        return redirect(url_for("admin_assignments"))

    faculty = User(
        name=name,
        email=email,
        phone=phone,
        role="faculty"
    )

    faculty.set_password(password)

    db.session.add(faculty)
    db.session.commit()

    flash("Faculty account created successfully.", "success")
    return redirect(url_for("admin_assignments"))

@app.context_processor
def inject_user(): return {"current_user": current_user()}

@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            session["user_id"] = user.id

            if user.role == "admin":
                return redirect(url_for("admin_assignments"))

            elif user.role == "faculty":
                return redirect(url_for("faculty_dashboard"))

            elif user.role == "advisor":
                return redirect(url_for("advisor_dashboard"))

            elif user.role == "hod":
                return redirect(url_for("hod_dashboard"))

            else:
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
    if not user:
        return redirect(url_for("login"))

    records = Attendance.query.filter_by(
        student_id=user.id
    ).order_by(Attendance.attendance_date.desc()).limit(5).all()

    all_records = Attendance.query.filter_by(student_id=user.id).all()

    total = sum(x.periods for x in all_records)
    present = sum(
        x.periods for x in all_records if x.status == "Present"
    )

    pct = round(present * 100 / total, 1) if total else 0

    leaves = LeaveRequest.query.filter_by(
        student_id=user.id
    ).order_by(LeaveRequest.id.desc()).all()

    subjects = {}
    for row in all_records:
        item = subjects.setdefault(row.subject, [0, 0])
        item[1] += row.periods
        if row.status == "Present":
            item[0] += row.periods

    subject_stats = [
        {
            "name": k,
            "present": v[0],
            "total": v[1],
            "pct": round(v[0] * 100 / v[1], 1) if v[1] else 0
        }
        for k, v in subjects.items()
    ]

    # Calculate the latest leave attendance impact
    leave_impact = None

    if user.role == "student" and leaves:
        latest_leave = leaves[0]

        working_days = sum(
    1
    for day in range(
        (latest_leave.end_date - latest_leave.start_date).days + 1
    )
    if date.fromordinal(
        latest_leave.start_date.toordinal() + day
    ).weekday() < 5
)

        missed_periods = working_days

        projected_pct = round(
            present * 100 / (total + missed_periods), 1
        ) if total + missed_periods else 0

        leave_impact = {
            "subject": latest_leave.subject,
            "current_pct": pct,
            "projected_pct": projected_pct,
            "decrease": round(pct - projected_pct, 1),
            "missed_periods": missed_periods
        }

    return render_template(
        "dashboard.html",
        user=user,
        records=records,
        pct=pct,
        present=present,
        total=total,
        leaves=leaves,
        subject_stats=subject_stats,
        leave_impact=leave_impact
    )


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

        # Create leave request
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

        # Calculate current attendance
        all_records = Attendance.query.filter_by(
            student_id=user.id
        ).all()

        total = sum(record.periods for record in all_records)

        present = sum(
            record.periods
            for record in all_records
            if record.status == "Present"
        )

        current_pct = round(
            present * 100 / total, 1
        ) if total else 0

        # Calculate working days
        working_days = sum(
            1
            for day in range(
                (item.end_date - item.start_date).days + 1
            )
            if date.fromordinal(
                item.start_date.toordinal() + day
            ).weekday() < 5
        )

        missed_periods = working_days

        # Calculate projected attendance
        projected_pct = round(
            present * 100 / (total + missed_periods), 1
        ) if total + missed_periods else 0

        decrease = round(current_pct - projected_pct, 1)

        # Prepare Gmail message
        email_body = f"""
Dear {user.name},

Your leave request has been submitted successfully.

LEAVE DETAILS
Subject: {item.subject}
Start Date: {item.start_date}
End Date: {item.end_date}
Expected Missed Periods: {missed_periods}

ATTENDANCE IMPACT
Current Attendance: {current_pct}%
Projected Attendance: {projected_pct}%
Expected Attendance Decrease: {decrease}%

This is an estimate based on Monday-Friday working days
and one period per working day.

Your actual attendance will not change automatically.

Regards,
EduAttendPro Team
"""

        
        # Create designed HTML attendance alert
        status_message = (
            "Attendance is still above the 75% requirement."
            if projected_pct >= 75
            else "Warning: Your projected attendance is below 75%."
        )

        status_color = "#087a35" if projected_pct >= 75 else "#dc2626"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <body style="margin:0;padding:20px;background:#f5f5f5;
                     font-family:Arial,sans-serif;">
          <div style="max-width:700px;margin:auto;background:#ffffff;
                      border:1px solid #dddddd;border-radius:18px;
                      padding:26px;">

            <div style="display:inline-block;background:#ffe5dc;
                        color:#9a3e13;padding:9px 15px;
                        border-radius:25px;">
              Attendance alert
            </div>

            <h1 style="font-size:46px;color:#a04416;margin:22px 0;">
              {projected_pct}%
            </h1>

            <h2 style="font-size:25px;color:#111111;">
              Your projected attendance after planned leave
            </h2>

            <p style="font-size:20px;">
              Current attendance: {current_pct}%
            </p>

            <p style="font-size:20px;">
              Planned leave: {missed_periods} days
            </p>

            <p style="font-size:19px;color:{status_color};">
              {status_message}
            </p>

            <hr style="border:0;border-top:1px solid #eeeeee;">

            <p style="color:#666666;font-size:14px;">
              Subject: {item.subject}<br>
              Leave dates: {item.start_date} to {item.end_date}
            </p>

            <p style="color:#777777;font-size:12px;">
              This is an estimate based on Monday-Friday working
              days and one period per working day. Actual attendance
              will not change automatically.
            </p>

            <p style="color:#555555;">EduAttendPro Team</p>
          </div>
        </body>
        </html>
        """

        # Send Gmail notification
        try:
            send_email(
                user.email,
                "EduAttendPro - Attendance Alert",
                email_body,
                html_body=html_body
            )
            email_status = "sent"

        except Exception as exc:
            email_status = "failed: " + str(exc)
            print("Leave impact email failed:", exc)


        # Save email notification status
        email_log = NotificationLog(
            student_id=user.id,
            channel="email",
            message=email_body,
            status=email_status[:30]
        )

        db.session.add(email_log)
        db.session.commit()

        flash(
            "Leave request submitted. Email status: " + email_status,
            "success"
        )

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

    student = db.session.get(User, leave.student_id)

    if not student:
        flash("Student account not found.", "error")
        return redirect(url_for("hod_dashboard"))

    # Calculate current attendance
    all_records = Attendance.query.filter_by(
        student_id=student.id
    ).all()

    total = sum(record.periods for record in all_records)

    present = sum(
        record.periods
        for record in all_records
        if record.status == "Present"
    )

    current_pct = round(
        present * 100 / total, 1
    ) if total else 0

    # Estimate missed working days
    missed_periods = sum(
        1
        for day in range(
            (leave.end_date - leave.start_date).days + 1
        )
        if date.fromordinal(
            leave.start_date.toordinal() + day
        ).weekday() < 5
    )

    projected_pct = round(
        present * 100 / (total + missed_periods), 1
    ) if total + missed_periods else 0

    decrease = round(current_pct - projected_pct, 1)

    # Prepare notification
    message = f"""
Dear {student.name},

Your leave request has been {leave.status} by the HOD.

LEAVE DETAILS
Subject: {leave.subject}
Start Date: {leave.start_date}
End Date: {leave.end_date}

ATTENDANCE IMPACT
Current Attendance: {current_pct}%
Projected Attendance: {projected_pct}%
Expected Attendance Decrease: {decrease}%
Expected Missed Periods: {missed_periods}

This is an estimate based on Monday-Friday working days
and one period per working day.

Regards,
EduAttendPro Team
"""

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

    # Send notifications
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

    # Save delivery results
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
@app.route("/student/my-qr")
def student_my_qr():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "student":
        flash("Only students can access their personal QR code.", "error")
        return redirect(url_for("dashboard"))

    token = generate_student_qr_token(user.id)
    qr_image = generate_qr_code(token)

    return render_template(
        "student_qr.html",
        user=user,
        qr_image=qr_image
    )
@app.route("/faculty/dashboard")
def faculty_dashboard():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "faculty":
        flash("Faculty access required.", "error")
        return redirect(url_for("dashboard"))

    return render_template("faculty_dashboard.html", user=user)

@app.route("/faculty/generate-qr", methods=["POST"])
def faculty_generate_qr():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "faculty":
        flash("Faculty access required.", "error")
        return redirect(url_for("dashboard"))

    subject = request.form.get("subject", "").strip()

    if not subject:
        flash("Please enter a subject.", "error")
        return redirect(url_for("faculty_dashboard"))

    # Create a unique QR attendance session
    token = secrets.token_urlsafe(32)

    qr_session = QRSession(
        token=token,
        subject=subject,
        created_by=user.id,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        active=True
    )

    db.session.add(qr_session)
    db.session.commit()

    # Generate the student attendance URL
    attendance_url = url_for(
        "student_scan_attendance",
        token=token,
        _external=True
    )

    qr_image = generate_qr_code(attendance_url)

    return render_template(
        "faculty_qr.html",
        qr_image=qr_image,
        subject=subject,
        expires_at=qr_session.expires_at
    )

@app.route("/student/scan-attendance/<token>")
def student_scan_attendance(token):
    user = current_user()

    if not user:
        flash("Please log in before scanning attendance QR.", "error")
        return redirect(url_for("login"))

    if user.role != "student":
        flash("Only students can mark attendance.", "error")
        return redirect(url_for("dashboard"))

    qr_session = QRSession.query.filter_by(
        token=token,
        active=True
    ).first()

    if not qr_session:
        flash("Invalid or inactive QR code.", "error")
        return redirect(url_for("dashboard"))

    if datetime.utcnow() > qr_session.expires_at:
        qr_session.active = False
        db.session.commit()
        flash("This attendance QR code has expired.", "error")
        return redirect(url_for("dashboard"))

    # Prevent duplicate attendance
    existing = Attendance.query.filter_by(
        student_id=user.id,
        subject=qr_session.subject,
        attendance_date=date.today()
    ).first()

    if existing:
        flash("Attendance already recorded for this subject today.", "error")
        return redirect(url_for("attendance"))

    record = Attendance(
        student_id=user.id,
        subject=qr_session.subject,
        attendance_date=date.today(),
        status="Present",
        periods=1
    )

    db.session.add(record)
    db.session.commit()

    flash(
        f"Attendance marked successfully for {qr_session.subject}!",
        "success"
    )

    return redirect(url_for("attendance"))
@app.route("/faculty/qr-scanner")
def faculty_qr_scanner():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user.role != "faculty":
        flash("Faculty access required.", "error")
        return redirect(url_for("dashboard"))

    return render_template("faculty_qr_scanner.html")

@app.route("/faculty/scan-student-qr", methods=["POST"])
def faculty_scan_student_qr():
    faculty = current_user()

    if not faculty:
        return jsonify({"success": False, "message": "Please log in."}), 401

    if faculty.role != "faculty":
        return jsonify({"success": False, "message": "Faculty access required."}), 403

    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    subject = data.get("subject", "").strip()

    if not token or not subject:
        return jsonify({
            "success": False,
            "message": "Student QR and subject are required."
        }), 400

    try:
        serializer = URLSafeTimedSerializer(
            app.config["SECRET_KEY"],
            salt="student-id-qr"
        )
        payload = serializer.loads(token)
        student_id = payload.get("student_id")
    except (BadSignature, SignatureExpired):
        return jsonify({
            "success": False,
            "message": "Invalid student QR code."
        }), 400

    student = db.session.get(User, student_id)

    if not student or student.role != "student":
        return jsonify({
            "success": False,
            "message": "Student account not found."
        }), 404

    existing = Attendance.query.filter_by(
        student_id=student.id,
        subject=subject,
        attendance_date=date.today()
    ).first()

    if existing:
        return jsonify({
            "success": False,
            "message": f"Attendance already recorded for {student.name}."
        }), 409

    record = Attendance(
        student_id=student.id,
        subject=subject,
        attendance_date=date.today(),
        status="Present",
        periods=1
    )

    db.session.add(record)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Attendance marked for {student.name} – {subject}."
    }), 200

if __name__ == "__main__":
    with app.app_context(): db.create_all()
    app.run(host="0.0.0.0", port=5001, debug=True)
