// Visual design adapted from bugkingZHT/cs2-sandbox KeyboardOverlay.vue (MIT, Copyright (c) 2026 huN7er).
// Same markup and values as the reference (styles in product.css, "S16 KEYS"): a 4 x 3 key grid with
// W over S, SHIFT / CTRL / SPACE, and the SVG mouse with its 左键 / 右键 labels.
import { memo } from "react";

import { decodeButtons, describePressedKeys, type PlayerButtons } from "@/lib/player-inputs";

export interface KeyboardOverlayProps extends PlayerButtons {
  // For the accessible label: "xelex 正在按：D、蹲、左键".
  playerName?: string | null;
}

const PRESSED_FILL = "rgba(74, 171, 247, 0.8)";
const RELEASED_FILL = "rgba(255,255,255,0.1)";

function keyClass(pressed: boolean, size?: "medium" | "wide"): string {
  return `key${size ? ` ${size}` : ""}${pressed ? " active" : ""}`;
}

// Memoised on the nine booleans: it only re-renders when a key goes down or up.
export const KeyboardOverlay = memo(function KeyboardOverlay({
  forward,
  back,
  left,
  right,
  attack,
  attack2,
  jump,
  duck,
  speed,
  playerName
}: KeyboardOverlayProps) {
  const label = describePressedKeys({ forward, back, left, right, attack, attack2, jump, duck, speed }, playerName);
  return (
    <div className="keyboard-overlay" role="img" aria-label={label}>
      <div className="wasd-section">
        <div className="keyboard-grid">
          <div className="grid-row">
            <div className="grid-cell" />
            <div className="grid-cell" />
            <div className="grid-cell">
              <div className={keyClass(forward)}>W</div>
            </div>
            <div className="grid-cell" />
          </div>
          <div className="grid-row">
            <div className="grid-cell">
              <div className={keyClass(speed, "medium")} title="静步">SHIFT</div>
            </div>
            <div className="grid-cell">
              <div className={keyClass(left)}>A</div>
            </div>
            <div className="grid-cell">
              <div className={keyClass(back)}>S</div>
            </div>
            <div className="grid-cell">
              <div className={keyClass(right)}>D</div>
            </div>
          </div>
          <div className="grid-row">
            <div className="grid-cell">
              <div className={keyClass(duck, "medium")} title="蹲下">CTRL</div>
            </div>
            <div className="grid-cell space-cell">
              <div className={keyClass(jump, "wide")} title="跳跃">SPACE</div>
            </div>
            <div className="grid-cell" />
            <div className="grid-cell" />
          </div>
        </div>
      </div>

      <div className="mouse-section">
        <div className="mouse-container">
          <svg className="mouse-svg" width="48" height="72" viewBox="0 0 48 72" aria-hidden="true" focusable="false">
            <path
              d="M8 24 C8 12 16 4 24 4 C32 4 40 12 40 24 L40 52 C40 62 32 68 24 68 C16 68 8 62 8 52 Z"
              fill="rgba(0,0,0,0.6)"
              stroke="rgba(255,255,255,0.4)"
              strokeWidth="1.5"
            />
            <line x1="24" y1="4" x2="24" y2="36" stroke="rgba(255,255,255,0.3)" strokeWidth="1" />
            <path
              className={`mouse-button left${attack ? " active" : ""}`}
              d="M8 24 C8 12 16 4 24 4 L24 36 L8 36 Z"
              fill={attack ? PRESSED_FILL : RELEASED_FILL}
            />
            <path
              className={`mouse-button right${attack2 ? " active" : ""}`}
              d="M24 4 C32 4 40 12 40 24 L40 36 L24 36 Z"
              fill={attack2 ? PRESSED_FILL : RELEASED_FILL}
            />
            <rect x="21" y="16" width="6" height="12" rx="3" fill="rgba(255,255,255,0.3)" />
          </svg>
          <div className="mouse-labels">
            <span className={attack ? "active" : undefined}>左键</span>
            <span className={attack2 ? "active" : undefined}>右键</span>
          </div>
        </div>
      </div>
    </div>
  );
});

// Where ReplayViewer puts the panel, never over the map: at the bottom of the followed player's team
// roster in the workbench, under the map in the stacked layout. Re-renders only when the mask or the
// name changes; the viewer keys it on the player's id. With no mask the slot stays empty and keeps its
// height, so nothing around it moves when the followed player dies.
export const KeyboardOverlaySlot = memo(function KeyboardOverlaySlot({
  mask,
  playerName
}: {
  mask: number | null;
  playerName: string | null;
}) {
  return (
    <div className="keyboard-overlay-slot">
      {mask !== null ? <KeyboardOverlay {...decodeButtons(mask)} playerName={playerName} /> : null}
    </div>
  );
});
