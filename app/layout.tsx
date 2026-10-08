import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SEU Exam Routin | Exam Planner",
  applicationName: "SEU Exam Routin",
  description: "Plan your exams and keep your schedule organized with SEU Exam Routin.",
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
