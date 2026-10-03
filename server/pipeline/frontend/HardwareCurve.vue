<template>
  <figure :class="['curve', { compact }]" :style="{ '--curve-color': color }">
    <figcaption v-if="!compact"><span>{{ label }}</span><strong>{{ valueText }}</strong></figcaption>
    <div class="plot">
      <span v-if="!compact" class="axis-max">{{ maximumText }}</span>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" :aria-label="`${label}，${valueText}，最近 ${seconds / 60} 分钟`">
        <g class="grid" v-if="!compact"><path v-for="i in 9" :key="`x${i}`" :d="`M ${i*10} 0 V 100`"/><path v-for="i in 3" :key="`y${i}`" :d="`M 0 ${i*25} H 100`"/></g>
        <template v-for="(segment, i) in segments" :key="i">
          <path v-if="segment.length > 1" class="area" :d="`M ${segment[0].x} 100 L ${segment.map(p => `${p.x} ${p.y}`).join(' L ')} L ${segment[segment.length-1].x} 100 Z`"/>
          <polyline :points="segment.map(p => `${p.x},${p.y}`).join(' ')" class="line"/>
          <circle v-if="segment.length === 1" :cx="segment[0].x" :cy="segment[0].y" r="0.5" :fill="color"/>
        </template>
      </svg>
      <div v-if="!segments.length && !compact" class="empty">暂无有效采样</div>
    </div>
    <div v-if="!compact" class="axis-time"><span>{{ seconds / 60 }} 分钟前</span><span>0</span></div>
  </figure>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { chartSegments, type HardwarePoint } from './hardwareChart'
const props = defineProps<{ rows: HardwarePoint[]; field: string; label: string; color: string; maximum: number; end: number; seconds: number; unit?: string; compact?: boolean }>()
const segments = computed(() => chartSegments(props.rows, props.field, props.end, props.seconds, props.maximum || 100))
const last = computed(() => props.rows.filter(r => r.at >= props.end-20 && r.at <= props.end).at(-1)?.[props.field])
const valueText = computed(() => last.value == null || !Number.isFinite(last.value) ? '未采集' : `${last.value.toFixed(1)}${props.unit || '%'}`)
const maximumText = computed(() => `${props.maximum.toFixed(props.unit === ' GiB' ? 1 : 0)}${props.unit || '%'}`)
</script>
<style scoped>
.curve{margin:0;min-width:0;color:var(--td-text-color-primary,#18242a)}figcaption{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:8px;font-size:14px}figcaption strong{font-size:14px;font-weight:500;font-variant-numeric:tabular-nums;color:var(--curve-color)}.plot{position:relative;height:170px;border:1px solid var(--td-component-border,#dce4eb);background:var(--td-bg-color-container,#fff)}svg{width:100%;height:100%;display:block;overflow:hidden}.grid path{stroke:var(--td-component-border,#e4eaf0);stroke-width:1;vector-effect:non-scaling-stroke}.area{fill:var(--curve-color);opacity:.16}.line{fill:none;stroke:var(--curve-color);stroke-width:1.5;vector-effect:non-scaling-stroke}.axis-max{position:absolute;right:5px;top:3px;color:var(--td-text-color-secondary,#7b8794);font-size:10px;z-index:1}.axis-time{display:flex;justify-content:space-between;font-size:11px;color:var(--td-text-color-secondary,#7b8794);margin-top:5px}.empty{position:absolute;inset:0;display:grid;place-items:center;font-size:12px;color:var(--td-text-color-secondary,#7b8794)}.compact .plot{height:44px;width:80px}.compact .line{stroke-width:1}.compact .area{opacity:.2}@media(max-width:580px){.plot{height:145px}.compact .plot{width:60px;height:40px}}
</style>
