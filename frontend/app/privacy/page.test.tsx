import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PrivacyPage from "@/app/privacy/page";
import { DATA_REGION_FALLBACK, privacyContact } from "@/lib/privacy";

// No AuthProvider and no useAuth mock: the page must render for a visitor with no session.
describe("PrivacyPage", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("renders signed out with every section, the Valve disclaimer and the fallbacks", () => {
    vi.stubEnv("NEXT_PUBLIC_PRIVACY_CONTACT", "");
    vi.stubEnv("NEXT_PUBLIC_DATA_REGION", "");
    render(<PrivacyPage />);

    expect(screen.getByRole("heading", { level: 1, name: "隐私说明" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "这是什么",
      "登录时我们拿到什么",
      "你上传的比赛",
      "Steam 比赛记录（可选）",
      "Cookie 和浏览器存储",
      "日志",
      "数据存在哪里、谁能接触",
      "保存多久",
      "删除",
      "联系",
      "与 Valve 无关联"
    ]);
    expect(screen.getByRole("region", { name: "与 Valve 无关联" })).toHaveTextContent(
      "本站与 Valve Corporation 无关联。Counter-Strike、CS2 和 Steam 是 Valve 的商标。"
    );
    expect(screen.getByRole("region", { name: "你上传的比赛" })).toHaveTextContent(
      ".dem 文件里包含同场所有玩家的 SteamID64、游戏内昵称、位置和击杀记录。"
    );
    expect(screen.getByRole("region", { name: "登录时我们拿到什么" })).toHaveTextContent(
      "删除账户不会把你移出名单；如需移出，请联系站长。"
    );
    expect(screen.getByRole("region", { name: "保存多久" })).toHaveTextContent("数据不会因此自动删除");
    expect(screen.getByRole("region", { name: "Cookie 和浏览器存储" })).toHaveTextContent("__Host-cs2_session");
    expect(screen.getByRole("region", { name: "数据存在哪里、谁能接触" })).toHaveTextContent(DATA_REGION_FALLBACK);
    const contact = screen.getByRole("region", { name: "联系" });
    expect(contact).toHaveTextContent("本站没有公开联系方式。受邀用户请直接联系邀请你的站长；同场的其他玩家可以请上传这场比赛的人删除它。");
    expect(within(contact).queryByRole("link")).not.toBeInTheDocument();
    const deletion = screen.getByRole("region", { name: "删除" });
    expect(within(deletion).getByRole("link", { name: "账户与数据" })).toHaveAttribute("href", "/account");
    expect(deletion).toHaveTextContent("删除比赛不会恢复当天的上传次数。");
    expect(screen.getByText("最近更新：2026-09-26")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "隐私说明" })).toHaveAttribute("aria-current", "page");
  });

  it("uses the configured region and contact", () => {
    vi.stubEnv("NEXT_PUBLIC_DATA_REGION", "德国法兰克福");
    vi.stubEnv("NEXT_PUBLIC_PRIVACY_CONTACT", "owner@example.com");
    render(<PrivacyPage />);

    expect(screen.getByRole("region", { name: "数据存在哪里、谁能接触" })).toHaveTextContent("网站服务器（VPS）：德国法兰克福。");
    const link = within(screen.getByRole("region", { name: "联系" })).getByRole("link", { name: "owner@example.com" });
    expect(link).toHaveAttribute("href", "mailto:owner@example.com");
  });

  it("turns the contact setting into a mailto link, a web link or plain text", () => {
    expect(privacyContact(undefined)).toBeNull();
    expect(privacyContact("  ")).toBeNull();
    expect(privacyContact("owner@example.com")).toEqual({
      kind: "link", href: "mailto:owner@example.com", label: "owner@example.com", external: false
    });
    expect(privacyContact("mailto:owner@example.com?subject=privacy")).toEqual({
      kind: "link", href: "mailto:owner@example.com?subject=privacy", label: "owner@example.com", external: false
    });
    expect(privacyContact("https://forms.example.com/privacy")).toEqual({
      kind: "link", href: "https://forms.example.com/privacy", label: "https://forms.example.com/privacy", external: true
    });
    expect(privacyContact("javascript:alert(1)")).toEqual({ kind: "text", label: "javascript:alert(1)" });
    expect(privacyContact("微信 xelex")).toEqual({ kind: "text", label: "微信 xelex" });
  });
});
