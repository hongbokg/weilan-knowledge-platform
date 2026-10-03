<template>
  <section class="nas-monitor" aria-label="硬件与 NAS 同步状态">
    <div class="nas-heading"><div><h2>硬件与 NAS 同步</h2><p>原文件只读 · 每 3 天增量扫描 · 解析、索引分别排队</p></div><div class="nas-controls"><span>{{ lastUpdate }}</span><t-button variant="outline" :loading="loading" @click="refresh">刷新</t-button></div></div>
    <div v-if="error" class="nas-error" role="alert">{{ error }} <button @click="refresh">重试</button></div>
    <template v-if="data">
      <div class="notice"><t-button theme="danger" variant="outline" :disabled="powerBusy" @click="showPower = true">安全关机</t-button><span style="margin-left:12px">{{ data.power?.message || '关机前保存任务检查点并备份配置；开机后自动恢复' }}</span></div>
      <div v-if="showPower" class="nas-error" role="alert">
        <b>关闭这台 Linux 虚拟机</b><p>等待任务保存检查点并完成备份后关机。未完成页面开机后重新处理，不保存进程内存。不会关闭 R740 宿主机。备份失败将取消关机。</p>
        <input v-model="powerConfirm" aria-label="关机确认" placeholder="输入：关闭虚拟机" />
        <t-button theme="danger" :disabled="powerConfirm !== '关闭虚拟机' || powerBusy" :loading="acting" @click="shutdown">备份并关机</t-button>
        <t-button variant="outline" @click="showPower = false; powerConfirm = ''">取消</t-button>
      </div>
      <HardwarePerformance :hardware="data.hardware" :now="data.time" />
      <section v-if="data.pipeline" class="notice" aria-label="PDF 分流与吞吐">
        <h3>PDF 分流与吞吐（最近一小时）</h3>
        <p v-if="data.pipeline.document_lanes?.large">文档通道：大文件优先 {{ data.pipeline.document_lanes.large }} 路（≥100 MB）· 小文件优先 {{ data.pipeline.document_lanes.small }} 路；空闲通道可互相接手。GPU 常驻模型池 {{ data.pipeline.gpu_slots }} 路推理，大文件每 10 页保存进度并让出队列。</p>
        <p class="hint">NAS 外部解析队列单独调度，不计入 WeKnora 内部 Worker 池；这里展示 NAS 当前任务与真实页吞吐。</p>
        <p>质量通过 {{ data.pipeline.successful_pages_hour }} 页 · 质量标记 {{ data.pipeline.flagged_pages_hour }} 页 · 平均 {{ data.pipeline.successful_pages_minute }} 页/分钟</p>
        <p>CPU 直接提取 {{ data.pipeline.routes_hour.cpu || 0 }} 页 · GPU {{ (data.pipeline.routes_hour.gpu || 0) + (data.pipeline.routes_hour.legacy_gpu || 0) }} 页 · 混合页 {{ data.pipeline.routes_hour.mixed || 0 }} 页</p>
        <p>GPU 等待 {{ data.pipeline.timings_mean.admission_seconds ?? '—' }} 秒 · 提交至结果完成 {{ data.pipeline.timings_mean.job_wait_seconds ?? '—' }} 秒 · 结果下载 {{ data.pipeline.timings_mean.download_seconds ?? '—' }} 秒</p>
        <p class="hint">电子文本页经质量校验后使用 CPU；扫描件、复杂表格和未通过校验的页面继续 GPU 识别。混合页当前保留整页识别。通过页数不等于已入库文件数。</p>
      </section>
      <section v-if="data.pipeline?.observability" class="notice" aria-label="队列与负载监护">
        <h3>队列与负载监护</h3>
        <p v-if="data.pipeline.observability.stale" class="nas-error" role="alert">监控数据超过 90 秒未更新，请检查监护服务；下方为上次采样。</p>
        <p>采样时间 {{ date(data.pipeline.observability.sampled_at) }} · 每 30 秒记录 · 最近 5 分钟合格 {{ data.pipeline.observability.pages['5'].quality_passed }} 页，{{ data.pipeline.observability.pages['5'].quality_pages_per_minute }} 页/分钟</p>
        <p v-if="data.pipeline.observability.ocr_pool">OCR 请求：执行 {{ data.pipeline.observability.ocr_pool.active }} / {{ data.pipeline.observability.ocr_pool.slots }} · 等待 {{ data.pipeline.observability.ocr_pool.waiting }} / {{ data.pipeline.observability.ocr_pool.waiting_limit }} · 接收请求体 {{ data.pipeline.observability.ocr_pool.receiving ?? 0 }}</p>
        <p v-else class="nas-error">OCR 请求池监控暂不可用</p>
        <p>页面等待：平均 {{ seconds(data.pipeline.observability.pages.timings.admission_seconds?.mean_seconds) }} · P95 {{ seconds(data.pipeline.observability.pages.timings.admission_seconds?.p95_seconds) }}；提交至识别完成：平均 {{ seconds(data.pipeline.observability.pages.timings.job_wait_seconds?.mean_seconds) }}</p>
        <p>文件退避等待 {{ data.pipeline.observability.cooling_files }} 份 · 最近一小时解析异常事件 {{ data.pipeline.observability.events_hour.document_attention || 0 }} 次 · 发布异常事件 {{ data.pipeline.observability.events_hour.publish_attention || 0 }} 次</p>
        <p v-if="data.pipeline.observability.ocr_pool?.metrics">请求池本次启动以来：完成 {{ data.pipeline.observability.ocr_pool.metrics.counts.completed }} · 超时 {{ data.pipeline.observability.ocr_pool.metrics.counts.timeouts }} · 后端异常 {{ data.pipeline.observability.ocr_pool.metrics.counts.failed }} · 洪峰拒绝 {{ data.pipeline.observability.ocr_pool.metrics.counts.queue_rejected }}</p>
        <p>GPU ≥98% 连续采样时长 {{ seconds(data.pipeline.observability.gpu_busy_sampled_seconds) }}。满载本身不触发重启。</p>
        <p v-for="warning in data.pipeline.observability.warnings" :key="warning" class="nas-error" role="alert">{{ observerWarnings[warning] || warning }}</p>
        <p v-if="!data.pipeline.observability.warnings.length">当前没有监控告警</p>
        <p class="hint">8 个上限仅指 OCR 识别请求等待，文件积压见同步进度；WeKnora 内部 Redis 队列另行统计。合格页数按版本和页码去重，不代表整个文档已入库。文件从发现到首次处理的等待、独立向量耗时尚未可靠计时，暂不显示估算值。历史记录保留 7 天，监控不调用云端模型。</p>
      </section>
      <section v-if="data.llm_cost?.available" class="notice" aria-label="云端模型预算">
        <h3>云端 LLM 预算与用量</h3>
        <p>今日 {{ tokens(data.llm_cost.daily_used) }} / {{ tokens(data.llm_cost.daily_limit) }} Token · 本月 {{ tokens(data.llm_cost.monthly_used) }} / {{ tokens(data.llm_cost.monthly_limit) }} Token</p>
        <p>含在途预留 {{ tokens(data.llm_cost.reserved) }} Token · 限流冷却中的模型 {{ data.llm_cost.cooling_models }} 个</p>
        <p class="hint">上海时间每日 / 每月 1 日重置。统计从 {{ date(data.llm_cost.started_at) }} 开始；不含此前及其他软件调用。未返回用量的请求保守估算，非供应商账单。达到预算后暂停云端生成，本地解析和索引继续。</p>
        <table><thead><tr><th>今日任务</th><th>请求数</th><th>实际 Token</th><th>估算 Token</th><th>摘要缓存命中</th><th>异常</th></tr></thead><tbody>
          <tr v-for="row in data.llm_cost.rows" :key="row.task"><td>{{ costTask(row.task) }}</td><td>{{ row.calls }}</td><td>{{ tokens(row.actual_tokens) }}</td><td>{{ tokens(row.estimated_tokens) }}</td><td>{{ row.cache_hits }}</td><td>{{ row.errors }}</td></tr>
          <tr v-if="!data.llm_cost.rows.length"><td colspan="6">统计启用后尚无调用</td></tr>
        </tbody></table>
        <p>摘要按需：打开文档 → 文档摘要 → 生成 / 刷新；相同内容复用缓存。重点项目 Wiki：仅在选定知识库中启用 Wiki 合成。</p>
      </section>
      <div class="sync-heading"><h3>同步进度 <span :class="['state-pill', data.paused ? 'paused' : 'running']">{{ data.paused ? '已暂停' : '自动运行' }}</span></h3><div class="nas-controls"><t-button variant="outline" :loading="acting" @click="action('scan')">立即扫描</t-button><t-button :theme="data.paused ? 'primary' : 'default'" :loading="acting" @click="action(data.paused ? 'resume' : 'pause')">{{ data.paused ? '继续同步' : '暂停同步' }}</t-button></div></div>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div class="counts-grid">
        <button @click="filter('all')"><strong>{{ data.total }}</strong><span>纳入同步的文件</span></button>
        <button @click="filter('indexed')"><strong>{{ data.counts.indexed || 0 }}</strong><span>已入库可检索</span></button>
        <button @click="filter('pending')"><strong>{{ (data.counts.pending || 0) + (data.counts.retry || 0) }}</strong><span>待解析（含当前任务）</span></button>
        <button @click="filter('validated')"><strong>{{ data.counts.validated || 0 }}</strong><span>解析完成，待索引</span></button>
        <button class="attention" @click="filter('review')"><strong>{{ (data.counts.review || 0) + (data.counts.ambiguous || 0) }}</strong><span>需处理 / 隔离</span></button>
      </div>
      <div class="progress-caption"><span>真正可检索 {{ readyPercent.toFixed(1) }}%</span><span>最近 1 小时完成 {{ data.events_hour.page_completed || 0 }} 页解析</span></div><progress class="overall" max="100" :value="readyPercent" aria-label="可检索文件占比" />
      <p class="hint">解析完成不代表已入库；Wiki 合成进度见下方队列。剩余总页数尚不完整，暂不显示不可靠的全量完成时间。</p>
      <p v-if="data.concurrency" class="hint">并发上限：Office {{ data.concurrency.cpu_document_workers }} 路 · PDF 下载与准备 {{ data.concurrency.pdf_document_workers }} 路 · CPU 渲染 {{ data.concurrency.cpu_render_processes }} 个进程 · GPU 识别 {{ data.concurrency.gpu_admission }} 路。下方显示当前实际任务；没有待处理文件时工作线程会自动空闲。</p>
      <div class="active-jobs"><div v-for="job in data.active" :key="`${job.role}:${job.lane ?? 'default'}`"><span class="state-pill running">{{ jobLabel(job) }}{{ job.lane == null ? '' : ` · ${job.lane + 1} 号队列` }}</span><b>{{ job.path.split('/').pop() }}</b><span>{{ job.pages ? `${job.page} / ${job.pages} 页` : '处理中' }}</span><small>{{ job.path }}</small></div><p v-if="!data.active.length">{{ data.paused ? '后续任务已暂停，已提交的任务会在检查点停止。' : '等待下一批任务；运行中的旧任务需在下一次检查点后显示。' }}</p></div>
      <p class="hint">最近扫描：{{ date(data.scan.started) }} · {{ data.scan.complete ? `完成，${data.scan.count} 份` : data.scan.finished ? `扫描中断，已发现 ${data.scan.count} 份；将重新扫描` : `扫描中，已发现 ${data.scan.count} 份` }}<template v-if="data.inventory"> · 目录盘点 {{ data.inventory.total || 0 }} 个文件</template></p>
      <p v-if="data.inventory" class="hint">全盘盘点：PDF {{ inventoryCount(['.pdf']) }} 份 · Word/PPT {{ inventoryCount(['.doc','.docx','.ppt','.pptx']) }} 份 · Excel/CSV {{ inventoryCount(['.xls','.xlsx','.xlsm','.csv']) }} 份（留给报价结构化通道）。当前同步支持 PDF、Word、PPT；图片、CAD、压缩包及其他格式不计为已入库。黑名单与敏感资料单独排除或隔离。</p>
      <div class="file-filters"><select v-model="state" aria-label="文件状态" @change="page = 1; refresh()"><option v-for="(label, value) in labels" :key="value" :value="value">{{ label }}</option></select><input v-model="query" placeholder="搜索文件名或 NAS 路径" aria-label="搜索文件" @keyup.enter="page = 1; refresh()" /><t-button variant="outline" @click="page = 1; refresh()">查询</t-button></div>
      <div class="table-wrap"><table><thead><tr><th>文件 / 原始路径</th><th>大小</th><th>状态</th><th>原因</th></tr></thead><tbody><tr v-for="file in data.files" :key="file.path"><td><b>{{ file.name }}</b><small>{{ file.path }}</small></td><td>{{ bytes(file.size) }}</td><td>{{ labels[file.state] || file.state }}</td><td>{{ reason(file.error) }}</td></tr><tr v-if="!data.files.length"><td colspan="4">当前筛选下没有文件</td></tr></tbody></table></div>
      <div class="pager"><span>共 {{ data.filtered_total }} 份 · 第 {{ page }} 页</span><t-button variant="outline" :disabled="page <= 1" @click="page--; refresh()">上一页</t-button><t-button variant="outline" :disabled="page * 30 >= data.filtered_total" @click="page++; refresh()">下一页</t-button></div>
    </template><p v-else-if="!error">正在读取服务器状态…</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { get, post } from '@/utils/request'
