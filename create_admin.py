import getpass
import os
import sys
import psycopg2
from werkzeug.security import generate_password_hash

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise SystemExit("DATABASE_URL is missing. Set it before creating an admin account.")

email = (sys.argv[1] if len(sys.argv) > 1 else input("Admin email: ")).strip().lower()
password = getpass.getpass("Choose admin password (12+ characters): ")
if len(password) < 12:
    raise SystemExit("Password must be at least 12 characters.")

con = psycopg2.connect(database_url, connect_timeout=15)
try:
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO admins(email,password_hash) VALUES(%s,%s)",
            (email, generate_password_hash(password)),
        )
    con.commit()
    print("Administrator account created. Sign in at /admin/login.")
except psycopg2.IntegrityError:
    con.rollback()
    print("That administrator email already exists.")
finally:
    con.close()
