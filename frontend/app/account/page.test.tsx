import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AccountPage from "@/app/account/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import type { AuthAccount } from "@/lib/auth";
import { PLAYER_PREFERENCE_KEY } from "@/lib/personal-review";

vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, deleteAccount: vi.fn() };
});

const STEAM_ID = "76561198000000001";
const STEAM_ACCOUNT: AuthAccount = { displayName: "xelex", avatarUrl: null, provider: "steam", steamId: STEAM_ID };
const DEV_ACCOUNT: AuthAccount = { displayName: "Local development", avatarUrl: null, provider: "development" };

const markSignedOut = vi.fn();

function mockAuth(account: AuthAccount) {
  vi.mocked(useAuth).mockReturnValue({
    state: { status: "authenticated", account, capabilities: { devTools: false, renderClips: false } },
    provider: "steam",
    refreshSession: vi.fn(async () => true),
    signIn: vi.fn(),
    signOut: vi.fn(async () => {}),
    markSignedOut
  });
}

async function confirmAccountDeletion() {
  const user = userEvent.setup();
  render(<AccountPage />);
  await user.click(screen.getByRole("button", { name: "删除账户…" }));
  const dialog = screen.getByRole("dialog", { name: "删除账户和全部数据？" });
  const confirm = within(dialog).getByRole("button", { name: "删除账户" });
  expect(confirm).toBeDisabled();
  await user.type(within(dialog).getByRole("textbox", { name: "请输入「删除账户」确认" }), "删除账户");
  expect(confirm).toBeEnabled();
  await user.click(confirm);
  return { user, dialog };
}

describe("AccountPage", () => {
  beforeEach(() => {
    mockAuth(STEAM_ACCOUNT);
    window.localStorage.setItem(`${PLAYER_PREFERENCE_KEY}:steam:${STEAM_ID}`, '{"version":1,"identity":"xelex"}');
    window.localStorage.setItem("unrelated", "kept");
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("lists the account, what is stored, and links to the library and the privacy page", () => {
    render(<AccountPage />);

    expect(screen.getByRole("heading", { level: 1, name: "账户与数据" })).toBeInTheDocument();
    const account = screen.getByRole("region", { name: "账户" });
    expect(within(account).getByText("昵称").nextSibling).toHaveTextContent("xelex");
    expect(within(account).getByText("登录方式").nextSibling).toHaveTextContent("Steam");
    expect(within(account).getByText("SteamID64").nextSibling).toHaveTextContent(STEAM_ID);
    const data = screen.getByRole("region", { name: "数据" });
    expect(within(data).getByRole("link", { name: "我的比赛" })).toHaveAttribute("href", "/dashboard");
    expect(within(data).getByRole("link", { name: "隐私说明" })).toHaveAttribute("href", "/privacy");
    // The invite list lives outside the account and survives its deletion; both panels say so.
    expect(data).toHaveTextContent("邀请名单（你的 SteamID64）由站长保存在服务器配置里");
    expect(screen.getByRole("region", { name: "删除账户" })).toHaveTextContent("删除账户不会把你移出邀请名单");
    // The top bar name is the way here, and says so.
    expect(screen.getByRole("link", { name: "xelex" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/本站与 Valve Corporation 无关联/)).toBeInTheDocument();
  });

  it("deletes the account after the typed confirmation, clears this browser and shows the public done page", async () => {
    vi.mocked(api.deleteAccount).mockResolvedValueOnce(undefined);

    await confirmAccountDeletion();

    expect(api.deleteAccount).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "账户已删除" })).toBeInTheDocument();
    expect(markSignedOut).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(`${PLAYER_PREFERENCE_KEY}:steam:${STEAM_ID}`)).toBeNull();
    expect(window.localStorage.getItem("unrelated")).toBe("kept");
    expect(screen.getByText("你的 SteamID64 仍在站长的邀请名单里；如需移出，请联系站长。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看隐私说明" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("link", { name: "返回登录页" })).toHaveAttribute("href", "/dashboard");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除账户…" })).not.toBeInTheDocument();
  });

  it("never reports a refused or unauthenticated delete as done", async () => {
    vi.mocked(api.deleteAccount)
      .mockRejectedValueOnce(new api.ApiError(409, "unavailable", "account_deletion_unavailable"))
      .mockRejectedValueOnce(new api.ApiError(401, "Not authenticated"));

    const { user, dialog } = await confirmAccountDeletion();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("这个环境不提供账户删除。");
    await user.click(within(dialog).getByRole("button", { name: "删除账户" }));
    await vi.waitFor(() => expect(api.deleteAccount).toHaveBeenCalledTimes(2));

    expect(screen.queryByRole("heading", { name: "账户已删除" })).not.toBeInTheDocument();
    expect(markSignedOut).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(`${PLAYER_PREFERENCE_KEY}:steam:${STEAM_ID}`)).not.toBeNull();
  });

  it("explains that the local development account cannot be deleted and points at single matches", () => {
    mockAuth(DEV_ACCOUNT);
    render(<AccountPage />);

    const danger = screen.getByRole("region", { name: "删除账户" });
    expect(danger).toHaveTextContent("账户删除只在使用 Steam 登录时提供");
    expect(within(danger).getByRole("link", { name: "我的比赛" })).toHaveAttribute("href", "/dashboard");
    expect(screen.queryByRole("button", { name: /删除账户/ })).not.toBeInTheDocument();
    expect(screen.getByText("登录方式").nextSibling).toHaveTextContent("本地开发账户");
    expect(screen.queryByText("SteamID64")).not.toBeInTheDocument();
  });
});
