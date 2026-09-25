import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { useAuth } from "@/components/auth/AuthProvider";
import type { AuthState } from "@/lib/auth";

vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

const signIn = vi.fn();
const refreshSession = vi.fn(async () => true);
const signOut = vi.fn(async () => {});

function mockAuth(state: AuthState, provider: "steam" | "oidc" = "steam") {
  vi.mocked(useAuth).mockReturnValue({ state, provider, refreshSession, signIn, signOut });
}

function renderBoundary() {
  return render(
    <AuthBoundary>
      <p>workspace</p>
    </AuthBoundary>
  );
}

describe("AuthBoundary", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the page once the session is authenticated", () => {
    mockAuth({ status: "authenticated", account: { displayName: "xelex", avatarUrl: null, provider: "steam" } });
    renderBoundary();
    expect(screen.getByText("workspace")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers Steam sign-in to an anonymous visitor", async () => {
    const user = userEvent.setup();
    mockAuth({ status: "anonymous" });
    renderBoundary();
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();
    // One line on what this is and what Steam shares, before the redirect.
    expect(screen.getByText("CS2 Demo Coach")).toBeInTheDocument();
    expect(screen.getByText("内测")).toBeInTheDocument();
    expect(screen.getByText(/上传 CS2 比赛录像（.dem）/)).toBeInTheDocument();
    expect(screen.getByText(/steamcommunity\.com.*不会获得你的密码/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "通过 Steam 登录" }));
    expect(signIn).toHaveBeenCalledTimes(1);
  });

  it("uses the generic sign-in label for the OIDC provider", () => {
    mockAuth({ status: "anonymous" }, "oidc");
    renderBoundary();
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
    expect(screen.queryByText(/steamcommunity/)).not.toBeInTheDocument();
  });

  it("explains an expired session and still offers sign-in", () => {
    mockAuth({ status: "expired" });
    renderBoundary();
    expect(screen.getByRole("heading", { name: "登录已过期" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "通过 Steam 登录" })).toBeInTheDocument();
  });

  it("shows the connection error and lets the user reconnect", async () => {
    const user = userEvent.setup();
    mockAuth({ status: "error", message: "api unreachable" });
    renderBoundary();
    expect(screen.getByRole("heading", { name: "暂时无法连接" })).toBeInTheDocument();
    expect(screen.getByText("网络连接中断，请检查网络后重新连接。")).toBeInTheDocument();
    expect(screen.queryByText(/应用已启动/)).not.toBeInTheDocument();
    expect(screen.getByText("api unreachable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新连接" }));
    expect(refreshSession).toHaveBeenCalledTimes(1);
  });

  it("holds the page in the app shell while the session is still being checked", async () => {
    vi.useFakeTimers();
    mockAuth({ status: "checking" });
    renderBoundary();

    // The shell, not a centered card about the library, and no page content yet.
    expect(screen.getByText("CS2 Demo Coach")).toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();
    expect(document.querySelector(".page")).toHaveAttribute("aria-busy", "true");
    // A fast check never flashes a message.
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(screen.getByRole("status")).toHaveTextContent("正在连接…");
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();
  });
});
