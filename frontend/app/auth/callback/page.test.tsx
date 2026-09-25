import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import AuthCallbackPage from "@/app/auth/callback/page";
import { useAuth } from "@/components/auth/AuthProvider";
import type { AuthState } from "@/lib/auth";

// One router object for the whole file: a fresh one per render would re-run the page's effect.
const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

const signIn = vi.fn();
const signOut = vi.fn(async () => {});

function mockAuth(refreshSession: () => Promise<boolean>, state: AuthState = { status: "checking" }) {
  vi.mocked(useAuth).mockReturnValue({
    state,
    provider: "steam",
    refreshSession,
    signIn,
    signOut
  });
}

function openCallback(search: string) {
  window.history.pushState({}, "", `/auth/callback${search}`);
  return render(<AuthCallbackPage />);
}

describe("AuthCallbackPage", () => {
  afterEach(() => {
    window.history.pushState({}, "", "/");
  });

  it("tells an account outside the beta it is not invited, without a sign-in loop", async () => {
    const user = userEvent.setup();
    const refreshSession = vi.fn(async () => false);
    mockAuth(refreshSession);

    openCallback("?error=not_invited");

    expect(await screen.findByRole("heading", { name: "暂未开放" })).toBeInTheDocument();
    expect(screen.getByText("这个 Steam 账号还没有获得内测资格。")).toBeInTheDocument();
    expect(screen.getByText(/请先在 Steam 网站退出当前账号/)).toBeInTheDocument();
    // No contact route is configured in tests, so none is offered.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    // Nothing signs in again on its own; switching accounts is the player's call.
    expect(refreshSession).not.toHaveBeenCalled();
    expect(signIn).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "使用其他 Steam 账号登录" }));
    expect(signIn).toHaveBeenCalledWith("/dashboard");
  });

  it("returns a signed-in user to the requested page", async () => {
    const refreshSession = vi.fn(async () => true);
    mockAuth(refreshSession);

    openCallback("?return_to=%2Fdemos%2Fdemo-1");

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/demos/demo-1"));
    expect(screen.queryByRole("heading", { name: "暂未开放" })).not.toBeInTheDocument();
  });

  it("still offers sign-in again when the callback made no session for another reason", async () => {
    const user = userEvent.setup();
    const refreshSession = vi.fn(async () => false);
    mockAuth(refreshSession);

    openCallback("?error=cancelled");

    await user.click(await screen.findByRole("button", { name: "重新通过 Steam 登录" }));
    expect(screen.getByRole("heading", { name: "登录没有完成" })).toBeInTheDocument();
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(signIn).toHaveBeenCalledWith("/dashboard");
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("says the sign-in is being completed, in Chinese", () => {
    mockAuth(vi.fn(() => new Promise<boolean>(() => {})));

    openCallback("?return_to=%2Fdashboard");

    expect(screen.getByRole("heading", { name: "正在完成登录…" })).toBeInTheDocument();
    expect(screen.getByText("正在确认登录状态，马上回到你的比赛。")).toBeInTheDocument();
  });

  it("offers a retry, not a new sign-in, when the network failed", async () => {
    const user = userEvent.setup();
    const refreshSession = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    mockAuth(refreshSession, { status: "error", message: "Failed to fetch" });

    openCallback("?return_to=%2Fdemos%2Fdemo-1");

    expect(await screen.findByRole("heading", { name: "暂时无法连接" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /登录/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/demos/demo-1"));
    expect(refreshSession).toHaveBeenCalledTimes(2);
    expect(signIn).not.toHaveBeenCalled();
  });
});
