import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/feedback/ConfirmDialog";

function Harness({
  onConfirm = vi.fn(),
  busy = false,
  error = null,
  requireText
}: {
  onConfirm?: () => void;
  busy?: boolean;
  error?: string | null;
  requireText?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>删除…</button>
      <ConfirmDialog
        open={open}
        title="永久删除这场比赛？"
        confirmLabel="永久删除"
        busy={busy}
        busyLabel="正在删除…"
        error={error}
        requireText={requireText}
        onConfirm={onConfirm}
        onCancel={() => setOpen(false)}
      >
        <p>此操作无法撤销。</p>
      </ConfirmDialog>
    </>
  );
}

describe("ConfirmDialog", () => {
  it("renders nothing until opened", () => {
    render(<Harness />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("is a labelled modal that starts on cancel, traps Tab and returns focus on Esc", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    const opener = screen.getByRole("button", { name: "删除…" });
    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: "永久删除这场比赛？" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent("此操作无法撤销。");
    const confirm = within(dialog).getByRole("button", { name: "永久删除" });
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    expect(confirm).toHaveClass("danger-button");
    expect(cancel).toHaveClass("secondary-button");
    expect(cancel).toHaveFocus();

    await user.tab();
    expect(confirm).toHaveFocus();
    await user.tab({ shift: true });
    expect(cancel).toHaveFocus();
    await user.tab();
    expect(confirm).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("closes on 取消 and confirms on the red button", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: "删除…" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除…" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "删除…" }));
    await user.click(screen.getByRole("button", { name: "永久删除" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("keeps confirm off until the typed text matches exactly", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} requireText="删除账户" />);
    await user.click(screen.getByRole("button", { name: "删除…" }));

    const input = screen.getByRole("textbox", { name: "请输入「删除账户」确认" });
    expect(input).toHaveFocus();
    const confirm = screen.getByRole("button", { name: "永久删除" });
    expect(confirm).toBeDisabled();
    await user.type(input, "删除账户 ");
    expect(confirm).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(onConfirm).not.toHaveBeenCalled();
    await user.clear(input);
    await user.type(input, "删除账户");
    expect(confirm).toBeEnabled();
    await user.keyboard("{Enter}");
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables both buttons while busy, ignores Esc, and shows an error inside", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Harness />);
    await user.click(screen.getByRole("button", { name: "删除…" }));

    rerender(<Harness busy />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "正在删除…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
    expect(dialog).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    rerender(<Harness error="服务暂时出错，请稍后重试。" />);
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("服务暂时出错，请稍后重试。");
    expect(screen.getByRole("button", { name: "永久删除" })).toBeEnabled();
  });

  it("keeps the trap and Esc working after a failed request leaves the dialog open", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <>
        <Harness />
        <a href="/privacy">隐私说明</a>
      </>
    );
    const opener = screen.getByRole("button", { name: "删除…" });
    await user.click(opener);
    rerender(
      <>
        <Harness busy />
        <a href="/privacy">隐私说明</a>
      </>
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus();

    rerender(
      <>
        <Harness error="服务暂时出错，请稍后重试。" />
        <a href="/privacy">隐私说明</a>
      </>
    );
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "永久删除" });
    // Focus goes back to a real control, not the panel.
    expect(cancel).toHaveFocus();
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await user.tab({ shift: true });
    expect(cancel).toHaveFocus();

    // Even if focus is left on the panel itself, Shift+Tab stays inside.
    dialog.focus();
    await user.tab({ shift: true });
    expect(cancel).toHaveFocus();
    dialog.focus();
    await user.tab();
    expect(confirm).toHaveFocus();

    // And if it escapes the dialog altogether, Esc still closes it.
    screen.getByRole("link", { name: "隐私说明" }).focus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("keeps page shortcuts from seeing keys typed in the dialog", async () => {
    const user = userEvent.setup();
    const pageKeys = vi.fn();
    window.addEventListener("keydown", pageKeys);
    render(<Harness requireText="删除账户" />);
    await user.click(screen.getByRole("button", { name: "删除…" }));
    pageKeys.mockClear();
    await user.keyboard("k ");
    expect(pageKeys).not.toHaveBeenCalled();
    window.removeEventListener("keydown", pageKeys);
  });
});
