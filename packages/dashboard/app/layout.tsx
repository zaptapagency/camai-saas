import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "CamAI Dashboard",
  description: "Live CCTV analytics — occupancy, fleet health, and billing.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-fg">
        <Providers>
          <Nav />
          <main className="mx-auto max-w-[1200px] px-4 py-6 sm:px-5">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
