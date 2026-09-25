import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "我的比赛"
};

export default function DashboardLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
