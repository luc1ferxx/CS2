"use client";

import { memo, useState } from "react";

import type { ReviewIdentityMatch, personalReviewSummary } from "@/lib/personal-review";
import type { ReplayPlayer } from "@/types/replay";

export const PLAYER_PICKER_ID = "review-player-picker";

interface PersonalReviewPanelProps {
  savedIdentity: string;
  match: ReviewIdentityMatch;
  players: ReplayPlayer[];
  selectedPlayer: ReplayPlayer | null;
  summary: ReturnType<typeof personalReviewSummary>;
  preferenceSaved: boolean;
  onSaveIdentity: (identity: string) => void;
  onSelectPlayer: (playerId: string | null) => void;
  onSeek: (tick: number, eventId: string) => void;
}

export const PersonalReviewPanel = memo(function PersonalReviewPanel({
  savedIdentity, match, players, selectedPlayer, summary, preferenceSaved,
  onSaveIdentity, onSelectPlayer, onSeek
}: PersonalReviewPanelProps) {
  const [draftIdentity, setDraftIdentity] = useState(savedIdentity);
  const ownPlayer = match.status === "matched" ? match.player : null;
  const reviewingSomeoneElse = selectedPlayer !== null && selectedPlayer.id !== ownPlayer?.id;
  const topFinding = summary.topFinding;

  return (
    <section className="panel personal-review-panel" aria-label="复盘玩家">
      <div className="personal-review-heading personal-review-inline">
        <div>
          <h2>{selectedPlayer ? <>正在复盘 <strong className="personal-review-name" title={selectedPlayer.name}>{selectedPlayer.name}</strong></> : "选择你在这场比赛中的玩家"}</h2>
          {selectedPlayer || !selectionHint(match) ? null : (
            <p className="personal-review-status" role="status">{selectionHint(match)}</p>
          )}
        </div>
        {selectedPlayer ? (
          <div className="personal-review-metrics" aria-label="复盘概况">
            <span className="personal-review-count">{summary.findingCount} 条建议</span>
            <span>{summary.priorityCount} 条值得优先回看</span>
          </div>
        ) : null}
        {selectedPlayer ? (
          <button className="secondary-button compact-button personal-first-finding" type="button"
            disabled={topFinding === null}
            onClick={() => { if (topFinding) onSeek(topFinding.tick_start, topFinding.id); }}>
            查看最值得回看的一条
          </button>
        ) : null}
        <details className="personal-review-settings">
          <summary>切换 / 身份设置</summary>
          <div className="popover personal-review-popover">
            <div className="personal-review-controls">
              <form onSubmit={(event) => { event.preventDefault(); onSaveIdentity(draftIdentity); }}>
                <label htmlFor="preferred-player-identity">我的游戏名或 Steam ID</label>
                <div className="personal-identity-input">
                  <input id="preferred-player-identity" value={draftIdentity} required maxLength={128}
                    placeholder="游戏名或 Steam ID"
                    onChange={(event) => setDraftIdentity(event.target.value)} autoComplete="off" />
                  <button className="secondary-button compact-button" type="submit" disabled={!draftIdentity.trim()}>保存身份</button>
                </div>
                {!preferenceSaved ? <small role="status">浏览器无法保存设置，本次访问仍可使用。</small> : null}
              </form>
              <div className="personal-player-select">
                <label htmlFor="review-player-select">当前复盘玩家</label>
                <div className="personal-player-select-row">
                  <select id="review-player-select" value={selectedPlayer?.id ?? ""}
                    onChange={(event) => onSelectPlayer(event.target.value || null)}>
                    <option value="">选择玩家</option>
                    {players.map((player) => (
                      <option key={player.id} value={player.id}>{player.name}（{player.id}）</option>
                    ))}
                  </select>
                  {selectedPlayer && reviewingSomeoneElse ? (
                    <button className="secondary-button compact-button" type="button"
                      onClick={() => onSaveIdentity(selectedPlayer.id)}>
                      设为我的玩家
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
            <p className="personal-review-status">
              {identityStatus(match, reviewingSomeoneElse)}
              建议和事件标记跟随当前复盘玩家。
            </p>
          </div>
        </details>
      </div>
      {selectedPlayer ? null : (
        <PlayerPicker players={players} highlighted={match.status === "ambiguous" ? match.candidates : []}
          onChoose={onSaveIdentity} />
      )}
    </section>
  );
});

function PlayerPicker({ players, highlighted, onChoose }: {
  players: ReplayPlayer[];
  highlighted: ReplayPlayer[];
  onChoose: (playerId: string) => void;
}) {
  const nameCounts = new Map<string, number>();
  for (const player of players) {
    const key = player.name.trim().toLowerCase();
    nameCounts.set(key, (nameCounts.get(key) ?? 0) + 1);
  }
  const highlightedIds = new Set(highlighted.map((player) => player.id));
  const sides = (["T", "CT"] as const)
    .map((side) => ({ side, players: players.filter((player) => player.side === side) }))
    .filter((group) => group.players.length > 0);

  return (
    <div id={PLAYER_PICKER_ID} className="personal-player-picker" role="group" aria-label="选择你在这场比赛中的玩家">
      {sides.map((group) => (
        <div key={group.side} className={`personal-player-picker-side side-${group.side.toLowerCase()}`}>
          <span className="personal-player-picker-label">{group.side === "T" ? "T 进攻方" : "CT 防守方"}</span>
          <div className="personal-player-picker-chips">
            {group.players.map((player) => {
              // Same-name players stay tellable apart by the end of their Steam ID.
              const duplicate = (nameCounts.get(player.name.trim().toLowerCase()) ?? 0) > 1;
              return (
                <button key={player.id} type="button" title={`${player.name} · ${player.id}`}
                  className={`personal-player-chip ${highlightedIds.has(player.id) ? "active" : ""}`}
                  onClick={() => onChoose(player.id)}>
                  {player.name}{duplicate ? <small>…{player.id.slice(-4)}</small> : null}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// Only what the player needs to know to pick; the picker itself says what it is for.
function selectionHint(match: ReviewIdentityMatch): string {
  if (match.status === "ambiguous") {
    return `有多位玩家叫 ${match.identity}，请选出你自己。`;
  }
  if (match.source === "saved") {
    return `这场比赛中没有你保存的身份 ${match.identity}。`;
  }
  return "";
}

function identityStatus(match: ReviewIdentityMatch, reviewingSomeoneElse: boolean): string {
  if (match.status !== "matched" || !match.player) {
    return reviewingSomeoneElse ? "还没有确认你在这场比赛中的玩家。" : "";
  }
  const found = {
    saved: `已按你保存的身份匹配到 ${match.player.name}。`,
    steamId: `已按你的 Steam 账号匹配到 ${match.player.name}。`,
    displayName: `已按账号昵称匹配到 ${match.player.name}。`,
    development: `已按本地开发身份匹配到 ${match.player.name}。`
  }[match.source ?? "saved"];
  return reviewingSomeoneElse ? `${found}当前在看其他玩家。` : found;
}
