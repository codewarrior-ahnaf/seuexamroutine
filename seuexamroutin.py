from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time as time_module
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, time
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from typing import Any, Generator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import psycopg
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, File, Form, HTTPException, Request as FastAPIRequest, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from psycopg.rows import dict_row
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError as GoogleHttpError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from requests.exceptions import RequestException


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "exam_dashboard.sqlite3"))
GOOGLE_CLIENT_FILE = Path(
    os.getenv("GOOGLE_CLIENT_FILE", BASE_DIR / "gcalendercred.json")
)
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").rstrip("/")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", f"{FRONTEND_ORIGIN}/api/auth/callback"
)
TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Dhaka")
SESSION_SECRET_FILE = BASE_DIR / ".session_secret"
SESSION_SECRET = os.getenv("SESSION_SECRET_KEY", "")
IS_PRODUCTION = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("NODE_ENV") == "production")
if IS_PRODUCTION and not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL to the persistent PostgreSQL database in production.")
if not SESSION_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError("Set SESSION_SECRET_KEY to a private random value in production.")
    try:
        SESSION_SECRET = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        SESSION_SECRET = secrets.token_urlsafe(48)
        SESSION_SECRET_FILE.write_text(SESSION_SECRET, encoding="utf-8")
        SESSION_SECRET_FILE.chmod(0o600)
IS_SECURE_COOKIE = FRONTEND_ORIGIN.startswith("https://")
MAX_FILE_BYTES = 10 * 1024 * 1024
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
]
POLL_INTERVAL_SECONDS = 20
SESSION_IDLE_SECONDS = 10 * 24 * 60 * 60

try:
    EXAM_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(f"Unknown TIMEZONE value: {TIMEZONE_NAME}") from exc


def get_files_in_directory(
    path: str, ext: str | None = None, startswith: str | None = None,
    endswith: str | None = None, includes: str | None = None,
) -> list[str]:
    """Return files matching the optional filters."""
    files: list[str] = []
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        if not os.path.isfile(full_path):
            continue
        if ext and Path(full_path).suffix.lstrip(".") != ext:
            continue
        if startswith and not full_path.startswith(startswith):
            continue
        if endswith and not full_path.endswith(endswith):
            continue
        if includes and includes not in full_path:
            continue
        files.append(full_path)
    return files


class _DatabaseConnection:
    def __init__(self, connection: Any, postgres: bool) -> None:
        self._connection = connection
        self._postgres = postgres

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self._postgres:
            statement = statement.replace("?", "%s")
        return self._connection.execute(statement, parameters)

    def __enter__(self) -> _DatabaseConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *exception: Any) -> Any:
        return self._connection.__exit__(*exception)

    def close(self) -> None:
        self._connection.close()


