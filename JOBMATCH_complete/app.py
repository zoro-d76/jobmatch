import os, re, sqlite3, smtplib, secrets
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "instance" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-secret-before-deployment"),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
DATABASE = BASE / "jobmatch.db"
ALLOWED_CV = {"pdf", "docx", "txt"}
STATUSES = {"Pending", "Reviewing", "Accepted", "Rejected"}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection:
        connection.close()

def init_db():
    con = sqlite3.connect(DATABASE)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('seeker','employer')),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS seekers (
      user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      phone TEXT NOT NULL DEFAULT '', skills TEXT NOT NULL DEFAULT '',
      experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),
      location TEXT NOT NULL DEFAULT '', expected_salary INTEGER NOT NULL DEFAULT 0 CHECK(expected_salary>=0),
      cv_filename TEXT NOT NULL DEFAULT '', cv_text TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, employer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title TEXT NOT NULL, description TEXT NOT NULL, skills TEXT NOT NULL,
      experience INTEGER NOT NULL CHECK(experience>=0), location TEXT NOT NULL,
      salary INTEGER NOT NULL CHECK(salary>=0), is_active INTEGER NOT NULL DEFAULT 1,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS applications (
      id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      seeker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      message TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Pending'
        CHECK(status IN ('Pending','Reviewing','Accepted','Rejected')),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(job_id,seeker_id)
    );
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
      sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title TEXT NOT NULL, body TEXT NOT NULL, link TEXT NOT NULL DEFAULT '',
      is_read INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS admins (
      id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Safe additive migrations for an earlier JOBMATCH database.
    columns = {r[1] for r in con.execute("PRAGMA table_info(seekers)")}
    for name, sql in [("cv_filename", "ALTER TABLE seekers ADD COLUMN cv_filename TEXT NOT NULL DEFAULT ''"),
                      ("cv_text", "ALTER TABLE seekers ADD COLUMN cv_text TEXT NOT NULL DEFAULT ''")]:
        if name not in columns:
            con.execute(sql)
    job_columns = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    if "is_active" not in job_columns:
        con.execute("ALTER TABLE jobs ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    con.commit()
    con.close()

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]

@app.context_processor
def inject_globals():
    return {"csrf_token": csrf_token, "unread_count": unread_count() if session.get("user_id") else 0}

@app.before_request
def protect_post_requests():
    if request.method == "POST":
        expected = session.get("_csrf", "")
        supplied = request.form.get("_csrf", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            abort(400, "Invalid or missing form security token. Refresh the page and try again.")

def unread_count():
    if not session.get("user_id"):
        return 0
    row = db().execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND is_read=0",
                       (session["user_id"],)).fetchone()
    return row["n"]

def login_required(role=None):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id") and session.get("role") != "admin":
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You do not have permission to access that page.", "warning")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return deco

def split_skills(raw):
    return [x.strip().lower() for x in re.split(r"[,;\n]", raw or "") if x.strip()]

def extract_cv_text(path):
    suffix = path.suffix.lower()
    try:
        if suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")[:50000]
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)[:50000]
        if suffix == ".docx":
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)[:50000]
    except Exception:
        return ""
    return ""

def score_match(seeker, job):
    # Transparent, local keyword scoring. It does not make hiring decisions.
    required = set(split_skills(job["skills"]))
    profile = set(split_skills(seeker["skills"]))
    cv_text = (seeker["cv_text"] or "").lower()
    cv_hits = {skill for skill in required if re.search(r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])", cv_text)}
    direct_hits = required & profile
    all_hits = direct_hits | cv_hits
    skill_score = (len(all_hits) / len(required) * 55) if required else 0
    exp_score = 20 if seeker["experience"] >= job["experience"] else (
        10 if seeker["experience"] == max(0, job["experience"] - 1) else 0)
    location_score = 15 if seeker["location"].strip().lower() == job["location"].strip().lower() else 0
    salary_score = 10 if seeker["expected_salary"] <= job["salary"] else 0
    score = min(100, round(skill_score + exp_score + location_score + salary_score))
    reasons = []
    if all_hits:
        reasons.append("Skills found: " + ", ".join(sorted(all_hits)))
    missing = required - all_hits
    if missing:
        reasons.append("Skills not found in your profile/CV: " + ", ".join(sorted(missing)))
    reasons.append("Experience requirement met." if exp_score == 20 else "Experience is below the listed requirement.")
    reasons.append("Location matches." if location_score else "Location differs from your preferred location.")
    reasons.append("Salary is within your expectation." if salary_score else "Offered salary is below your expectation.")
    return score, reasons

