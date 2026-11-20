import getpass, sqlite3, sys
from pathlib import Path
from werkzeug.security import generate_password_hash

DB = Path(__file__).resolve().parent / "jobmatch.db"
email = (sys.argv[1] if len(sys.argv)>1 else input("Admin email: ")).strip().lower()
password = getpass.getpass("Choose admin password (12+ characters): ")
if len(password) < 12:
    raise SystemExit("Password must be at least 12 characters.")
con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS admins (
 id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
try:
    con.execute("INSERT INTO admins(email,password_hash) VALUES(?,?)",
                (email, generate_password_hash(password)))
    con.commit()
    print("Administrator account created. Sign in at /admin/login.")
except sqlite3.IntegrityError:
    print("That administrator email already exists.")
finally:
    con.close()
