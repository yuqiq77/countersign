import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Countersign",
  description: "Evidence-driven, human-governed job intelligence workflows",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
