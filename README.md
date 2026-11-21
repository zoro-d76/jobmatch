# JOBMATCH — Neon PostgreSQL deployment build

This build uses Flask + PostgreSQL (Neon). It no longer creates or uses a local SQLite database.

## Render configuration

In the Render Web Service settings:

- **Root Directory:** leave blank if these files are at the root of your GitHub repository.
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn wsgi:app`

Set these Environment Variables in Render:

- `DATABASE_URL` — the Neon PostgreSQL connection string (use the pooled connection string; keep it secret).
- `SECRET_KEY` — a long random secret, at least 40 characters.

After saving environment variables, Render should redeploy the service. The `wsgi.py` file calls `init_db()` and creates the required tables automatically.

## Important: uploaded CV files

The database records are stored in Neon and persist independently of the free Render web service. However, uploaded CV files are still stored under `instance/uploads` by default. Render's free web-service filesystem is ephemeral, so uploaded CV files may disappear after a redeploy/restart. For production, configure durable object storage for CVs (or a paid persistent disk where supported) before accepting real applicants.

You can set `UPLOAD_DIR` to a durable storage mount later. Do not set it to `/var/data` on Render Free because Free web services do not have persistent disks.

## Existing SQLite data

This source ZIP did not include a `jobmatch.db` SQLite database file, so there are no local database records in this archive to transfer. If you have a separate `jobmatch.db` on your computer, keep it safe; it can be migrated separately after the Neon app is working.

## Create an administrator

After the Neon tables have been created by the first successful app start, run:

`python create_admin.py admin@example.com`

Set `DATABASE_URL` in the environment where you run the command. The script prompts for an administrator password (12+ characters).

## Local development

Install dependencies:

`pip install -r requirements.txt`

Set `DATABASE_URL` and `SECRET_KEY`, then run:

`python app.py`

## Privacy and production readiness

This is still a development platform. Before using real job seekers' personal data or CVs, add durable private file storage, backups, retention/deletion controls, privacy notices and consent, rate limiting, email verification, password reset, security review, and monitoring. Review matching logic for fairness; its score is a rough guide, not a hiring decision.
