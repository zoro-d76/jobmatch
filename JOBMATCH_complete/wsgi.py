import os
from app import app, init_db, db
from werkzeug.security import generate_password_hash

init_db()

with app.app_context():
    email = os.getenv("JOBMATCH_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("JOBMATCH_ADMIN_PASSWORD", "")

    if email and password:
        connection = db()
        admin = connection.execute(
            "SELECT id FROM admins WHERE email = ?", (email,)
        ).fetchone()

        if admin:
            connection.execute(
                "UPDATE admins SET password_hash = ? WHERE email = ?",
                (generate_password_hash(password), email)
            )
        else:
            connection.execute(
                "INSERT INTO admins (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password))
            )

        connection.commit()