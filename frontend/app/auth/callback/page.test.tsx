import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import AuthCallbackPage from "@/app/auth/callback/page";
import { useAuth } from "@/components/auth/AuthProvider";

// One router object for the whole file: a fresh one per render would re-run the page's effect.
const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

const signIn = vi.fn();
const signOut = vi.fn(async () => {});

function mockAuth(refreshSession: () => Promise<boolean>) {
  vi.mocked(useAuth).mockReturnValue({
    state: { status: "checking" },
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
    const refreshSession = vi.fn(async () => false);
    mockAuth(refreshSession);

    openCallback("?error=not_invited");

    expect(await screen.findByRole("heading", { name: "暂未开放" })).toBeInTheDocument();
    expect(screen.getByText("这个 Steam 账号还没有获得内测资格。如需参加内测，请联系我们。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回首页" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(refreshSession).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
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

    await user.click(await screen.findByRole("button", { name: "Sign in with Steam again" }));
    expect(screen.getByRole("heading", { name: "Sign-in could not be completed" })).toBeInTheDocument();
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(signIn).toHaveBeenCalledWith("/dashboard");
    expect(router.replace).not.toHaveBeenCalled();
  });
});
