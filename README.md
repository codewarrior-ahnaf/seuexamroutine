# SEU Exam Mate

SEU Exam Mate is an exam-planning app with a Next.js dashboard and FastAPI backend. Students upload a CSV or Excel routine, enter course codes, connect Google Calendar, and create personal exam events with Calendar notifications.

## Requirements

- Node.js 20+
- Python 3.10+
- A Google Cloud OAuth web client

## Google OAuth setup

1. Enable **Google Calendar API** in Google Cloud.
2. Configure the OAuth consent screen. Add test accounts while developing. Public use by other Google accounts requires publishing and may require Google's verification for Calendar access.
3. Create an OAuth client ID with application type **Web application** and add this redirect URI:
   - Local: `http://localhost:3000/api/auth/callback`
   - Hosted: `https://YOUR-FRONTEND-DOMAIN/api/auth/callback`
4. Set the consent screen app name to **SEU Exam Mate**.
5. For local development, put the downloaded OAuth client JSON in the project root as `gcalendercred.json`. For Railway, store the complete JSON as a private `GOOGLE_CLIENT_CONFIG_JSON` variable. Never commit or expose this secret.

Each student connects their own Google account and grants Calendar and basic account identity access. Google `sub` is used as the stable account identifier. The app stores encrypted credentials and keeps exam records separate by Google account.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm run dev:all
```

Open `http://localhost:3000`. Both services run together through `npm run dev:all`; the Python virtual environment must exist first. Local development uses SQLite (`exam_dashboard.sqlite3`) and a generated private `.session_secret`; both are ignored by git.

## Deploy: Vercel frontend and Railway API

Deploy the Next.js project from the repository root on Vercel. Create a Railway project with PostgreSQL and an API service built from `Dockerfile.api`. Keep one API replica running for Calendar event cleanup.

Set these private variables on the Railway API service:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `FRONTEND_ORIGIN` | `https://YOUR-VERCEL-DOMAIN` |
| `GOOGLE_REDIRECT_URI` | `https://YOUR-VERCEL-DOMAIN/api/auth/callback` |
| `GOOGLE_CLIENT_CONFIG_JSON` | The complete Google OAuth web-client JSON |
| `SESSION_SECRET_KEY` | A long random secret, e.g. `openssl rand -hex 32` |
| `TIMEZONE` | `Asia/Dhaka` |

Set these variables on Vercel (Production and Preview if needed):

| Variable | Value |
|---|---|
| `API_BACKEND_URL` | The Railway API's public HTTPS origin, e.g. `https://your-api.up.railway.app` |
| `FRONTEND_ORIGIN` | `https://YOUR-VERCEL-DOMAIN` |

Use the exact same frontend origin in Railway and Vercel, with no trailing slash. The Google OAuth client's authorized JavaScript origin must be that origin, and its redirect URI must be `https://YOUR-VERCEL-DOMAIN/api/auth/callback`. If the Vercel domain changes, update those values and redeploy. Never use `NEXT_PUBLIC_*` for private credentials.

While the OAuth consent screen is in Testing mode, only listed test accounts can connect; Google also expires test authorizations after 7 days for these scopes. For general access, publish the app and complete any verification Google requires.

The app uses an HTTP-only, Secure-on-HTTPS session cookie with SameSite=Lax. Routine files are parsed in memory and not retained. Users see only their own exam records.

## Routine and Calendar reminders

Required columns (case-insensitive): `Course Code`, `Course Title`, `Date`, `Start Time`, and `End Time`. Optional columns: `Program`, `Faculty`, `Slot`, and `Students`. CSV, XLSX, XLS, and XLSM are supported, up to 10 MB. Download university Drive files locally before uploading; Drive share links are not imported.

Enter comma-separated course codes. Matching exams are added to the connected account's primary Google Calendar with popup reminders at 5:00 AM on exam day, one day before, and two hours before the exam. The app does not request Gmail access or send email reminders. Calendar events are deleted after their end time. Use **Delete routine** to remove saved exams and their Calendar events. Times use `Asia/Dhaka` unless `TIMEZONE` is changed.
