import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ExamMate | Exam Planner",
  applicationName: "ExamMate",
  description: "Plan your exams and keep your schedule organized with ExamMate.",
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
