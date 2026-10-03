import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useFirstEntry } from "@/lib/use-first-entry";

function Workspace({ show, label }: { show: boolean; label: string }) {
  const entryRef = useFirstEntry<HTMLDivElement>();
  return show ? <div data-testid="workspace" className="review-layout" ref={entryRef}>{label}</div> : <p>loading</p>;
}

describe("useFirstEntry", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("marks the element at its first mount and takes the class off after the entry", () => {
    vi.useFakeTimers();
    const { getByTestId, rerender } = render(<Workspace show label="a" />);
    const workspace = getByTestId("workspace");
    expect(workspace).toHaveClass("review-layout", "is-entering");

    // A re-render (a playback frame, a round change) keeps the one-shot running, not restarted.
    rerender(<Workspace show label="b" />);
    act(() => vi.advanceTimersByTime(699));
    expect(workspace).toHaveClass("is-entering");
    act(() => vi.advanceTimersByTime(1));
    expect(workspace).not.toHaveClass("is-entering");
    expect(workspace).toHaveClass("review-layout");
  });

  it("does not play again when the element mounts a second time", () => {
    vi.useFakeTimers();
    const { getByTestId, rerender } = render(<Workspace show={false} label="a" />);
    rerender(<Workspace show label="a" />);
    expect(getByTestId("workspace")).toHaveClass("is-entering");
    act(() => vi.advanceTimersByTime(700));

    // A replay reload unmounts the workspace and mounts it again.
    rerender(<Workspace show={false} label="a" />);
    rerender(<Workspace show label="a" />);
    expect(getByTestId("workspace")).not.toHaveClass("is-entering");
  });
});
