/** Parse a positive duration to milliseconds. All segments must be consumed. */
export function parseDuration(value: string): number {
  const units: Record<string, number> = { ns: 1e-6, us: 1e-3, "µs": 1e-3, ms: 1, s: 1_000, m: 60_000, h: 3_600_000, d: 86_400_000 };
  const input = value.trim();
  let total = 0;
  let cursor = 0;
  const segment = /(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h|d)/gy;
  while (cursor < input.length) {
    segment.lastIndex = cursor;
    const match = segment.exec(input);
    if (!match) throw new Error(`Invalid duration: '${value}'`);
    total += Number(match[1]) * units[match[2]!]!;
    cursor = segment.lastIndex;
  }
  if (!Number.isFinite(total) || total <= 0 || total > Number.MAX_SAFE_INTEGER) {
    throw new Error(`Invalid duration: '${value}'`);
  }
  return total;
}
