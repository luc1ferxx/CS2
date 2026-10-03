import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KeyboardOverlay, KeyboardOverlaySlot } from "@/components/replay/KeyboardOverlay";
import { INPUT_BITS, type PlayerButtons } from "@/lib/player-inputs";

const NONE: PlayerButtons = {
  forward: false, back: false, left: false, right: false, attack: false, attack2: false, jump: false, duck: false, speed: false
};
const PRESSED_FILL = "rgba(74, 171, 247, 0.8)";
const RELEASED_FILL = "rgba(255,255,255,0.1)";

function keyNamed(text: string): HTMLElement {
  const key = [...document.querySelectorAll<HTMLElement>(".keyboard-overlay .key")].find((item) => item.textContent === text);
  if (!key) throw new Error(`no key ${text}`);
  return key;
}

function activeKeys(): string[] {
  return [...document.querySelectorAll(".keyboard-overlay .key.active")].map((key) => key.textContent ?? "");
}

function mouseButton(side: "left" | "right"): SVGPathElement {
  return document.querySelector(`.keyboard-overlay .mouse-button.${side}`) as SVGPathElement;
}

function mouseLabel(text: string): HTMLElement {
  return screen.getByText(text, { selector: ".mouse-labels span" });
}

describe("KeyboardOverlay", () => {
  it("lays out the reference's keys: W over S, SHIFT / CTRL / SPACE with their titles, and the mouse", () => {
    render(<KeyboardOverlay {...NONE} />);
    const rows = [...document.querySelectorAll(".keyboard-overlay .keyboard-grid > .grid-row")];
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.querySelectorAll(":scope > .grid-cell").length)).toEqual([4, 4, 4]);
    // Row 1: W in the third column, above S.
    expect(rows[0].querySelectorAll(":scope > .grid-cell")[2]).toHaveTextContent("W");
    expect([...rows[1].querySelectorAll(".key")].map((key) => key.textContent)).toEqual(["SHIFT", "A", "S", "D"]);
    expect([...rows[2].querySelectorAll(".key")].map((key) => key.textContent)).toEqual(["CTRL", "SPACE"]);
    expect(keyNamed("SHIFT")).toHaveClass("medium");
    expect(keyNamed("SHIFT")).toHaveAttribute("title", "静步");
    expect(keyNamed("CTRL")).toHaveClass("medium");
    expect(keyNamed("CTRL")).toHaveAttribute("title", "蹲下");
    expect(keyNamed("SPACE")).toHaveClass("wide");
    expect(keyNamed("SPACE")).toHaveAttribute("title", "跳跃");
    expect(keyNamed("SPACE").parentElement).toHaveClass("space-cell");

    const svg = document.querySelector(".keyboard-overlay .mouse-svg")!;
    expect(svg).toHaveAttribute("width", "48");
    expect(svg).toHaveAttribute("height", "72");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg.querySelector("line")).toHaveAttribute("x1", "24");
    expect(svg.querySelector("rect")).toHaveAttribute("rx", "3");
    expect(mouseLabel("左键")).toBeInTheDocument();
    expect(mouseLabel("右键")).toBeInTheDocument();
  });

  it("shows nothing pressed and says so", () => {
    render(<KeyboardOverlay {...NONE} playerName="xelex" />);
    expect(activeKeys()).toEqual([]);
    expect(mouseButton("left")).toHaveAttribute("fill", RELEASED_FILL);
    expect(mouseButton("right")).toHaveAttribute("fill", RELEASED_FILL);
    expect(mouseButton("left")).not.toHaveClass("active");
    expect(mouseLabel("左键")).not.toHaveClass("active");
    expect(screen.getByRole("img", { name: "xelex 没有按键" })).toHaveClass("keyboard-overlay");
  });

  it("marks the pressed keys, fills the pressed mouse half and lists them in the label", () => {
    render(<KeyboardOverlay {...NONE} right duck attack playerName="xelex" />);
    expect(activeKeys()).toEqual(["D", "CTRL"]);
    expect(mouseButton("left")).toHaveAttribute("fill", PRESSED_FILL);
    expect(mouseButton("left")).toHaveClass("active");
    expect(mouseButton("right")).toHaveAttribute("fill", RELEASED_FILL);
    expect(mouseLabel("左键")).toHaveClass("active");
    expect(mouseLabel("右键")).not.toHaveClass("active");
    expect(screen.getByRole("img", { name: "xelex 正在按：D、蹲、左键" })).toBeInTheDocument();
  });

  it("lights every key at once", () => {
    render(<KeyboardOverlay forward back left right attack attack2 jump duck speed />);
    expect(activeKeys()).toEqual(["W", "SHIFT", "A", "S", "D", "CTRL", "SPACE"]);
    expect(mouseButton("left")).toHaveAttribute("fill", PRESSED_FILL);
    expect(mouseButton("right")).toHaveAttribute("fill", PRESSED_FILL);
    expect(mouseLabel("右键")).toHaveClass("active");
    expect(screen.getByRole("img")).toHaveAccessibleName("正在按：W、A、S、D、静步、蹲、跳、左键、右键");
  });
});

describe("KeyboardOverlaySlot", () => {
  it("decodes the mask into the panel and stays an empty slot without one", () => {
    const { rerender } = render(
      <KeyboardOverlaySlot mask={INPUT_BITS.forward | INPUT_BITS.speed | INPUT_BITS.attack2 | 32} playerName="Alpha" />
    );
    expect(activeKeys()).toEqual(["W", "SHIFT"]);
    expect(mouseButton("right")).toHaveAttribute("fill", PRESSED_FILL);
    expect(screen.getByRole("img", { name: "Alpha 正在按：W、静步、右键" })).toBeInTheDocument();
    expect(document.querySelector(".keyboard-overlay")!.parentElement).toHaveClass("keyboard-overlay-slot");

    rerender(<KeyboardOverlaySlot mask={null} playerName="Alpha" />);
    expect(document.querySelector(".keyboard-overlay")).toBeNull();
    expect(document.querySelector(".keyboard-overlay-slot")).toBeEmptyDOMElement();
  });
});
