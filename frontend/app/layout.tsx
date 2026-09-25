import type { Metadata } from "next";
import { Barlow, Barlow_Semi_Condensed } from "next/font/google";
import { AuthProvider } from "@/components/auth/AuthProvider";
import "./globals.css";
import "./product.css";

// Latin and numerals only; Chinese falls through to the system CJK stack in globals.css.
const barlow = Barlow({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-barlow"
});

const barlowSemiCondensed = Barlow_Semi_Condensed({
  subsets: ["latin"],
  weight: ["600", "700"],
  display: "swap",
  variable: "--font-barlow-condensed"
});

export const metadata: Metadata = {
  // Pages name themselves ("我的比赛 · CS2 Demo Coach"), so open tabs stay tellable apart.
  title: { default: "CS2 Demo Coach", template: "%s · CS2 Demo Coach" },
  description: "上传 CS2 比赛录像，结合战术回放、第一人称视频和重点建议复盘。",
  icons: {
    icon: "/favicon.svg"
  }
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={`${barlow.variable} ${barlowSemiCondensed.variable}`}>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
