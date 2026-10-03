import assert from 'node:assert/strict'
import test from 'node:test'
import { chartSegments } from '../frontend/hardwareChart'

test('missing or stale samples are never joined or converted to zero', () => {
  const rows = [{ at: 10, gpu: 0 }, { at: 15, gpu: 50 }, { at: 20, gpu: null }, { at: 25, gpu: 100 }, { at: 60, gpu: 5 }]
  const segments = chartSegments(rows, 'gpu', 60, 60, 100)
  assert.deepEqual(segments.map(s => s.map(p => p.value)), [[0, 50], [100], [5]])
  assert.equal(segments[0][0].y, 100)
  assert.equal(segments[1][0].y, 0)
})

test('time axes use actual elapsed time, omit outside-window and future points', () => {
  const segments = chartSegments([{ at: 0, v: 10 }, { at: 50, v: 20 }, { at: 75, v: 30 }, { at: 101, v: 40 }], 'v', 100, 60, 100)
  assert.deepEqual(segments.map(s => s.map(p => p.at)), [[50], [75]])
  assert.ok(Math.abs(segments[0][0].x - 100/6) < 1e-6)
})