def add_notification(user_id, title, body, link=""):
    db().execute("INSERT INTO notifications(user_id,title,body,link) VALUES(?,?,?,?)",
                 (user_id, title, body, link))

def send_email(to_email, subject, body):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not all([host, user, password, to_email]):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", user)
    msg["To"] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception:
        app.logger.exception("Email notification failed")
        return False

def notify(user_id, email, title, body, link=""):
    add_notification(user_id, title, body, link)
    send_email(email, title, body)

def get_application_for_user(application_id):
    return db().execute("""
      SELECT a.*, j.title AS job_title, j.employer_id, seeker.name AS seeker_name,
             seeker.email AS seeker_email, employer.name AS employer_name, employer.email AS employer_email
      FROM applications a JOIN jobs j ON j.id=a.job_id
      JOIN users seeker ON seeker.id=a.seeker_id
      JOIN users employer ON employer.id=j.employer_id
      WHERE a.id=?""", (application_id,)).fetchone()

@app.route("/")
def index():
    rows = db().execute("""SELECT j.*,u.name AS company FROM jobs j JOIN users u ON u.id=j.employer_id
                           WHERE j.is_active=1 ORDER BY j.created_at DESC LIMIT 6""").fetchall()
    return render_template("index.html", jobs=rows)
@app.route('/about')
def about():
    return render_template('about.html')

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        password=request.form.get("password",""); role=request.form.get("role","")
        if not name or not email or len(password)<10 or role not in ("seeker","employer"):
            flash("Enter all details. Use a password with at least 10 characters.", "danger")
            return render_template("register.html")
        try:
            cur=db().execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                             (name,email,generate_password_hash(password),role))
            uid=cur.lastrowid
            if role=="seeker":
                phone=request.form.get("phone","").strip(); skills=request.form.get("skills","").strip()
                location=request.form.get("location","").strip()
                try:
                    exp=int(request.form.get("experience","0")); salary=int(request.form.get("salary","0"))
                    if exp<0 or salary<0 or not phone or not skills or not location: raise ValueError
                except ValueError:
                    db().rollback(); flash("Complete your job-seeker profile with valid values.", "danger")
                    return render_template("register.html")
                db().execute("""INSERT INTO seekers(user_id,phone,skills,experience,location,expected_salary)
                                VALUES(?,?,?,?,?,?)""",(uid,phone,skills,exp,location,salary))
            db().commit()
            session.clear(); session.update(user_id=uid,name=name,role=role)
            flash("Your JOBMATCH account is ready.", "success")
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError:
            db().rollback(); flash("That email address is already registered.", "danger")
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        user=db().execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if user and check_password_hash(user["password_hash"],password):
            session.clear(); session.update(user_id=user["id"],name=user["name"],role=user["role"])
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.","danger")
    return render_template("login.html")

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        admin=db().execute("SELECT * FROM admins WHERE email=?",(email,)).fetchone()
        if admin and check_password_hash(admin["password_hash"],password):
            session.clear(); session.update(user_id=admin["id"],name="Administrator",role="admin")
            return redirect(url_for("admin_dashboard"))
        flash("Admin email or password is incorrect.","danger")
    return render_template("admin_login.html")

