import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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
    await user.click(screen.getByRole("button", { name: "通过 Steam 登录" }));
    expect(signIn).toHaveBeenCalledTimes(1);
  });

  it("uses the generic sign-in label for the OIDC provider", () => {
    mockAuth({ status: "anonymous" }, "oidc");
    renderBoundary();
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
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
    expect(screen.getByText("api unreachable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新连接" }));
    expect(refreshSession).toHaveBeenCalledTimes(1);
  });

  it("holds the page while the session is still being checked", () => {
    mockAuth({ status: "checking" });
    renderBoundary();
    expect(screen.getByRole("heading", { name: "正在打开比赛库" })).toBeInTheDocument();
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();
  });
});
