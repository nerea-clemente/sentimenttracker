import type { Metadata } from "next";
import "./globals.css";
import { SideNav } from "@/components/SideNav";
import { SnapshotBanner } from "@/components/SnapshotBanner";

export const metadata: Metadata = {
  title: "Campaign Impact Benchmarker",
  description:
    "Measured, traceable media footprints for external campaigns and investigations.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <SideNav />
          <main>
            <SnapshotBanner />
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