def _connect_db() -> _DatabaseConnection:
    if DATABASE_URL:
        connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return _DatabaseConnection(connection, postgres=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return _DatabaseConnection(connection, postgres=False)


@contextmanager
def _database() -> Generator[_DatabaseConnection, None, None]:
    connection = _connect_db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _initialize_database() -> None:
    if not DATABASE_URL:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _database() as connection:
        if DATABASE_URL:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS exams (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    course_code TEXT NOT NULL,
                    course_title TEXT NOT NULL,
                    exam_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    program TEXT NOT NULL,
                    faculty TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    students TEXT NOT NULL,
                    calendar_event_id TEXT,
                    calendar_deleted INTEGER NOT NULL DEFAULT 0,
                    email_sent INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (user_id, signature)
                )
                """
            )
        else:
            existing = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'exams'"
            ).fetchone()
            if existing and "user_id" not in (existing["sql"] or ""):
                connection.execute("ALTER TABLE exams RENAME TO exams_legacy")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS exams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    signature TEXT NOT NULL,
                    course_code TEXT NOT NULL,
                    course_title TEXT NOT NULL,
                    exam_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    program TEXT NOT NULL,
                    faculty TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    students TEXT NOT NULL,
                    calendar_event_id TEXT,
                    calendar_deleted INTEGER NOT NULL DEFAULT 0,
                    email_sent INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (user_id, signature)
                )
                """
            )
            if existing and "user_id" not in (existing["sql"] or ""):
                connection.execute(
                    """
                    INSERT INTO exams (
                        id, user_id, signature, course_code, course_title, exam_date,
                        start_time, end_time, program, faculty, slot, students,
                        calendar_event_id, calendar_deleted, email_sent
                    )
                    SELECT id, '', signature, course_code, course_title, exam_date,
                           start_time, end_time, program, faculty, slot, students,
                           calendar_event_id, calendar_deleted, email_sent
                    FROM exams_legacy
                    """
                )
                connection.execute("DROP TABLE exams_legacy")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS google_accounts (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                encrypted_credentials TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_flows (
                state TEXT PRIMARY KEY,
                browser_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS exams_user_date_idx ON exams (user_id, exam_date, start_time)"
        )


def _row_to_exam(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "course_code": row["course_code"],
        "course_title": row["course_title"],
        "date": row["exam_date"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "program": row["program"],
        "faculty": row["faculty"],
        "slot": row["slot"],
        "students": row["students"],
        "calendar_added": bool(row["calendar_event_id"]),
        "calendar_deleted": bool(row["calendar_deleted"]),
        "email_sent": bool(row["email_sent"]),
    }


def _session_signature(payload: str) -> str:
    digest = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _create_session_cookie(user_id: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"user_id": user_id, "expires": int(time_module.time()) + SESSION_IDLE_SECONDS},
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{payload}.{_session_signature(payload)}"


def _get_session_user_id(request: FastAPIRequest) -> str:
    value = request.cookies.get("exam_session", "")
    try:
        payload, signature = value.split(".", maxsplit=1)
        if not hmac.compare_digest(signature, _session_signature(payload)):
            raise ValueError("Invalid session signature.")
        encoded_payload = payload + "=" * (-len(payload) % 4)
        session = json.loads(base64.urlsafe_b64decode(encoded_payload))
        if int(session["expires"]) < int(time_module.time()):
            raise ValueError("Session expired.")
        user_id = str(session["user_id"])
    except (
        ValueError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(status_code=401, detail="Connect your Google account first.") from exc

    with _database() as connection:
        account = connection.execute(
            "SELECT user_id FROM google_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not account:
        raise HTTPException(status_code=401, detail="Connect your Google account first.")
    return user_id


def _encrypt_credentials(credentials_json: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(SESSION_SECRET.encode("utf-8")).digest())
    return Fernet(key).encrypt(credentials_json.encode("utf-8")).decode("ascii")


def _decrypt_credentials(encrypted_credentials: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(SESSION_SECRET.encode("utf-8")).digest())
    try:
        return Fernet(key).decrypt(encrypted_credentials.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Saved Google credentials cannot be decrypted. Check SESSION_SECRET_KEY.",
        ) from exc


def _safe_text(value: Any, fallback: str = "N/A") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text if text and text.casefold() != "nan" else fallback


def _parse_exam_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(str(value).strip(), dayfirst=True, errors="raise")
    return parsed.date()


def _parse_exam_time(value: Any, column_name: str) -> time:
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, pd.Timedelta):
        seconds = int(value.total_seconds()) % (24 * 60 * 60)
        return time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)

    raw_value = _safe_text(value, "")
    if not raw_value:
        raise ValueError(f"Missing required value in '{column_name}'.")
    for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(raw_value, time_format).time()
        except ValueError:
            continue
    raise ValueError(f"Could not parse '{raw_value}' as a time in '{column_name}'.")


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    exam_date = _parse_exam_date(row["Date"])
    start_time = _parse_exam_time(row["Start Time"], "Start Time")
    end_time = _parse_exam_time(row["End Time"], "End Time")
    start_dt = datetime.combine(exam_date, start_time, tzinfo=EXAM_TIMEZONE)
    end_dt = datetime.combine(exam_date, end_time, tzinfo=EXAM_TIMEZONE)
    if end_dt <= start_dt:
        raise ValueError(
            f"End Time must be later than Start Time for {_safe_text(row['Course Code'])}."
        )

    exam = {
        "course_code": _safe_text(row["Course Code"]),
        "course_title": _safe_text(row["Course Title"]),
        "date": exam_date.isoformat(),
        "start_time": start_time.strftime("%H:%M"),
        "end_time": end_time.strftime("%H:%M"),
        "program": _safe_text(row.get("Program"), "Exam Hall"),
        "faculty": _safe_text(row.get("Faculty")),
        "slot": _safe_text(row.get("Slot")),
        "students": _safe_text(row.get("Students")),
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }
    signature_values = (
        exam["course_code"].casefold(),
        exam["course_title"].casefold(),
        exam["date"],
        exam["start_time"],
        exam["end_time"],
        exam["program"].casefold(),
    )
    exam["signature"] = hashlib.sha256(
        "|".join(signature_values).encode("utf-8")
    ).hexdigest()
    return exam


def _read_routine(data: bytes, filename: str) -> pd.DataFrame:
    extension = Path(filename).suffix.casefold()
    try:
        if extension == ".csv":
            frame = pd.read_csv(BytesIO(data), encoding="utf-8-sig")
        elif extension in {".xlsx", ".xls", ".xlsm"}:
            frame = pd.read_excel(BytesIO(data))
        else:
            raise ValueError("Please provide a .csv, .xlsx, or .xls routine file.")
    except (ValueError, ImportError, OSError, pd.errors.ParserError) as exc:
        raise ValueError(f"Could not read the routine file: {exc}") from exc

    frame.columns = [str(column).strip() for column in frame.columns]
    canonical_columns = {str(column).casefold(): str(column) for column in frame.columns}
    required_columns = ("Course Code", "Course Title", "Date", "Start Time", "End Time")
    missing = [
        column for column in required_columns
        if column.casefold() not in canonical_columns
    ]
    if missing:
        raise ValueError(
            "Missing required column(s): " + ", ".join(missing)
        )
    frame.rename(
        columns={
            canonical_columns[column.casefold()]: column
            for column in required_columns
        },
        inplace=True,
    )
    for optional_column in ("Program", "Faculty", "Slot", "Students"):
        if optional_column.casefold() in canonical_columns:
            frame.rename(
                columns={
                    canonical_columns[optional_column.casefold()]: optional_column
                },
                inplace=True,
            )
    return frame


def _load_google_client_config() -> dict[str, Any]:
    client_json = os.getenv("GOOGLE_CLIENT_CONFIG_JSON")
    if not client_json and not GOOGLE_CLIENT_FILE.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_CONFIG_JSON "
                "or provide gcalendercred.json."
            ),
        )
    try:
        if client_json:
            client_config = json.loads(client_json)
        else:
            client_config = json.loads(GOOGLE_CLIENT_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError, TypeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth client configuration is invalid.",
        ) from exc
    web_config = client_config.get("web") if isinstance(client_config, dict) else None
    if not isinstance(web_config, dict) or not all(
        isinstance(web_config.get(key), str) and web_config[key].strip()
        for key in ("client_id", "client_secret", "auth_uri", "token_uri")
    ):
        raise HTTPException(
            status_code=503,
            detail="Google OAuth configuration must be the complete Web application JSON.",
        )
    return client_config


