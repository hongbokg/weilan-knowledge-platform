<template>
  <section class="performance" aria-label="硬件性能曲线">
    <header><div><h3>性能</h3><p>每 5 秒采样 · 仅保留最近 10 分钟 · 服务器真实负载</p></div><label>时间范围 <select v-model.number="seconds" aria-label="性能图时间范围"><option :value="60">1 分钟</option><option :value="300">5 分钟</option><option :value="600">10 分钟</option></select></label></header>
    <p v-if="stale" class="stale" role="status">硬件采样已过期，曲线保留历史记录；请检查监控服务。</p>
    <div class="performance-layout">
      <nav class="devices" aria-label="硬件视图">
        <button :aria-pressed="selected === 'all'" :class="{ selected: selected === 'all' }" @click="selected = 'all'"><span class="overview-icon" aria-hidden="true">▤</span><span><b>总览</b><small>CPU、GPU、内存与显存</small></span></button>
        <button v-for="item in devices" :key="item.id" :aria-pressed="selected === item.id" :class="{ selected: selected === item.id }" @click="selected = item.id">
          <HardwareCurve :rows="rows" :field="item.field" :label="item.label" :color="item.color" :maximum="item.max" :unit="item.unit" :end="end" :seconds="seconds" compact />
          <span><b>{{ item.label }}</b><small>{{ current(item.field, item.unit) }}</small></span>
        </button>
      </nav>
      <div class="performance-detail">
        <div class="detail-heading"><h4>{{ titles[selected] }}</h4><span>{{ selected === 'gpu' ? hardware.gpu?.name || 'GPU 暂不可用' : selected === 'cpu' ? `${hardware.cpu_count || '—'} 个逻辑处理器` : selected === 'memory' ? `${gib(hardware.memory_total)} GiB 总内存` : 'CPU / NVIDIA GPU' }}</span></div>
        <div class="curves"><HardwareCurve v-for="chart in charts" :key="chart.field" :rows="rows" :field="chart.field" :label="chart.label" :color="chart.color" :maximum="chart.max" :unit="chart.unit" :end="end" :seconds="seconds" /></div>
        <dl class="performance-stats"><div><dt>CPU 使用率</dt><dd>{{ current('cpu_percent') }}</dd></div><div><dt>GPU 使用率</dt><dd>{{ current('gpu_percent') }}</dd></div><div><dt>内存使用</dt><dd>{{ current('memory_used_gib', ' GiB') }}<small> / {{ gib(hardware.memory_total) }} GiB</small></dd></div><div><dt>专用显存</dt><dd>{{ current('vram_used_gib', ' GiB') }}<small> / {{ gib((hardware.gpu?.total_mib ?? 0)*1024**2) }} GiB</small></dd></div><div><dt>GPU 温度</dt><dd>{{ current('gpu_temperature', '°C') }}</dd></div><div><dt>归档磁盘可用</dt><dd>{{ gib(archiveDisk?.free) }}<small> GiB</small></dd></div></dl>
        <p class="footnote">曲线来自服务器，未采集值留空；时间缺口不连线。内存用量按总内存减可用内存统计。GPU 内存控制器利用率与显存容量占比含义不同。监控服务重启后从新采样开始。</p>
      </div>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import HardwareCurve from './HardwareCurve.vue'
