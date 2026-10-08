import Link from "next/link";
import { ArrowLeft, CalendarDays } from "lucide-react";

export default function NotFound() {
  return (
    <main className="not-found-page">
      <div className="not-found-card">
        <span className="not-found-icon"><CalendarDays size={25} /></span>
        <span className="eyebrow">404 · PAGE NOT FOUND</span>
        <h1>This page is off the schedule.</h1>
        <p>The page you’re looking for may have moved or the address may be incorrect.</p>
        <Link className="not-found-link" href="/">
          <ArrowLeft size={16} /> Back to SEU Exam Mate
        </Link>
      </div>
    </main>
  );
}