def _google_oauth_configured() -> bool:
    try:
        _load_google_client_config()
    except HTTPException:
        return False
    return True


def _get_flow(state: str | None = None) -> Flow:
    client_config = _load_google_client_config()
    try:
        flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth client configuration is invalid.",
        ) from exc
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    return flow


def _get_google_credentials(user_id: str) -> Credentials:
    with _database() as connection:
        account = connection.execute(
            "SELECT encrypted_credentials FROM google_accounts WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not account:
        raise HTTPException(
            status_code=401,
            detail="Connect your Google account before scheduling exam reminders.",
        )
    credentials_json = _decrypt_credentials(account["encrypted_credentials"])
    credentials = Credentials.from_authorized_user_info(
        json.loads(credentials_json), SCOPES
    )
    if not credentials.has_scopes(SCOPES):
        raise HTTPException(
            status_code=401,
            detail="Reconnect your Google account to grant the required permissions.",
        )
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(GoogleAuthRequest())
        except GoogleAuthError as exc:
            raise HTTPException(
                status_code=401,
                detail="Google authorization expired. Connect your Google account again.",
            ) from exc
        with _database() as connection:
            connection.execute(
                "UPDATE google_accounts SET encrypted_credentials = ? WHERE user_id = ?",
                (_encrypt_credentials(credentials.to_json()), user_id),
            )
    if not credentials.valid:
        raise HTTPException(
            status_code=401,
            detail="Google authorization expired. Connect your Google account again.",
        )
    return credentials


def _calendar_service(credentials: Credentials) -> Any:
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _gmail_service(credentials: Credentials) -> Any:
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _get_google_email(credentials: Credentials) -> str:
    profile = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    email = profile.userinfo().get().execute().get("email")
    if not email:
        raise RuntimeError("Google did not return the linked account email address.")
    return str(email)


def _get_google_profile(credentials: Credentials) -> tuple[str, str]:
    profile = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    data = profile.userinfo().get().execute()
    user_id = data.get("id")
    email = data.get("email")
    if not user_id or not email:
        raise RuntimeError("Google did not return the account ID and email address.")
    return str(user_id), str(email)


def build_exam_event_details(exam: dict[str, Any]) -> dict[str, Any]:
    exam_start = exam["start_datetime"]
    five_am = datetime.combine(
        exam_start.date(), time(5, 0), tzinfo=EXAM_TIMEZONE
    )
    five_am_minutes_before = max(
        0, int((exam_start - five_am).total_seconds() // 60)
    )
    popup_minutes = sorted({1440, 120, five_am_minutes_before})
    return {
        "summary": f"{exam['course_code']} - {exam['course_title']} (Exam)",
        "location": exam["program"],
        "description": (
            f"Program: {exam['program']}\n"
            f"Course: {exam['course_code']} - {exam['course_title']}\n"
            f"Slot: {exam['slot']}\n"
            f"Faculty: {exam['faculty']}\n"
            f"Students: {exam['students']}\n"
            f"Date: {exam['date']}\n"
            f"Time: {exam['start_time']} - {exam['end_time']}"
        ),
        "start": {
            "dateTime": exam["start_datetime"].isoformat(),
            "timeZone": TIMEZONE_NAME,
        },
        "end": {
            "dateTime": exam["end_datetime"].isoformat(),
            "timeZone": TIMEZONE_NAME,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                *(
                    {"method": "popup", "minutes": minutes}
                    for minutes in popup_minutes
                ),
                {"method": "email", "minutes": 2880},
            ],
        },
    }


def _insert_calendar_event(credentials: Credentials, exam: dict[str, Any]) -> str:
    event = _calendar_service(credentials).events().insert(
        calendarId="primary", body=build_exam_event_details(exam)
    ).execute()
    event_id = event.get("id")
    if not event_id:
        raise RuntimeError("Google Calendar did not return an event ID.")
    return event_id


def _send_exam_email(
    credentials: Credentials, exam: dict[str, Any], reminder_time: str = "12:00 AM"
) -> None:
    message = EmailMessage()
    message["To"] = _get_google_email(credentials)
    message["Subject"] = f"{reminder_time} exam reminder: {exam['course_code']} - {exam['course_title']}"
    message.set_content(
        f"This is your {reminder_time} exam reminder for today.\n\n"
        f"Course: {exam['course_code']} - {exam['course_title']}\n"
        f"Date: {exam['date']}\n"
        f"Time: {exam['start_time']} - {exam['end_time']} ({TIMEZONE_NAME})\n"
        f"Venue: {exam['program']}\n"
        f"Slot: {exam['slot']}\n\n"
        "Good luck with your exam!"
    )
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    _gmail_service(credentials).users().messages().send(
        userId="me", body={"raw": encoded_message}
    ).execute()


def _delete_calendar_event(credentials: Credentials, event_id: str) -> None:
    try:
        _calendar_service(credentials).events().delete(
            calendarId="primary", eventId=event_id
        ).execute()
    except GoogleHttpError as exc:
        if exc.resp.status != 404:
            raise


async def _run_reminder_worker() -> None:
    while True:
        try:
            now = datetime.now(EXAM_TIMEZONE)
            today = now.date().isoformat()
            with _database() as connection:
                due_midnight_emails = connection.execute(
                    """
                    SELECT * FROM exams
                    WHERE user_id != ''
                      AND exam_date = ?
                      AND email_sent = 0
                      AND exam_date || 'T' || end_time > ?
                    """,
                    (today, now.strftime("%Y-%m-%dT%H:%M")),
                ).fetchall()
                due_deletions = connection.execute(
                    """
                    SELECT * FROM exams
                    WHERE user_id != ''
                      AND calendar_event_id IS NOT NULL
                      AND calendar_deleted = 0
                      AND exam_date || 'T' || end_time <= ?
                    """,
                    (now.strftime("%Y-%m-%dT%H:%M"),),
                ).fetchall()
        except Exception:
            logger.exception("Reminder worker could not read due jobs; it will retry.")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        for row in due_midnight_emails:
            exam = _row_to_exam(row)
            try:
                credentials = await asyncio.to_thread(
                    _get_google_credentials, row["user_id"]
                )
                await asyncio.to_thread(_send_exam_email, credentials, exam, "12:00 AM")
                with _database() as connection:
                    connection.execute(
                        "UPDATE exams SET email_sent = 1 WHERE id = ? AND user_id = ?",
                        (row["id"], row["user_id"]),
                    )
            except Exception as exc:
                logger.exception(
                    "Could not send exam reminder for exam id %s: %s", row["id"], exc
                )

        for row in due_deletions:
            try:
                credentials = await asyncio.to_thread(
                    _get_google_credentials, row["user_id"]
                )
                await asyncio.to_thread(
                    _delete_calendar_event, credentials, row["calendar_event_id"]
                )
                with _database() as connection:
                    connection.execute(
                        "UPDATE exams SET calendar_deleted = 1 WHERE id = ? AND user_id = ?",
                        (row["id"], row["user_id"]),
                    )
            except Exception as exc:
                logger.exception(
                    "Could not remove calendar event for exam id %s: %s", row["id"], exc
                )

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _initialize_database()
    worker = asyncio.create_task(_run_reminder_worker())
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


app = FastAPI(title="ExamMate API", lifespan=lifespan)


@app.middleware("http")
async def refresh_active_session(request: FastAPIRequest, call_next: Any) -> Any:
    response = await call_next(request)
    value = request.cookies.get("exam_session", "")
    try:
        payload, signature = value.split(".", maxsplit=1)
        if not hmac.compare_digest(signature, _session_signature(payload)):
            return response
        encoded_payload = payload + "=" * (-len(payload) % 4)
        session = json.loads(base64.urlsafe_b64decode(encoded_payload))
        user_id = str(session["user_id"])
        if int(session["expires"]) >= int(time_module.time()) and user_id:
            response.set_cookie(
                "exam_session",
                _create_session_cookie(user_id),
                max_age=SESSION_IDLE_SECONDS,
                httponly=True,
                secure=IS_SECURE_COOKIE,
                samesite="lax",
                path="/",
            )
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        pass
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
@app.get("/api/status")
def get_status(request: FastAPIRequest) -> dict[str, Any]:
    connected = False
    email = ""
    scheduled_count = 0
    try:
        user_id = _get_session_user_id(request)
    except HTTPException:
        user_id = ""
    if user_id:
        with _database() as connection:
            account = connection.execute(
                "SELECT email FROM google_accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
            scheduled_count = connection.execute(
                "SELECT COUNT(*) AS count FROM exams WHERE user_id = ? "
                "AND calendar_event_id IS NOT NULL AND calendar_deleted = 0",
                (user_id,),
            ).fetchone()["count"]
        if account:
            email = account["email"]
            try:
                _get_google_credentials(user_id)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                email = ""
            else:
                connected = True
    client_configured = _google_oauth_configured()
    return {
        "google_connected": connected,
        "google_client_configured": client_configured,
        "email": email,
        "timezone": TIMEZONE_NAME,
        "scheduled_count": scheduled_count,
    }


@app.get("/api/auth/google")
def start_google_auth() -> RedirectResponse:
    flow = _get_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    browser_nonce = secrets.token_urlsafe(32)
    with _database() as connection:
        connection.execute(
            "DELETE FROM oauth_flows WHERE created_at < ?", (time_module.time() - 600,)
        )
        connection.execute(
            "INSERT INTO oauth_flows (state, browser_hash, created_at) VALUES (?, ?, ?)",
            (
                state,
                hashlib.sha256(browser_nonce.encode("ascii")).hexdigest(),
                time_module.time(),
            ),
        )
    response = RedirectResponse(authorization_url)
    response.set_cookie(
        "exam_oauth_browser",
        browser_nonce,
        max_age=600,
        httponly=True,
        secure=IS_SECURE_COOKIE,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/auth/callback")
def google_auth_callback(
    request: FastAPIRequest,
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        response = RedirectResponse(f"{FRONTEND_ORIGIN}/?auth_error=cancelled")
        response.delete_cookie(
            "exam_oauth_browser", path="/", secure=IS_SECURE_COOKIE, samesite="lax"
        )
        return response
    if not code:
        raise HTTPException(status_code=400, detail="Google authorization did not return a code.")
    browser_nonce = request.cookies.get("exam_oauth_browser", "")
    if not browser_nonce:
        raise HTTPException(status_code=400, detail="Invalid or expired Google OAuth state.")
    with _database() as connection:
        flow_state = connection.execute(
            "SELECT browser_hash, created_at FROM oauth_flows WHERE state = ?",
            (state,),
        ).fetchone()
        connection.execute("DELETE FROM oauth_flows WHERE state = ?", (state,))
    expected_browser_hash = hashlib.sha256(browser_nonce.encode("ascii")).hexdigest()
    if (
        not flow_state
        or time_module.time() - flow_state["created_at"] > 600
        or not hmac.compare_digest(flow_state["browser_hash"], expected_browser_hash)
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired Google OAuth state.")
    flow = _get_flow(state)
    try:
        flow.fetch_token(code=code)
    except (GoogleAuthError, OAuth2Error, RequestException) as exc:
        logger.exception("Google OAuth token exchange failed.")
        response = RedirectResponse(f"{FRONTEND_ORIGIN}/?auth_error=token_exchange")
        response.delete_cookie(
            "exam_oauth_browser", path="/", secure=IS_SECURE_COOKIE, samesite="lax"
        )
        return response
    user_id, email = _get_google_profile(flow.credentials)
    with _database() as connection:
        account_count = connection.execute(
            "SELECT COUNT(*) AS count FROM google_accounts"
        ).fetchone()["count"]
        connection.execute(
            """
            INSERT INTO google_accounts (user_id, email, encrypted_credentials)
            VALUES (?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                email = excluded.email,
                encrypted_credentials = excluded.encrypted_credentials
            """,
            (user_id, email, _encrypt_credentials(flow.credentials.to_json())),
        )
        if account_count == 0:
            connection.execute(
                "UPDATE exams SET user_id = ? WHERE user_id = ''", (user_id,)
            )
    response = RedirectResponse(f"{FRONTEND_ORIGIN}/?connected=1")
    response.set_cookie(
        "exam_session",
        _create_session_cookie(user_id),
        max_age=SESSION_IDLE_SECONDS,
        httponly=True,
        secure=IS_SECURE_COOKIE,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        "exam_oauth_browser", path="/", secure=IS_SECURE_COOKIE, samesite="lax"
    )
    return response


@app.get("/api/exams")
def list_exams(request: FastAPIRequest) -> dict[str, Any]:
    try:
        user_id = _get_session_user_id(request)
    except HTTPException:
        return {"exams": []}
    with _database() as connection:
        rows = connection.execute(
            "SELECT * FROM exams WHERE user_id = ? "
            "ORDER BY exam_date, start_time, course_code",
            (user_id,),
        ).fetchall()
    return {"exams": [_row_to_exam(row) for row in rows]}


@app.delete("/api/exams")
async def delete_exams(request: FastAPIRequest) -> dict[str, str]:
    allowed_origins = {FRONTEND_ORIGIN, "http://127.0.0.1:3000"}
    if request.headers.get("origin") not in allowed_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")
    user_id = _get_session_user_id(request)

    with _database() as connection:
        rows = connection.execute(
            "SELECT id, calendar_event_id FROM exams WHERE user_id = ? "
            "AND calendar_event_id IS NOT NULL",
            (user_id,),
        ).fetchall()

    if rows:
        credentials = _get_google_credentials(user_id)
        for row in rows:
            try:
                await asyncio.to_thread(
                    _delete_calendar_event, credentials, row["calendar_event_id"]
                )
            except (GoogleHttpError, OSError) as exc:
                logger.exception(
                    "Could not remove calendar event for exam id %s.", row["id"]
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Could not remove every exam from Google Calendar. "
                        "Please retry deleting the routine."
                    ),
                ) from exc

    with _database() as connection:
        connection.execute("DELETE FROM exams WHERE user_id = ?", (user_id,))
    return {"message": "Routine and its calendar events were deleted."}


@app.post("/api/exams/schedule")
async def schedule_exams(
    request: FastAPIRequest,
    course_codes: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    allowed_origins = {FRONTEND_ORIGIN, "http://127.0.0.1:3000"}
    if request.headers.get("origin") not in allowed_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")
    user_id = _get_session_user_id(request)
    codes = {code.strip().casefold() for code in course_codes.split(",") if code.strip()}
    if not codes:
        raise HTTPException(status_code=400, detail="Enter at least one course code.")

    try:
        filename = file.filename or ""
        if Path(filename).suffix.casefold() not in {".csv", ".xlsx", ".xls", ".xlsm"}:
            raise ValueError("Please upload a .csv, .xlsx, .xls, or .xlsm routine file.")
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("The routine file must be 10 MB or smaller.")
        frame = _read_routine(data, filename)
        matched = frame[
            frame["Course Code"].astype(str).str.strip().str.casefold().isin(codes)
        ]
        normalized = [_normalize_row(row) for row in matched.to_dict(orient="records")]
    except (ValueError, TypeError, pd.errors.ParserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not normalized:
        raise HTTPException(
            status_code=404,
            detail="No matching courses found. Check the entered course codes and routine.",
        )
    credentials = _get_google_credentials(user_id)
    scheduled: list[dict[str, Any]] = []
    try:
        for exam in normalized:
            with _database() as connection:
                connection.execute(
                    """
                    INSERT INTO exams (
                        user_id, signature, course_code, course_title, exam_date, start_time,
                        end_time, program, faculty, slot, students
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (user_id, signature) DO NOTHING
                    """,
                    (
                        user_id, exam["signature"], exam["course_code"], exam["course_title"],
                        exam["date"], exam["start_time"], exam["end_time"],
                        exam["program"], exam["faculty"], exam["slot"], exam["students"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM exams WHERE user_id = ? AND signature = ?",
                    (user_id, exam["signature"]),
                ).fetchone()
                if row["calendar_event_id"] or row["calendar_deleted"]:
                    scheduled.append(_row_to_exam(row))
                    continue
                exam_id = row["id"]
            event_id = await asyncio.to_thread(_insert_calendar_event, credentials, exam)
            with _database() as connection:
                connection.execute(
                    "UPDATE exams SET calendar_event_id = ? WHERE id = ? AND user_id = ?",
                    (event_id, exam_id, user_id),
                )
                updated_row = connection.execute(
                    "SELECT * FROM exams WHERE id = ? AND user_id = ?",
                    (exam_id, user_id),
                ).fetchone()
                scheduled.append(_row_to_exam(updated_row))
    except GoogleHttpError as exc:
        logger.exception("Google Calendar could not create an exam event.")
        raise HTTPException(
            status_code=502,
            detail="Google Calendar could not add an event. Check your Google permissions.",
        ) from exc

    matched_codes = {exam["course_code"].casefold() for exam in normalized}
    return {
        "exams": scheduled,
        "unmatched_course_codes": sorted(codes - matched_codes),
        "message": f"Scheduled {len(scheduled)} matching exam(s).",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("seuexamroutin:app", host="127.0.0.1", port=8000, reload=True)