@app.route("/logout")
def logout():
    session.clear(); flash("You have signed out.","success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required()
def dashboard():
    if session["role"]=="admin": return redirect(url_for("admin_dashboard"))
    if session["role"]=="employer":
        jobs=db().execute("""SELECT j.*,(SELECT COUNT(*) FROM applications a WHERE a.job_id=j.id) AS application_count
                             FROM jobs j WHERE employer_id=? ORDER BY created_at DESC""",(session["user_id"],)).fetchall()
        applications=db().execute("""SELECT a.*,j.title AS job_title,u.name AS applicant_name,u.email AS applicant_email,
            s.phone,s.skills,s.experience,s.location,s.cv_filename FROM applications a JOIN jobs j ON j.id=a.job_id
            JOIN users u ON u.id=a.seeker_id JOIN seekers s ON s.user_id=a.seeker_id
            WHERE j.employer_id=? ORDER BY a.created_at DESC""",(session["user_id"],)).fetchall()
        return render_template("employer_dashboard.html",jobs=jobs,applications=applications)
    seeker=db().execute("SELECT * FROM seekers WHERE user_id=?",(session["user_id"],)).fetchone()
    jobs=db().execute("""SELECT j.*,u.name AS company FROM jobs j JOIN users u ON u.id=j.employer_id
                         WHERE j.is_active=1 ORDER BY j.created_at DESC""").fetchall()
    matches=[]
    for job in jobs:
        score,reasons=score_match(seeker,job)
        matches.append({"job":job,"score":score,"reasons":reasons})
    matches.sort(key=lambda x:x["score"],reverse=True)
    applications=db().execute("""SELECT a.*,j.title AS job_title,u.name AS company FROM applications a
        JOIN jobs j ON j.id=a.job_id JOIN users u ON u.id=j.employer_id
        WHERE a.seeker_id=? ORDER BY a.created_at DESC""",(session["user_id"],)).fetchall()
    return render_template("seeker_dashboard.html",seeker=seeker,matches=matches,applications=applications)

@app.route("/profile", methods=["GET","POST"])
@login_required("seeker")
def profile():
    seeker=db().execute("SELECT * FROM seekers WHERE user_id=?",(session["user_id"],)).fetchone()
    if request.method=="POST":
        try:
            exp=int(request.form.get("experience","")); salary=int(request.form.get("salary",""))
            phone=request.form.get("phone","").strip(); skills=request.form.get("skills","").strip()
            location=request.form.get("location","").strip()
            if min(exp,salary)<0 or not all([phone,skills,location]): raise ValueError
        except ValueError:
            flash("Please enter valid profile details.","danger"); return render_template("profile.html",seeker=seeker)
        db().execute("""UPDATE seekers SET phone=?,skills=?,experience=?,location=?,expected_salary=?
                        WHERE user_id=?""",(phone,skills,exp,location,salary,session["user_id"]))
        db().commit(); flash("Your profile has been updated.","success")
        return redirect(url_for("profile"))
    return render_template("profile.html",seeker=seeker)

@app.route("/profile/cv", methods=["POST"])
@login_required("seeker")
def upload_cv():
    file=request.files.get("cv")
    if not file or not file.filename:
        flash("Choose a CV file first.","warning"); return redirect(url_for("profile"))
    original=secure_filename(file.filename)
    ext=original.rsplit(".",1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_CV:
        flash("Upload a PDF, DOCX or TXT CV only.","danger"); return redirect(url_for("profile"))
    stored=f"seeker_{session['user_id']}_{secrets.token_hex(8)}.{ext}"
    path=UPLOAD_DIR/stored
    file.save(path)
    text=extract_cv_text(path)
    if not text.strip():
        path.unlink(missing_ok=True)
        flash("We could not read text from that file. Try a text-based PDF, DOCX or TXT file.","danger")
        return redirect(url_for("profile"))
    old=db().execute("SELECT cv_filename FROM seekers WHERE user_id=?",(session["user_id"],)).fetchone()
    db().execute("UPDATE seekers SET cv_filename=?,cv_text=? WHERE user_id=?",(stored,text,session["user_id"]))
    db().commit()
    if old and old["cv_filename"]:
        (UPLOAD_DIR/old["cv_filename"]).unlink(missing_ok=True)
    flash("CV uploaded. JOBMATCH can now include its text in skill matching.","success")
    return redirect(url_for("profile"))

@app.route("/applications/<int:application_id>/cv")
@login_required("employer")
def download_candidate_cv(application_id):
    row=db().execute("""SELECT s.cv_filename FROM applications a JOIN jobs j ON j.id=a.job_id
        JOIN seekers s ON s.user_id=a.seeker_id WHERE a.id=? AND j.employer_id=?""",
        (application_id,session["user_id"])).fetchone()
    if not row or not row["cv_filename"]: abort(404)
    return send_from_directory(UPLOAD_DIR,row["cv_filename"],as_attachment=True,download_name="candidate_cv."+row["cv_filename"].rsplit(".",1)[-1])

@app.route("/jobs")
def jobs():
    rows=db().execute("""SELECT j.*,u.name AS company FROM jobs j JOIN users u ON u.id=j.employer_id
                         WHERE j.is_active=1 ORDER BY j.created_at DESC""").fetchall()
    return render_template("jobs.html",jobs=rows)

@app.route("/jobs/new",methods=["GET","POST"])
@login_required("employer")
def new_job():
    if request.method=="POST":
        title=request.form.get("title","").strip(); desc=request.form.get("description","").strip()
        skills=request.form.get("skills","").strip(); loc=request.form.get("location","").strip()
        try:
            exp=int(request.form.get("experience","")); salary=int(request.form.get("salary",""))
            if min(exp,salary)<0 or not all([title,desc,skills,loc]): raise ValueError
        except ValueError:
            flash("Complete all fields with valid non-negative numbers.","danger")
            return render_template("new_job.html")
        db().execute("""INSERT INTO jobs(employer_id,title,description,skills,experience,location,salary)
                        VALUES(?,?,?,?,?,?,?)""",(session["user_id"],title,desc,skills,exp,loc,salary))
        db().commit(); flash("Your vacancy is published.","success")
        return redirect(url_for("dashboard"))
    return render_template("new_job.html")

@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job=db().execute("""SELECT j.*,u.name AS company FROM jobs j JOIN users u ON u.id=j.employer_id
                        WHERE j.id=? AND j.is_active=1""",(job_id,)).fetchone()
    if not job: abort(404)
    score=None; reasons=[]; already=False
    if session.get("role")=="seeker":
        seeker=db().execute("SELECT * FROM seekers WHERE user_id=?",(session["user_id"],)).fetchone()
        score,reasons=score_match(seeker,job)
        already=bool(db().execute("SELECT 1 FROM applications WHERE job_id=? AND seeker_id=?",
                                  (job_id,session["user_id"])).fetchone())
    return render_template("job_detail.html",job=job,score=score,reasons=reasons,already_applied=already)

@app.route("/jobs/<int:job_id>/apply",methods=["POST"])
@login_required("seeker")
def apply_for_job(job_id):
    job=db().execute("SELECT * FROM jobs WHERE id=? AND is_active=1",(job_id,)).fetchone()
    if not job: abort(404)
    message=request.form.get("message","").strip()[:1500]
    try:
        cur=db().execute("INSERT INTO applications(job_id,seeker_id,message) VALUES(?,?,?)",
                         (job_id,session["user_id"],message))
        db().commit()
        employer=db().execute("SELECT email FROM users WHERE id=?",(job["employer_id"],)).fetchone()
        notify(job["employer_id"],employer["email"],"New JOBMATCH application",
               f"{session['name']} applied for {job['title']}. Sign in to review the application.",
               url_for("dashboard"))
        flash("Your application has been submitted.","success")
    except sqlite3.IntegrityError:
        db().rollback(); flash("You have already applied for this job.","warning")
    return redirect(url_for("job_detail",job_id=job_id))

@app.route("/applications/<int:application_id>/status",methods=["POST"])
@login_required("employer")
def update_status(application_id):
    status=request.form.get("status","")
    if status not in STATUSES: abort(400)
    approw=get_application_for_user(application_id)
    if not approw or approw["employer_id"]!=session["user_id"]: abort(404)
    db().execute("UPDATE applications SET status=? WHERE id=?",(status,application_id))
    notify(approw["seeker_id"],approw["seeker_email"],"Application status updated",
           f"Your application for {approw['job_title']} is now marked: {status}.",
           url_for("dashboard"))
    db().commit(); flash("Application status updated and candidate notified.","success")
    return redirect(url_for("dashboard"))

@app.route("/applications/<int:application_id>/messages",methods=["GET","POST"])
@login_required()
def conversation(application_id):
    approw=get_application_for_user(application_id)
    if not approw: abort(404)
    uid=session.get("user_id")
    if session["role"]=="admin" or uid not in (approw["seeker_id"],approw["employer_id"]): abort(403)
    if request.method=="POST":
        body=request.form.get("body","").strip()
        if not body or len(body)>3000:
            flash("Messages must contain 1–3000 characters.","danger")
        else:
            db().execute("INSERT INTO messages(application_id,sender_id,body) VALUES(?,?,?)",
                         (application_id,uid,body))
            recipient_id=approw["employer_id"] if uid==approw["seeker_id"] else approw["seeker_id"]
            recipient_email=approw["employer_email"] if uid==approw["seeker_id"] else approw["seeker_email"]
            notify(recipient_id,recipient_email,"New JOBMATCH message",
                   f"You have new  {approw['job_title']}. Sign in to read it.",
                   url_for("conversation",application_id=application_id))
            db().commit(); flash("Message sent.","success")
        return redirect(url_for("conversation",application_id=application_id))
    msgs=db().execute("""SELECT m.*,u.name AS sender_name FROM messages m JOIN users u ON u.id=m.sender_id
                         WHERE m.application_id=? ORDER BY m.created_at""",(application_id,)).fetchall()
    return render_template("conversation.html",application=approw,messages=msgs)

@app.route("/notifications")
@login_required()
def notifications():
    rows=db().execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
                       (session["user_id"],)).fetchall()
    db().execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(session["user_id"],)); db().commit()
    return render_template("notifications.html",notifications=rows)

