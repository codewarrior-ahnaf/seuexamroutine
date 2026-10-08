import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ExamMate | Exam Planner",
  description: "Schedule exam reminders in Google Calendar and Gmail.",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
