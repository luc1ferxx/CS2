"use client";

import { useState } from "react";

import type { PlayerMatch, personalReviewSummary } from "@/lib/personal-review";
import type { ReplayPlayer } from "@/types/replay";

interface PersonalReviewPanelProps {
  preferredIdentity: string;
  match: PlayerMatch;
  players: ReplayPlayer[];
  selectedPlayer: ReplayPlayer | null;
  summary: ReturnType<typeof personalReviewSummary>;
  preferenceSaved: boolean;
  onSaveIdentity: (identity: string) => void;
  onSelectPlayer: (playerId: string | null) => void;
  onSeek: (tick: number) => void;
}

export function PersonalReviewPanel({
  preferredIdentity, match, players, selectedPlayer, summary, preferenceSaved,
  onSaveIdentity, onSelectPlayer, onSeek
}: PersonalReviewPanelProps) {
  const [draftIdentity, setDraftIdentity] = useState(preferredIdentity);
  const reviewingPreferred = selectedPlayer?.id === match.player?.id;
  const needsSelection = !selectedPlayer || match.status !== "matched";

  return (
    <section className="panel personal-review-panel" aria-label="Personal review">
      <div className="personal-review-heading personal-review-inline">
        <div>
          <h2>{selectedPlayer ? <>正在复盘 <strong>{selectedPlayer.name}</strong></> : "选择复盘玩家"}</h2>
          {needsSelection ? (
            <p className="personal-review-status" role="status">
              {match.status === "missing"
                ? `这场比赛中未找到 ${preferredIdentity}，请选择玩家或修改身份。`
                : match.status === "ambiguous"
                  ? `有多位玩家使用 ${preferredIdentity}，请按 Steam ID 确认。`
                  : "选择玩家后，建议和时间轴会跟随该玩家。"}
            </p>
          ) : null}
        </div>
        {selectedPlayer ? (
          <div className="personal-review-metrics" aria-label="Selected player review summary">
            <span><strong>{summary.findingCount}</strong> 条建议</span>
            <span><strong>{summary.highPriorityCount}</strong> 条优先复核</span>
          </div>
        ) : null}
        <button className="secondary-button compact-button personal-first-finding" type="button"
          aria-label="First finding" disabled={summary.firstFindingTick === null}
          onClick={() => { if (summary.firstFindingTick !== null) onSeek(summary.firstFindingTick); }}>
          查看首条建议
        </button>
      </div>
      <details className="personal-review-settings" open={needsSelection ? true : undefined}>
        <summary>切换 / 身份设置</summary>
        <div className="personal-review-controls">
          <form onSubmit={(event) => { event.preventDefault(); onSaveIdentity(draftIdentity); }}>
            <label htmlFor="preferred-player-identity">我的游戏名或 Steam ID</label>
            <div className="personal-identity-input">
              <input id="preferred-player-identity" value={draftIdentity} required maxLength={128}
                onChange={(event) => setDraftIdentity(event.target.value)} autoComplete="off" />
              <button className="secondary-button compact-button" type="submit" disabled={!draftIdentity.trim()}>保存身份</button>
            </div>
            {!preferenceSaved ? <small role="status">浏览器无法保存设置，本次访问仍可使用。</small> : null}
          </form>
          <label className="personal-player-select">
            <span>当前复盘玩家</span>
            <select aria-label="Player to review" value={selectedPlayer?.id ?? ""}
              onChange={(event) => onSelectPlayer(event.target.value || null)}>
              <option value="">选择玩家</option>
              {players.map((player) => (
                <option key={player.id} value={player.id}>{player.name} · {player.id}</option>
              ))}
            </select>
          </label>
        </div>
        <p className="personal-review-status">
          {reviewingPreferred ? `已匹配保存的身份 ${preferredIdentity}。` : `已保存的身份为 ${preferredIdentity}。`}
          建议和事件标记跟随当前复盘玩家。
        </p>
      </details>
    </section>
  );
}
