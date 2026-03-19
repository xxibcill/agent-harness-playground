import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Harness",
  description: "Monitoring client for agent workflow execution.",
};

type RootLayoutProps = Readonly<{
  children: React.ReactNode;
}>;

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

