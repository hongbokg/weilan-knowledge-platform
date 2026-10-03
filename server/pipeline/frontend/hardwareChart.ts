export type HardwarePoint = { at: number; [key: string]: number | null }
export function chartSegments(rows: HardwarePoint[], key: string, end: number, seconds: number, maximum: number) {
  const start = end - seconds
  const segments: { x: number; y: number; at: number; value: number }[][] = []
  let current: typeof segments[number] = []
  let previous: number | undefined
  for (const row of rows) {
    if (row.at < start || row.at > end) continue
    const value = row[key]
    if (value == null || !Number.isFinite(value) || (previous != null && row.at - previous > 20)) {
      if (current.length) segments.push(current)
      current = []
    }
    previous = row.at
    if (value == null || !Number.isFinite(value)) continue
    current.push({ x: 100 * (row.at-start)/seconds, y: 100-100*Math.min(maximum, Math.max(0, value))/maximum, at: row.at, value })
  }
  if (current.length) segments.push(current)
  return segments
}
