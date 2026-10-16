import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "llms.txt Generator — Make your website AI-readable",
  description: "Discover your website’s important pages and generate a structured llms.txt file. Free to generate, easy to review, and ready to download.",
};

export const viewport: Viewport = { themeColor: "#fafaf8" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body suppressHydrationWarning>{children}</body></html>;
}