import HardwarePerformance from './HardwarePerformance.vue'
const data = ref<any>(null), loading = ref(false), acting = ref(false), error = ref(''), notice = ref(''), state = ref('all'), query = ref(''), page = ref(1)
const showPower = ref(false), powerConfirm = ref(''), shutdownAccepted = ref(false)
const powerBusy = computed(() => shutdownAccepted.value || ['draining','backing_up','ready','powering_off'].includes(data.value?.power?.phase))
async function shutdown() {
 acting.value = true
 try { await post(endpoint, { action: 'shutdown', confirmation: powerConfirm.value }); shutdownAccepted.value = true; showPower.value = false; notice.value = '安全关机已提交，请等待备份和关机完成。'; await refresh() }
 catch { error.value = '关机请求失败，请检查状态后重试。' }
 finally { acting.value = false }
}
let timer: ReturnType<typeof setInterval> | undefined
const seconds = (n: number | null | undefined) => n == null ? '未采集' : `${Number(n).toFixed(1)} 秒`
const observerWarnings: Record<string, string> = { ocr_request_queue_sustained_full: 'OCR 请求等待连续满 5 分钟，请检查处理耗时和上游请求量', gpu_temperature_sustained_high: 'GPU 温度持续偏高，请检查服务器散热与降频状态', ocr_pool_telemetry_unavailable: '无法获取 OCR 请求池监控，请检查服务', ocr_request_timeout: '最近采样间隔出现 OCR 请求超时，文件按既有策略处理', ocr_request_backpressure: '最近采样间隔发生请求洪峰拒绝，建议检查上游退避' }
const tokens = (n: number) => Number(n || 0).toLocaleString('zh-CN')
const costTask = (s: string) => s === 'document_summary' ? '文档摘要' : s.startsWith('wiki') ? `Wiki · ${s}` : s === 'interactive_or_other' ? '问答 / 其他' : s
const jobLabel = (job: any) => ({ download: '下载与校验', content_wait: '等待同内容任务', cpu_render: 'CPU 渲染', gpu_wait: '等待 GPU', gpu_ocr: 'GPU 识别', result_download: '下载识别结果', cpu_office: 'CPU 文档解析' } as Record<string, string>)[job.stage] || (job.role === 'parser' ? 'PDF 处理' : job.role === 'cpu' ? 'CPU 文档解析' : '入库校验')
const endpoint = '/api/v1/system/admin/nas-monitor'
const labels: Record<string, string> = { all: '全部', indexed: '已入库', pending: '待解析', validated: '待索引', review: '需处理', retry: '待重试', ambiguous: '提交待核对' }
const reasons: Record<string, string> = { image_assets_require_review: '原图尚未关联', native_text_coverage_low: '文字识别覆盖不足', nonblank_page_empty_or_short: '页面内容识别不足', empty_table: '表格内容为空', oversize_page_requires_tiling: '超大页面待切片', sensitive_content_admin_review: '敏感资料隔离', mineru_page_failed: '解析引擎失败', anydoc_conversion_failed: 'Office 转换失败', office_oversize_requires_conversion: '大 Office 文件待专门转换', sensitive_path_admin_review: '敏感路径隔离' }
const reason = (value: string) => value ? value.split(',').map(v => reasons[v] || v).join('；') : '—'
const bytes = (v: number | undefined) => !Number.isFinite(v) ? '—' : v! >= 1024 ** 3 ? `${(v! / 1024 ** 3).toFixed(1)} GB` : v! >= 1024 ** 2 ? `${(v! / 1024 ** 2).toFixed(1)} MB` : `${Math.round(v! / 1024)} KB`
const pct = (v: number | undefined | null) => v == null ? '—' : `${v.toFixed(1)}%`
const date = (v: number) => v ? new Date(v * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未扫描'
const lastUpdate = computed(() => data.value ? `更新于 ${date(data.value.time)}` : '')
const memoryPercent = computed(() => data.value?.hardware.memory_total ? 100 * (1 - data.value.hardware.memory_available / data.value.hardware.memory_total) : 0)
const archiveDisk = computed(() => data.value?.hardware.disks?.find((d: any) => d.path === '/data/archive'))
const inventoryCount = (extensions: string[]) => extensions.reduce((n, ext) => n + (data.value?.inventory?.extensions?.[ext] || 0), 0)
const readyPercent = computed(() => data.value?.total ? 100 * (data.value.counts.indexed || 0) / data.value.total : 0)
async function refresh() {
 if (loading.value) return
 loading.value = true
 try { const result: any = await get(endpoint, { params: { state: state.value, page: page.value, q: query.value } }); data.value = result.data ?? result; error.value = ''; if (data.value?.power?.phase === 'failed') shutdownAccepted.value = false }
 catch { if (shutdownAccepted.value || data.value?.power?.phase === 'powering_off') { error.value = ''; notice.value = '关机请求后连接已断开。请在虚拟机管理界面确认是否已关闭；断连本身不能证明关机成功，请勿直接断电。' } else { error.value = '无法读取同步状态，请确认管理员登录有效或稍后重试。' } }
 finally { loading.value = false }
}
function filter(value: string) { state.value = value; page.value = 1; refresh() }
async function action(value: string) {
 acting.value = true
 try { await post(endpoint, { action: value }); notice.value = value === 'scan' ? '扫描请求已登记，将在解析任务释放扫描锁后执行。' : value === 'pause' ? '已请求暂停；正在执行的页面会在检查点停止。' : '已恢复，后台将继续处理。'; await refresh() }
 catch { error.value = '操作失败，请稍后重试。' }
 finally { acting.value = false }
}
onMounted(() => { refresh(); timer = setInterval(() => { if (!document.hidden) refresh() }, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
<style scoped>
.nas-monitor{border:1px solid var(--td-component-border,#e7e9ed);border-radius:12px;padding:24px;margin-bottom:28px;background:var(--td-bg-color-container,#fff);color:var(--td-text-color-primary,#20312b)}.nas-heading,.sync-heading,.nas-controls,.pager,.progress-caption{display:flex;align-items:center;justify-content:space-between;gap:12px}.nas-heading h2{margin:0;font-size:20px}.nas-heading p,.hint,.nas-controls>span{font-size:12px;color:var(--td-text-color-secondary,#737d78)}.hardware-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}.hardware-grid article{padding:16px;border-radius:9px;background:var(--td-bg-color-secondarycontainer,#f6f8f7);display:flex;gap:8px;flex-direction:column}.hardware-grid strong{font-size:24px}.hardware-grid small{font-size:12px;font-weight:400;color:#64736c}.counts-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.counts-grid button{border:1px solid #e4eae6;background:transparent;border-radius:8px;padding:16px 8px;text-align:left;cursor:pointer;color:inherit}.counts-grid button strong{display:block;font-size:26px}.counts-grid button span{font-size:12px;color:#67736b}.counts-grid .attention strong{color:#b77918}progress{width:100%;height:6px;accent-color:#07b875;border:0}.overall{height:10px}.progress-caption{margin-top:18px;font-size:13px}.state-pill{font-size:12px;border-radius:12px;padding:4px 9px;font-weight:400}.running{background:#e6f8ee;color:#087f50}.paused{background:#fff1da;color:#8b621a}.active-jobs>div{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px;background:#f5f9f7;border-radius:8px;margin:8px 0}.active-jobs small{width:100%;overflow-wrap:anywhere;color:#6f7872}.active-jobs p{font-size:13px;color:#77827c}.file-filters{display:flex;gap:10px;margin:18px 0}.file-filters input,.file-filters select{padding:8px 10px;border:1px solid #dce3df;border-radius:6px;background:transparent;color:inherit}.file-filters input{flex:1;min-width:80px}.table-wrap{overflow:auto;max-height:440px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:12px;border-bottom:1px solid #edf0ee}th{background:#f7f9f8;position:sticky;top:0}td:first-child{min-width:240px;max-width:620px}td small{display:block;font-size:11px;color:#7b847e;overflow-wrap:anywhere;margin-top:5px}.pager{justify-content:flex-end;margin-top:14px;font-size:12px}.nas-error{padding:14px;background:#fff0ed;color:#a43823}.notice{padding:10px;background:#edf8f1;font-size:13px}@media(max-width:950px){.hardware-grid{grid-template-columns:repeat(2,1fr)}.counts-grid{grid-template-columns:repeat(3,1fr)}.nas-heading{align-items:flex-start;flex-direction:column}}@media(max-width:580px){.nas-monitor{padding:14px}.counts-grid{grid-template-columns:repeat(2,1fr)}.nas-controls{flex-wrap:wrap}.sync-heading{align-items:flex-start;flex-direction:column}}
</style>
