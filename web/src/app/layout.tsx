import type { Metadata } from "next";
import "./globals.css";
import { SideNav } from "@/components/SideNav";

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
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
