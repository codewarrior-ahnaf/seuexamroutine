import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SEU Exam Mate | Exam Planner",
  applicationName: "SEU Exam Mate",
  description: "Plan your exams and keep your schedule organized with SEU Exam Mate.",
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
