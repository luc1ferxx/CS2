import type { Metadata } from "next";
import { AuthProvider } from "@/components/auth/AuthProvider";
import "./globals.css";
import "./product.css";

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
    <html lang="zh-CN">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
