import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "账户与数据"
};

export default function AccountLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
