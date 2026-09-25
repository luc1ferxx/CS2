// Chinese copy for the production upload quotas. Takes primitives, not an
// ApiError, so the node helper tests can load it without the API client.
export function uploadLimitMessage(
  status: number,
  detailCode: string | null | undefined,
  retryAfterSeconds: number | null | undefined
): string | null {
  if (status === 429 && detailCode === "upload_daily_limit") {
    const wait = waitDuration(retryAfterSeconds);
    return wait
      ? `今天的上传次数已用完，约 ${wait} 后可以继续上传。`
      : "今天的上传次数已用完，请稍后再继续上传。";
  }
  if (status === 429 && detailCode === "active_parse_limit") {
    return "已有比赛正在处理，请等当前比赛处理完成后再上传。";
  }
  if (status === 503 && detailCode === "parse_queue_full") {
    return "服务繁忙，处理队列已满，请稍后再试。";
  }
  if (status === 503 && detailCode === "upload_quota_unavailable") {
    return "暂时无法检查上传额度，请稍后再试。";
  }
  return null;
}

// Rounds up to whole minutes so the copy never promises an earlier time than the API.
function waitDuration(seconds: number | null | undefined): string | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  const totalMinutes = Math.ceil(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) {
    return `${minutes} 分钟`;
  }
  return minutes === 0 ? `${hours} 小时` : `${hours} 小时 ${minutes} 分钟`;
}