import type { HardwarePoint } from './hardwareChart'
const props = defineProps<{ hardware: any; now: number }>()
const selected = ref('all'), seconds = ref(300)
const clock = ref(Date.now()/1000)
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { clock.value = Date.now()/1000 }, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
const end = computed(() => Math.max(props.now, clock.value))
const rows = computed<HardwarePoint[]>(() => props.hardware.history?.samples || [])
const stale = computed(() => !props.hardware.sampled_at || end.value-props.hardware.sampled_at > 20)
const gib = (v: number | undefined) => Number.isFinite(v) && v! > 0 ? (v! / 1024**3).toFixed(1) : '—'
const current = (field: string, unit = '%') => {
  const value = rows.value.filter(r => r.at <= end.value && r.at >= end.value-20).at(-1)?.[field]
  return value == null || !Number.isFinite(value) ? '未采集' : `${value.toFixed(1)}${unit}`
}
const archiveDisk = computed(() => props.hardware.disks?.find((d: any) => d.path === '/data/archive'))
const devices = computed(() => [
  { id: 'cpu', field: 'cpu_percent', label: 'CPU', color: '#168bb3', max: 100, unit: '%' },
  { id: 'memory', field: 'memory_used_gib', label: '内存', color: '#287de0', max: props.hardware.memory_total/1024**3 || 128, unit: ' GiB' },
  { id: 'gpu', field: 'gpu_percent', label: 'GPU', color: '#9351d6', max: 100, unit: '%' },
  { id: 'vram', field: 'vram_used_gib', label: '显存', color: '#7253c5', max: props.hardware.gpu?.total_mib/1024 || 16, unit: ' GiB' },
])
const titles: Record<string,string> = { all: '硬件总览', cpu: 'CPU', gpu: 'GPU', memory: '内存', vram: '专用显存' }
const charts = computed(() => {
  const base = devices.value.map(d => ({ ...d, label: d.id === 'gpu' ? 'GPU 计算利用率' : d.id === 'cpu' ? 'CPU 总利用率' : d.label }))
  if (selected.value === 'all') return base
  if (selected.value === 'gpu') return [base[2], { field: 'gpu_memory_percent', label: 'GPU 内存控制器利用率', color: '#9351d6', max: 100, unit: '%' }, base[3], { field: 'gpu_temperature', label: 'GPU 温度', color: '#c2711f', max: 100, unit: '°C' }]
  if (selected.value === 'cpu') return [base[0], { field: 'cpu_load1', label: 'CPU 1 分钟负载（任务数）', color: '#168bb3', max: Math.max(props.hardware.cpu_count || 1, ...rows.value.map(r => r.cpu_load1 || 0)), unit: ' 个' }]
  if (selected.value === 'memory') return [base[1], { field: 'memory_percent', label: '内存使用率', color: '#287de0', max: 100, unit: '%' }]
  return [base[3]]
})
</script>
<style scoped>
.performance{margin:20px 0;border:1px solid var(--td-component-border,#e0e6eb);border-radius:10px;overflow:hidden;background:var(--td-bg-color-container,#fff)}header{padding:18px 20px;border-bottom:1px solid var(--td-component-border,#e7ecf0);display:flex;justify-content:space-between;align-items:center;gap:16px}h3{margin:0;font-size:18px}header p{font-size:12px;margin:5px 0 0;color:var(--td-text-color-secondary,#7b8794)}header label{font-size:12px;white-space:nowrap}select{border:1px solid var(--td-component-border,#dce4eb);border-radius:6px;padding:6px;color:inherit;background:transparent}.performance-layout{display:grid;grid-template-columns:190px minmax(0,1fr)}.devices{padding:12px 8px;border-right:1px solid var(--td-component-border,#e7ecf0);background:var(--td-bg-color-secondarycontainer,#f7f9fb)}.devices button{width:100%;display:flex;gap:10px;align-items:center;text-align:left;padding:13px 10px;margin-bottom:5px;border:1px solid transparent;border-radius:5px;background:transparent;color:inherit;cursor:pointer}.devices button.selected{border-color:var(--td-brand-color,#138ecb);background:var(--td-bg-color-container,#fff)}button:focus-visible,select:focus-visible{outline:2px solid var(--td-brand-color,#138ecb);outline-offset:2px}.devices b{display:block;font-size:15px;font-weight:500}.devices small{display:block;margin-top:5px;font-size:11px;color:var(--td-text-color-secondary,#687788);font-variant-numeric:tabular-nums}.overview-icon{width:80px;text-align:center;font-size:30px;color:#8797a4}.performance-detail{padding:20px;min-width:0}.detail-heading{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:20px}h4{margin:0;font-size:28px;font-weight:500}.detail-heading>span{font-size:13px;color:var(--td-text-color-secondary,#687788)}.curves{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 18px}.performance-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:24px 0 12px}dt{font-size:12px;color:var(--td-text-color-secondary,#687788)}dd{margin:5px 0 0;font-size:21px;font-weight:400;font-variant-numeric:tabular-nums}dd small{font-size:11px}.footnote{font-size:11px;line-height:1.8;color:var(--td-text-color-secondary,#7b8794);margin:12px 0 0}.stale{padding:10px 20px;margin:0;color:#996014;background:#fff6df;font-size:12px}@media(max-width:900px){.performance-layout{grid-template-columns:1fr}.devices{display:flex;overflow-x:auto;border-right:0;border-bottom:1px solid var(--td-component-border,#e7ecf0)}.devices button{min-width:120px;width:auto;flex:1;padding:8px;flex-direction:column;align-items:flex-start}.overview-icon{height:44px}.devices b{font-size:13px}.devices small{font-size:11px}.performance-detail{padding:16px}}@media(max-width:580px){header{align-items:flex-start;padding:14px;flex-direction:column;gap:10px}.curves{grid-template-columns:1fr;gap:16px}.performance-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.detail-heading{align-items:flex-start;flex-direction:column;gap:5px}h4{font-size:23px}dd{font-size:18px}}
</style>