@app.route("/admin/dashboard")
@login_required("admin")
def admin_dashboard():
    stats={
      "users":db().execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
      "seekers":db().execute("SELECT COUNT(*) n FROM users WHERE role='seeker'").fetchone()["n"],
      "employers":db().execute("SELECT COUNT(*) n FROM users WHERE role='employer'").fetchone()["n"],
      "jobs":db().execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"],
      "applications":db().execute("SELECT COUNT(*) n FROM applications").fetchone()["n"],
    }
    users=db().execute("SELECT id,name,email,role,created_at FROM users ORDER BY created_at DESC LIMIT 100").fetchall()
    jobs=db().execute("""SELECT j.*,u.name company FROM jobs j JOIN users u ON u.id=j.employer_id
                         ORDER BY j.created_at DESC LIMIT 100""").fetchall()
    return render_template("admin_dashboard.html",stats=stats,users=users,jobs=jobs)

@app.route("/admin/jobs/<int:job_id>/toggle",methods=["POST"])
@login_required("admin")
def admin_toggle_job(job_id):
    row=db().execute("SELECT is_active FROM jobs WHERE id=?",(job_id,)).fetchone()
    if not row: abort(404)
    db().execute("UPDATE jobs SET is_active=? WHERE id=?",(0 if row["is_active"] else 1,job_id))
    db().commit(); flash("Job listing visibility changed.","success")
    return redirect(url_for("admin_dashboard"))

@app.errorhandler(413)
def too_large(_error):
    flash("That upload is too large. Maximum CV size is 5 MB.","danger")
    return redirect(url_for("profile"))

@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html",code=400,message=getattr(error,"description","Bad request")),400

@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html",code=403,message="You do not have permission to view this page."),403

@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html",code=404,message="The page or record could not be found."),404

if __name__=="__main__":
    init_db()
    app.run(debug=os.environ.get("FLASK_DEBUG")=="1")
