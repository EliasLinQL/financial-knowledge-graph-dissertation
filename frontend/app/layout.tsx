import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Financial Event Intelligence",
  description:
    "Explore company news, source evidence, related companies and price context.",
  openGraph: {
    title: "Financial Event Intelligence",
    description:
      "A clear workspace for checking company news and related events.",
    images: ["/og-financial-intelligence.png"],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Financial Event Intelligence",
    description: "A clear workspace for checking company news and related events.",
    images: ["/og-financial-intelligence.png"],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f4f1e8",
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
