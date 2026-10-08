"use client";

import {
  ArrowDownToLine,
  ArrowUpRight,
  Bell,
  CalendarDays,
  Check,
  Clock3,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type Exam = {
  id: number;
  course_code: string;
  course_title: string;
  date: string;
  start_time: string;
  end_time: string;
  program: string;
  calendar_added: boolean;
  calendar_deleted: boolean;
};

type DashboardStatus = {
  google_connected: boolean;
  google_client_configured: boolean;
  email: string;
  timezone: string;
  scheduled_count: number;
};

function formatTime(value: string) {
  const [hours, minutes] = value.split(":").map(Number);
  const suffix = hours >= 12 ? "PM" : "AM";
  return `${hours % 12 || 12}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

function formatZonedDateTime(value: Date, timeZone: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(value).map(({ type, value: partValue }) => [type, partValue]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
}

export default function Home() {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [exams, setExams] = useState<Exam[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const routineInputRef = useRef<HTMLInputElement>(null);
  const [courseCodes, setCourseCodes] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [activeSection, setActiveSection] = useState("overview");

  useEffect(() => {
    if (!error) return;
    const timeout = window.setTimeout(() => setError(""), 5000);
    return () => window.clearTimeout(timeout);
  }, [error]);

  useEffect(() => {
    if (!message) return;
    const timeout = window.setTimeout(() => setMessage(""), 5000);
    return () => window.clearTimeout(timeout);
  }, [message]);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    try {
      const [statusResponse, examsResponse] = await Promise.all([
        fetch("/api/status", { cache: "no-store" }),
        fetch("/api/exams", { cache: "no-store" }),
      ]);
      if (!statusResponse.ok || !examsResponse.ok) {
        const statusResult = await statusResponse.json().catch(() => null) as { detail?: string } | null;
        const examsResult = await examsResponse.json().catch(() => null) as { detail?: string } | null;
        throw new Error(
          statusResult?.detail ?? examsResult?.detail ?? "Could not load dashboard data. Is the Python backend running?",
        );
      }
      const statusData = (await statusResponse.json()) as DashboardStatus;
      const examData = (await examsResponse.json()) as { exams: Exam[] };
      setStatus(statusData);
      setExams(examData.exams);
      setError("");
      return statusData.google_connected;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load dashboard.");
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const updateActiveSection = () => {
      setActiveSection(window.location.hash === "#schedule" ? "schedule" : "overview");
    };
    updateActiveSection();
    window.addEventListener("hashchange", updateActiveSection);
    const query = new URLSearchParams(window.location.search);
    if (query.get("connected") === "1") {
      window.history.replaceState({}, "", window.location.pathname);
      void loadDashboard().then((isConnected) => {
        if (isConnected) {
          setMessage("Google account connected successfully.");
        } else {
          setError("Google returned successfully, but this browser is not connected yet. Please try Connect Google again.");
        }
      });
      return () => window.removeEventListener("hashchange", updateActiveSection);
    } else if (query.get("auth_error") === "token_exchange") {
      setError(
        "Google approved sign-in, but the server could not complete it. Check the Google client secret and callback URL in Railway, then try again.",
      );
      window.history.replaceState({}, "", window.location.pathname);
    } else if (query.get("auth_error") === "profile") {
      setError("Google sign-in could not verify your account. Check the OAuth client setup and try again.");
      window.history.replaceState({}, "", window.location.pathname);
    } else if (query.get("auth_error") === "state" || query.get("auth_error") === "missing_code") {
      setError("This Google sign-in link expired or could not be verified. Please connect again.");
      window.history.replaceState({}, "", window.location.pathname);
    } else if (query.get("auth_error")) {
      setError("Google sign-in was cancelled. Connect your account to schedule reminders.");
      window.history.replaceState({}, "", window.location.pathname);
    }
    void loadDashboard();
    return () => window.removeEventListener("hashchange", updateActiveSection);
  }, [loadDashboard]);

  const upcomingExams = useMemo(
    () => {
      const now = formatZonedDateTime(new Date(), status?.timezone ?? "Asia/Dhaka");
      return exams.filter(
        (exam) => !exam.calendar_deleted && `${exam.date}T${exam.end_time}:00` >= now,
      );
    },
    [exams, status?.timezone],
  );

  async function handleSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (!status?.google_connected) {
      setError("Connect your Google account before scheduling exam reminders.");
      return;
    }
    if (!file) {
      setError("Choose an exam routine file first.");
      return;
    }

    const formData = new FormData();
    formData.set("course_codes", courseCodes);
    formData.set("file", file);

    setBusy(true);
    try {
      const response = await fetch("/api/exams/schedule", {
        method: "POST",
        body: formData,
      });
      const result = (await response.json()) as {
        exams?: Exam[];
        unmatched_course_codes?: string[];
        message?: string;
        detail?: string;
      };
      if (!response.ok) throw new Error(result.detail ?? "Could not schedule these exams.");
      const unmatched = result.unmatched_course_codes?.length
        ? ` Not found: ${result.unmatched_course_codes.join(", ")}.`
        : "";
      setMessage(`${result.message ?? "Exam reminders scheduled."}${unmatched}`);
      setFile(null);
      if (routineInputRef.current) routineInputRef.current.value = "";
      await loadDashboard();
    } catch (scheduleError) {
      setError(
        scheduleError instanceof Error ? scheduleError.message : "Could not schedule these exams.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteRoutine() {
    if (!window.confirm("Delete this routine and remove its exam events from your Google Calendar?")) {
      return;
    }
    setError("");
    setMessage("");
    setDeleting(true);
    try {
      const response = await fetch("/api/exams", { method: "DELETE" });
      const result = (await response.json()) as { message?: string; detail?: string };
      if (!response.ok) throw new Error(result.detail ?? "Could not delete this routine.");
      setFile(null);
      if (routineInputRef.current) routineInputRef.current.value = "";
      setCourseCodes("");
      setMessage(result.message ?? "Routine deleted.");
      await loadDashboard();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete this routine.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <main className="page-shell">
      <aside className="sidebar">
        <a className="brand" href="#">
          <span className="brand-mark"><CalendarDays size={19} /></span>
          <span>SEU <span className="brand-light">Exam Routin</span></span>
        </a>
        <div className="side-label">WORKSPACE</div>
        <a className={`nav-item ${activeSection === "overview" ? "active" : ""}`} href="#overview" onClick={() => setActiveSection("overview")}><span className="nav-dot" />Overview</a>
        <a className={`nav-item ${activeSection === "schedule" ? "active" : ""}`} href="#schedule" onClick={() => setActiveSection("schedule")}><CalendarDays size={17} />Exam schedule</a>
        <div className="sidebar-bottom">
          <div className="help-card">
            <div className="help-icon"><Bell size={17} /></div>
            <strong>Never miss an exam</strong>
            <p>Calendar alerts help keep your exam day on track.</p>
          </div>
        </div>
      </aside>

      <section className="main-area" id="overview">
        {loading && !status ? (
          <header className="topbar skeleton-topbar" aria-label="Loading dashboard">
            <span className="skeleton skeleton-bar skeleton-breadcrumb" />
            <span className="skeleton skeleton-bar skeleton-account" />
          </header>
        ) : (
          <header className="topbar">
            <div className="breadcrumbs">
              Workspace <span>/</span> {activeSection === "schedule" ? "Exam schedule" : "Overview"}
            </div>
            {status?.google_connected && (
              <div className="account-pill account-linked">
                <Check size={15} />
                <span>{status.email}</span>
              </div>
            )}
          </header>
        )}

        <div className="content">
          {loading && !status ? (
            <div className="dashboard-skeleton" aria-label="Loading dashboard content" aria-busy="true">
              <div className="skeleton-welcome">
                <div><span className="skeleton skeleton-bar skeleton-eyebrow" /><span className="skeleton skeleton-bar skeleton-title" /><span className="skeleton skeleton-bar skeleton-copy" /></div>
                <span className="skeleton skeleton-chip" />
              </div>
              <div className="skeleton-stats">
                {[0, 1, 2].map((item) => <div className="skeleton-card" key={item}><span className="skeleton skeleton-bar skeleton-label" /><span className="skeleton skeleton-bar skeleton-value" /><span className="skeleton skeleton-bar skeleton-copy" /></div>)}
              </div>
              <div className="skeleton skeleton-banner" />
              <div className="skeleton-section-heading"><span className="skeleton skeleton-bar skeleton-label" /><span className="skeleton skeleton-bar skeleton-title-small" /></div>
              <div className="skeleton-planner">
                <div className="skeleton-card skeleton-form">
                  <span className="skeleton skeleton-bar skeleton-title-small" />
                  <span className="skeleton skeleton-upload" />
                  <span className="skeleton skeleton-bar skeleton-label" />
                  <span className="skeleton skeleton-textarea" />
                  <span className="skeleton skeleton-button" />
                </div>
                <div className="skeleton-card skeleton-how">
                  <span className="skeleton skeleton-bar skeleton-title-small" />
                  <span className="skeleton skeleton-bar skeleton-copy" />
                  <span className="skeleton skeleton-bar skeleton-copy" />
                  <span className="skeleton skeleton-bar skeleton-copy" />
                  <span className="skeleton skeleton-upload" />
                </div>
              </div>
              <div className="skeleton-section-heading"><span className="skeleton skeleton-bar skeleton-label" /><span className="skeleton skeleton-bar skeleton-title-small" /></div>
              <div className="skeleton-card skeleton-exams">
                {[0, 1, 2].map((item) => <div className="skeleton-exam-row" key={item}><span className="skeleton skeleton-exam-date" /><span className="skeleton skeleton-bar skeleton-copy" /><span className="skeleton skeleton-bar skeleton-time" /></div>)}
              </div>
            </div>
          ) : (
          <>
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span /> YOUR PERSONAL EXAM PLANNER</div>
              <h1>Good evening<span className="title-period">.</span></h1>
              <p className="welcome-copy">One less thing to worry about. Keep your exams organized and on time.</p>
            </div>
            <div className="today-chip"><CalendarDays size={16} /> {new Intl.DateTimeFormat("en", { weekday: "short", day: "numeric", month: "short" }).format(new Date())}</div>
          </section>

          {error && <div className="alert error-alert" role="alert"><strong>SEU Exam Routin</strong><span>{error}</span><button className="toast-close" type="button" onClick={() => setError("")} aria-label="Dismiss notification"><X size={16} /></button></div>}
          {message && <div className="alert success-alert" role="status" aria-live="polite"><Check size={17} /><strong>SEU Exam Routin</strong><span>{message}</span><button className="toast-close" type="button" onClick={() => setMessage("")} aria-label="Dismiss notification"><X size={16} /></button></div>}

          <section className="stats-grid">
            <div className="stat-card">
              <div className="stat-top"><span>UPCOMING EXAMS</span><div className="stat-icon blue"><CalendarDays size={17} /></div></div>
              <div className="stat-value">{loading ? "—" : upcomingExams.length}</div>
              <div className="stat-note">In your current schedule</div>
            </div>
            <div className="stat-card">
              <div className="stat-top"><span>CALENDAR STATUS</span><div className={`stat-icon ${status?.google_connected ? "green" : "red"}`}>{status?.google_connected ? <Check size={17} /> : <X size={17} />}</div></div>
      <div className="stat-value status-value">{status?.google_connected ? "Connected" : "Not connected"}</div>
              <div className="stat-note">{status?.google_connected ? "Events sync to your primary calendar" : "Connect Google to get started"}</div>
            </div>
            <div className="stat-card">
              <div className="stat-top"><span>CALENDAR REMINDERS</span><div className="stat-icon amber"><Bell size={17} /></div></div>
              <div className="stat-value status-value">5:00 AM</div>
              <div className="stat-note">Exam day · {status?.timezone ?? "Asia/Dhaka"}</div>
            </div>
          </section>

          <section className="connect-banner">
            <div className="connect-symbol"><ShieldCheck size={22} /></div>
            <div className="connect-copy">
              <strong>
                {status?.google_connected
                  ? "Your Google account is connected"
                  : status?.google_client_configured
                    ? "Connect your Google account"
                    : "Connect Google Calendar"}
              </strong>
              <span>
                {status?.google_connected
                  ? `Calendar reminders will use ${status.email}.`
                  : status?.google_client_configured
                    ? "Allow access to create exam reminders in your Google Calendar."
                    : "Link your Google account to add exam reminders to your calendar."}
              </span>
            </div>
            {!status?.google_connected ? (
              <a
                className="connect-button"
                href="/api/auth/google"
                title="Connect your Google account"
                onClick={(event) => {
                  if (!status?.google_client_configured) {
                    event.preventDefault();
                    setError("Google sign-in is temporarily unavailable. Please try again later.");
                  }
                }}
              >
                Connect Google <ArrowUpRight size={16} />
              </a>
            ) : (
              <span className="connected-label"><Check size={15} /> Connected</span>
            )}
          </section>

          <div className="section-heading" id="schedule">
            <div>
              <div className="eyebrow">GET STARTED</div>
              <h2>Build your exam schedule</h2>
              <p>Upload a routine, add your course codes, and we’ll handle the reminders.</p>
            </div>
            <div className="step-indicator"><span>01</span><i /><span>02</span><i /><span>03</span></div>
          </div>

          <div className="planner-grid">
            <form className="planner-card" onSubmit={handleSchedule}>
              <div className="card-title-row">
                <div className="card-title-icon"><UploadCloud size={19} /></div>
                <div><h3>Add your routine</h3><p>Upload the routine file downloaded from your university drive.</p></div>
              </div>
              <label className={`upload-zone ${file ? "has-file" : ""}`}>
                <input
                  ref={routineInputRef}
                  type="file"
                  accept=".csv,.xlsx,.xls,.xlsm,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
                <span className="upload-icon"><ArrowDownToLine size={19} /></span>
                <strong>{file ? file.name : "Choose an exam routine file"}</strong>
                <span>{file ? `${(file.size / 1024).toFixed(0)} KB · Click to change` : "Click to browse · Excel (.xlsx, .xls, .xlsm) or CSV · up to 10 MB"}</span>
              </label>
              <label className="field-label" htmlFor="course-codes">YOUR COURSE CODES</label>
              <textarea id="course-codes" className="course-textarea" value={courseCodes} onChange={(event) => setCourseCodes(event.target.value)} placeholder="e.g. CSE351.1, CSE359.2, CSE383.2" rows={3} />
              <div className="field-hint"><span>Separate multiple codes with commas.</span><span>{courseCodes.split(",").filter((code) => code.trim()).length} added</span></div>
              <button className="schedule-button" type="submit" disabled={busy || deleting || loading}>
                {busy ? <><LoaderCircle size={17} className="spin" /> Adding your exams…</> : <>Create my reminders <ArrowUpRight size={17} /></>}
              </button>
              <p className="privacy-note"><ShieldCheck size={14} /> Routine files are processed in memory; only your matched exams are saved to your account.</p>
            </form>

            <aside className="how-card">
              <div className="how-heading"><span className="sparkle">✳</span><div><h3>How it works</h3><p>From routine to ready in a minute.</p></div></div>
              <div className="timeline">
                <div className="timeline-item"><span className="timeline-number">1</span>                <div><strong>Pick your routine</strong><p>Download the routine from your university drive and upload the Excel/CSV file.</p></div></div>
                <div className="timeline-item"><span className="timeline-number">2</span><div><strong>Add your course codes</strong><p>We’ll match only your exams from the full routine.</p></div></div>
                <div className="timeline-item"><span className="timeline-number">3</span><div><strong>We’ll take it from here</strong><p>Calendar alerts and cleanup after each exam.</p></div></div>
              </div>
              <div className="reminder-info">
                <div className="reminder-info-icon"><Clock3 size={16} /></div>
                <div><strong>Built-in Calendar reminders</strong><p>5:00 AM on exam day<br />1 day and 2 hours before</p></div>
              </div>
            </aside>
          </div>

          <section className="exam-list-section">
            <div className="list-heading">
              <div><div className="eyebrow">YOUR PLAN</div><h2>Upcoming exams</h2></div>
              <div className="list-actions">
                {exams.length > 0 && <button className="delete-routine-button" type="button" onClick={() => void handleDeleteRoutine()} disabled={deleting || busy} aria-label="Delete routine and calendar events">
                  {deleting ? <LoaderCircle size={14} className="spin" /> : <Trash2 size={14} />}
                  {deleting ? "Deleting…" : "Delete routine"}
                </button>}
                <button className="refresh-button" type="button" onClick={() => void loadDashboard()} aria-label="Refresh exam list"><RefreshCw size={16} /></button>
              </div>
            </div>
            {loading ? <div className="empty-state"><LoaderCircle className="spin" size={20} /> Loading your schedule…</div> : upcomingExams.length ? (
              <div className="exam-list">
                {upcomingExams.map((exam) => (
                  <article className="exam-row" key={exam.id}>
                    <div className="exam-date"><strong>{new Intl.DateTimeFormat("en", { day: "2-digit" }).format(new Date(`${exam.date}T00:00:00`))}</strong><span>{new Intl.DateTimeFormat("en", { month: "short" }).format(new Date(`${exam.date}T00:00:00`)).toUpperCase()}</span></div>
                    <div className="exam-main"><strong>{exam.course_code} <span>·</span> {exam.course_title}</strong><span>{exam.program}</span></div>
                    <div className="exam-time"><Clock3 size={15} />{formatTime(exam.start_time)} – {formatTime(exam.end_time)}</div>
                    <span className={`calendar-badge ${exam.calendar_added ? "" : "calendar-pending"}`}>
                      {exam.calendar_added ? <Check size={13} /> : <Clock3 size={13} />}
                      {exam.calendar_added ? "Calendar linked" : "Not linked"}
                    </span>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty-state"><CalendarDays size={20} /> Your matched exams will appear here once scheduled.</div>
            )}
          </section>
          <footer className="page-footer">Made for a calmer exam season <span>✳</span></footer>
          </>
          )}
        </div>
      </section>
    </main>
  );
}
