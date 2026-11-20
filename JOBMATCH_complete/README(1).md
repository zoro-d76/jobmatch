# JOBMATCH Complete Platform (Development Build)

A full-featured educational recruitment-platform prototype built with Flask, SQLite, HTML and CSS.

## Included features
1. Job-seeker and employer accounts with hashed passwords.
2. Persistent SQLite database.
3. Employer job publishing and public job listings.
4. Job applications, duplicate-application protection and status updates.
5. CV upload (PDF, DOCX, TXT; 5 MB limit) and local text extraction.
6. Explainable, local skills-based matching using profile skills and CV text.
7. Candidate/employer conversations linked to an application.
8. In-app notifications.
9. Optional email notifications via SMTP environment variables.
10. Admin dashboard, platform counts, user listing and job-listing moderation.
11. Basic CSRF form-token checks, upload extension restrictions, file-size limits and access checks.
12. Responsive layout for desktop and mobile.

## Run on Windows
1. Extract this ZIP.
2. Open the extracted `JOBMATCH_complete` folder in VS Code.
3. Open the VS Code terminal.
4. Create a virtual environment:
   `python -m venv .venv`
5. Activate it:
   `.venv\Scripts\activate`
6. Install packages:
   `pip install -r requirements.txt`
7. Start:
   `python app.py`
8. Visit `http://127.0.0.1:5000`.

macOS/Linux activation: `source .venv/bin/activate`.

The database is created automatically as `jobmatch.db`.

## Create an administrator account
Run this from the project folder after installing dependencies:

`python create_admin.py admin@example.com`

The script prompts you to enter an admin password. Use a unique password of at least 12 characters. Then sign in at `/admin/login`.

## Optional email notifications
Email is disabled unless SMTP settings are configured. Set these environment variables before starting the app:
- `SMTP_HOST`
- `SMTP_PORT` (usually 587)
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM` (optional)

Use an email provider's app password or SMTP credential. Never put credentials in source code or commit them to Git.

## Important: what "AI matching" means in this version
This version includes an explainable local matching algorithm. It compares required job skills with profile skills and words extracted from the uploaded CV, then adds experience, location and salary factors. It does not call a generative AI service, train a model, or make hiring decisions. Its score is only a rough guide and may miss synonyms, context, transferable skills or unfairly penalise non-traditional backgrounds. Employers must review candidates fairly and make decisions themselves.

## Data and privacy
This is a development prototype. Use fictional/test data while learning. CVs and contact details are personal information. Do not expose this server publicly or upload real CVs until you have reviewed applicable privacy/employment rules, implemented retention/deletion controls, HTTPS, secure production configuration, backups, audit logs, rate limiting, stronger CSRF protection and appropriate access policies.

## Before deployment
- Set a long random `SECRET_KEY`.
- Run with debug mode off (default).
- Use HTTPS and secure cookie settings.
- Add email verification, password reset, rate limiting and abuse reporting.
- Add a proper privacy notice, consent, data export/deletion and retention schedule.
- Scan uploaded documents and consider storing them outside the web root/object storage.
- Review matching logic for bias; do not automatically reject or rank people for consequential decisions.
- Configure production database, backups and monitoring.
