import { act, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { useSlidingIndicator } from "@/lib/use-sliding-indicator";

// jsdom has no layout: each button sits 100 px after the previous one and is 10 px per character wide.
const realOffsetLeft = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetLeft");
const realOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "offsetLeft", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.tagName !== "BUTTON" || !this.parentElement) return 0;
      return [...this.parentElement.querySelectorAll("button")].indexOf(this as HTMLButtonElement) * 100;
    }
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get(this: HTMLElement) {
      return this.tagName === "BUTTON" ? (this.textContent?.length ?? 0) * 10 : 0;
    }
  });
});
afterAll(() => {
  if (realOffsetLeft) Object.defineProperty(HTMLElement.prototype, "offsetLeft", realOffsetLeft);
  if (realOffsetWidth) Object.defineProperty(HTMLElement.prototype, "offsetWidth", realOffsetWidth);
});

// Buttons are keyed by their first word, so a count added to a label keeps the same button.
function Segments({ selected, labels, shown = true }: { selected: string; labels: string[]; shown?: boolean }) {
  const { groupRef, indicatorRef } = useSlidingIndicator(selected, labels.join("|"));
  if (!shown) return null;
  return (
    <div ref={groupRef}>
      <span data-testid="marker" ref={indicatorRef} />
      {labels.map((label) => {
        const key = label.split(" ")[0];
        return <button key={key} type="button" aria-pressed={key === selected}>{label}</button>;
      })}
    </div>
  );
}

// Every style the marker passed through: a placement without animation goes through "transition: none".
function watchStyle(element: HTMLElement) {
  const states: string[] = [];
  const observer = new MutationObserver(() => undefined);
  observer.observe(element, { attributes: true, attributeFilter: ["style"], attributeOldValue: true });
  return {
    jumped() {
      states.push(...observer.takeRecords().map((record) => record.oldValue ?? ""));
      const jumped = states.some((state) => state.includes("transition: none"));
      states.length = 0;
      return jumped;
    }
  };
}

const marker = () => screen.getByTestId("marker");

describe("useSlidingIndicator", () => {
  it("puts the marker on the pressed button at first paint, with no animation", () => {
    render(<Segments selected="地图" labels={["战术回放", "地图", "第一人称"]} />);
    expect(marker().style.transform).toBe("translateX(100px)");
    expect(marker().style.width).toBe("20px");
    // The transition is back for the next move.
    expect(marker().style.transition).toBe("");
  });

  it("slides to a newly pressed button with the transition, and jumps when only the labels change", () => {
    const view = render(<Segments selected="当前回合" labels={["当前回合", "全部回合"]} />);
    const style = watchStyle(marker());

    view.rerender(<Segments selected="全部回合" labels={["当前回合", "全部回合"]} />);
    expect(marker().style.transform).toBe("translateX(100px)");
    expect(style.jumped()).toBe(false);

    // A count in a label changed its width: placed at once, not animated.
    view.rerender(<Segments selected="全部回合" labels={["当前回合", "全部回合 20 条"]} />);
    expect(marker().style.width).toBe(`${"全部回合 20 条".length * 10}px`);
    expect(style.jumped()).toBe(true);
  });

  it("re-places without animation on a window resize", () => {
    render(<Segments selected="b" labels={["a", "b"]} />);
    const style = watchStyle(marker());
    marker().style.transform = "translateX(0px)";
    style.jumped();

    act(() => { window.dispatchEvent(new Event("resize")); });
    expect(marker().style.transform).toBe("translateX(100px)");
    expect(style.jumped()).toBe(true);
  });

  it("places a group that mounts after the hook, as it attaches", () => {
    const view = render(<Segments selected="b" labels={["a", "b"]} shown={false} />);
    view.rerender(<Segments selected="b" labels={["a", "b"]} />);
    expect(marker().style.transform).toBe("translateX(100px)");
    expect(marker().style.width).toBe("10px");
  });
});
