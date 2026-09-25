import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import DemoDetailError from "@/app/demos/[demoId]/error";
import AppError from "@/app/error";
import NotFound from "@/app/not-found";

describe("route-level fallback pages", () => {
  it("offers a reload and a way back when a page crashes", async () => {
    const user = userEvent.setup();
    const reset = vi.fn();
    render(<AppError error={new Error("boom")} reset={reset} />);

    expect(screen.getByRole("heading", { name: "页面出错了" })).toBeInTheDocument();
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "返回我的比赛" })).toHaveAttribute("href", "/dashboard");
  });

  it("keeps the topbar when the review workspace crashes", async () => {
    const user = userEvent.setup();
    const reset = vi.fn();
    render(<DemoDetailError error={new Error("bad replay shape")} reset={reset} />);

    expect(screen.getByRole("link", { name: "我的比赛" })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("heading", { name: "复盘页面出错了" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("explains a mistyped address in Chinese with a way back", () => {
    render(<NotFound />);

    expect(screen.getByRole("heading", { name: "找不到这个页面" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回我的比赛" })).toHaveAttribute("href", "/dashboard");
  });
});
