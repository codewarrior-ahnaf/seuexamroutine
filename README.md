# ExamMate

ExamMate is a Next.js dashboard with a FastAPI backend. Users upload a local CSV/Excel exam routine, enter course codes, connect their own Google account, and schedule private Calendar events and exam-day emails.

## Requirements

- Node.js 20+
- Python 3.10+
- A Google Cloud OAuth web client

## Google OAuth setup

1. In Google Cloud Console, enable **Google Calendar API** and **Gmail API**.
2. Configure the OAuth consent screen. Add test accounts while developing; unrestricted public use may require publishing and Google verification for Calendar/Gmail scopes.
3. Create an OAuth client ID with application type **Web application** and add the redirect URI:
   - Local: `http://localhost:3000/api/auth/callback`
   - Public: `https://YOUR-FRONTEND-DOMAIN/api/auth/callback`
4. For local development, put its downloaded JSON in the project root as `gcalendercred.json`. For Railway, store its complete JSON as a private `GOOGLE_CLIENT_CONFIG_JSON` variable. Never commit or expose this secret.

Each person clicks **Connect Google** and approves Calendar, Gmail-send, and account-email access. Their Google account owns their calendar events and exam reminders; the app stores encrypted credentials and scopes exam records to that account.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm run dev:all
```

Open `http://localhost:3000`. `npm run dev` and `npm run dev:all` both start the Next.js dashboard and the project `.venv` Python API; the virtual environment must be created first. Keep both services running for midnight emails and post-exam calendar cleanup. Local development uses SQLite (`exam_dashboard.sqlite3`) and a generated private `.session_secret`; both are ignored by git.

## Public deployment: Vercel web + Railway services

The Next.js website can be public on Vercel. The API still needs a server because it handles Google OAuth secrets, private user data, file parsing, and Calendar/Gmail requests. The API also runs the reminder worker continuously, so keep it on Railway with one replica; Vercel Functions are short-lived and are not a replacement for this worker in this project.

Deploy the Next.js project on Vercel from the repository root. Create a Railway project with PostgreSQL and an API service built from `Dockerfile.api`. Do not deploy `Dockerfile.web` to Railway when using Vercel for the website.

Set these private variables on the Railway API service:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `FRONTEND_ORIGIN` | `https://YOUR-VERCEL-DOMAIN` |
| `GOOGLE_REDIRECT_URI` | `https://YOUR-VERCEL-DOMAIN/api/auth/callback` |
| `GOOGLE_CLIENT_CONFIG_JSON` | The complete Google OAuth web-client JSON |
| `SESSION_SECRET_KEY` | A long random secret, e.g. `openssl rand -hex 32` |
| `TIMEZONE` | `Asia/Dhaka` |

Set these environment variables on the Vercel project (Production, Preview only if needed):

| Variable | Value |
|---|---|
| `API_BACKEND_URL` | The Railway API's public HTTPS domain, e.g. `https://exam-api.up.railway.app` |
| `FRONTEND_ORIGIN` | `https://YOUR-VERCEL-DOMAIN` |

Generate the Vercel production domain first, then use that exact HTTPS domain for both `FRONTEND_ORIGIN` and `GOOGLE_REDIRECT_URI` on Railway. Add the exact redirect URI to the Google OAuth web client. Never use `NEXT_PUBLIC_*` for database credentials, OAuth client secrets, or the session secret. In Google Auth Platform, add your account as a test user while the app is in Testing mode. Testing mode is limited to 100 test users and Google expires authorizations after 7 days for these non-profile scopes, so you may need to reconnect weekly until the OAuth app is verified and published. Public use by arbitrary Google accounts requires Google's OAuth publishing and verification steps.

The session cookie is HTTP-only, Secure on HTTPS, SameSite=Lax, and expires after 10 days without activity. API requests refresh its expiry while the user is active. The Railway worker sends one exam-day email at 12:00 AM Bangladesh time. Google Calendar pop-up reminders are set for 5:00 AM on exam day, 1 day before, and 2 hours before the exam; an email reminder is also set 2 days before through Google Calendar.

The Next.js API proxy forwards session cookies and the OAuth callback to the private API. Uploaded routine files are parsed in memory and not retained. Each user sees only their own saved exams.

## Routine format and reminders

Required columns (case-insensitive): `Course Code`, `Course Title`, `Date`, `Start Time`, and `End Time`. Optional columns: `Program`, `Faculty`, `Slot`, and `Students`. CSV, XLSX, XLS, and XLSM uploads are supported, up to 10 MB. Download university Drive files locally before uploading; Drive share links are not imported.

Enter comma-separated course codes. Calendar events are created in the linked account's primary calendar, with popup reminders at 5:00 AM on exam day, 1 day before, and 2 hours before; Google Calendar also gets an email reminder 2 days before. The app sends a separate Gmail reminder at 12:00 AM on exam day. Calendar events are deleted after their end time. Use **Delete routine** to remove your saved exams and their calendar events before uploading a replacement. Times use `Asia/Dhaka` unless `TIMEZONE` is changed.
