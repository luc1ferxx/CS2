import type { Metadata } from "next";
import { Chakra_Petch } from "next/font/google";
import { AuthProvider } from "@/components/auth/AuthProvider";
import "./globals.css";
import "./product.css";

// The techno face for Latin names and numbers; globals.css puts it first in
// --font-body and lets Chinese fall through to the system CJK face.
const techFont = Chakra_Petch({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-tech"
});

export const metadata: Metadata = {
  // Pages name themselves ("我的比赛 - CS2 复盘"), so open tabs stay tellable apart.
  title: { default: "CS2 复盘", template: "%s - CS2 复盘" },
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
    <html lang="zh-CN" className={techFont.variable}>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
