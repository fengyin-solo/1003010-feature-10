<template>
  <section class="page" data-module="electricbill">
    <header class="page-head">
      <div>
        <h2>电费管理</h2>
        <p class="page-desc">
          按站点比对缴费月份环比，涨幅越过阈值即标异常；分摊口径为「分表」时按各家用电量分摊、缺读数按合同比例兜底，口径为「合同比例」时一律按比例摊；口径变更重算并原值留档。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = true">登记电费账单</button>
        <button class="btn" type="button" @click="exportRows">导出电费清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span v-if="tab.badge" class="tab-badge">{{ tab.badge }}</span>
      </button>
    </nav>

    <!-- ============ 站点账单（环比 + 分摊） ============ -->
    <div v-if="activeTab === 'bills'">
      <form class="filter-bar" @submit.prevent="reloadBills">
        <label class="filter-item">
          <span>所属站点</span>
          <input v-model="billFilters.site" placeholder="按站点检索" />
        </label>
        <label class="filter-item">
          <span>缴费月份</span>
          <input v-model="billFilters.month" placeholder="如 2026-09" />
        </label>
        <label class="filter-item">
          <span>记录编号</span>
          <input v-model="billFilters.keyword" placeholder="按记录编号检索" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="billFilters.status">
            <option value="">全部</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetBillFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>所属站点</th>
            <th>缴费月份</th>
            <th>电费金额(元)</th>
            <th>上期金额(元)</th>
            <th>环比</th>
            <th>异常说明</th>
            <th>分摊依据</th>
            <th>综合状态</th>
            <th>缴费状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in bills" :key="String(row.id)">
            <tr :class="{ 'row-abnormal': row.abnormal }">
              <td>{{ row['记录编号'] }}</td>
              <td>{{ row['所属站点'] }}</td>
              <td>{{ row['缴费月份'] }}</td>
              <td>{{ fmt(row['电费金额']) }}</td>
              <td>{{ fmt(row['上期电费金额']) }}</td>
              <td>
                <span v-if="row['环比涨幅'] === null" class="muted">首月无比对</span>
                <span :class="rateClass(row['环比涨幅'], row.abnormal)">
                  {{ fmtRate(row['环比涨幅']) }}
                </span>
              </td>
              <td class="abnormal-cell">
                <span v-if="row.abnormal" class="badge danger">超阈值 {{ fmtRate(row['超阈值幅度']) }}</span>
                <span v-else class="muted">{{ row['异常说明'] }}</span>
              </td>
              <td>
                <button class="link" type="button" @click="toggleDetail(row.id)">
                  {{ row['分摊依据'] }} {{ expanded[row.id] ? '▲' : '▼' }}
                </button>
              </td>
              <td>
                <span class="badge" :class="statusBadge(row.status)">{{ row.status }}</span>
              </td>
              <td>{{ row['缴费状态'] }}</td>
              <td class="row-actions">
                <button v-if="row['缴费状态'] === '待缴费'" class="link" type="button" @click="runAction('缴纳电费', row.id)">
                  缴纳电费
                </button>
                <button v-if="row.abnormal" class="link" type="button" @click="runAction('核实确认', row.id)">
                  核实确认
                </button>
                <button v-if="row['核实状态'] === '已核实'" class="link" type="button" @click="runAction('撤销核实', row.id)">
                  撤销核实
                </button>
              </td>
            </tr>
            <tr v-if="expanded[row.id]">
              <td colspan="11" class="detail-cell">
                <table class="sub-table">
                  <thead>
                    <tr>
                      <th>运营商</th>
                      <th>分表用电量(kWh)</th>
                      <th>合同分摊比例</th>
                      <th>分摊金额(元)</th>
                      <th>分摊来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="d in row['分摊明细']" :key="d['运营商']">
                      <td>{{ d['运营商'] }}</td>
                      <td>{{ d['分表用电量'] === null || d['分表用电量'] === undefined ? '—' : d['分表用电量'] }}</td>
                      <td>{{ fmtRate(d['合同分摊比例']) }}</td>
                      <td>{{ fmt(d['分摊金额']) }}</td>
                      <td>{{ d['分摊来源'] }}</td>
                    </tr>
                  </tbody>
                </table>
                <p class="alloc-note">{{ row['分摊说明'] }}</p>
              </td>
            </tr>
          </template>
          <tr v-if="!bills.length">
            <td colspan="11" class="empty-state">没有符合条件的电费账单</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ billTotal }} 条账单，环比异常阈值 {{ fmtRate(threshold) }}</span>
      </footer>
    </div>

    <!-- ============ 异常站点待核实清单 ============ -->
    <div v-if="activeTab === 'abnormal'">
      <table class="data-table">
        <thead>
          <tr>
            <th>所属站点</th>
            <th>异常账单数</th>
            <th>最新异常月份</th>
            <th>上期 → 本期(元)</th>
            <th>环比涨幅</th>
            <th>超阈值</th>
            <th>异常说明</th>
            <th>待核实账单</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in abnormalSites" :key="item['所属站点']" class="row-abnormal">
            <td>{{ item['所属站点'] }}</td>
            <td>{{ item['异常账单数'] }}</td>
            <td>{{ item['最新异常月份'] }}</td>
            <td>{{ fmt(item['上期账单金额']) }} → {{ fmt(item['最新账单金额']) }}</td>
            <td class="rate-up">{{ fmtRate(item['最新环比涨幅']) }}</td>
            <td><span class="badge danger">+{{ fmtRate(item['超阈值幅度']) }}</span></td>
            <td>{{ item['异常说明'] }}</td>
            <td>
              <button class="link" type="button" @click="toggleSite(item['所属站点'])">
                {{ expandedSite === item['所属站点'] ? '收起' : '展开' }} {{ item['异常账单数'] }} 条
              </button>
            </td>
          </tr>
          <tr v-if="expandedSite && abnormalSites.some((i) => i['所属站点'] === expandedSite)">
            <td colspan="8" class="detail-cell">
              <ul class="verify-list">
                <li v-for="b in siteBills(expandedSite)" :key="String(b['账单id'])">
                  <span>{{ b['缴费月份'] }}：{{ b['记录编号'] }}，{{ fmt(b['上期电费金额']) }} → {{ fmt(b['电费金额']) }} 元，环比 {{ fmtRate(b['环比涨幅']) }}，{{ b['异常说明'] }}</span>
                  <button class="btn" type="button" @click="runAction('核实确认', b['账单id'])">核实确认</button>
                </li>
              </ul>
            </td>
          </tr>
          <tr v-if="!abnormalSites.length">
            <td colspan="8" class="empty-state">暂无环比越线站点，待核实清单为空</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ============ 分表读数 + 分摊口径 ============ -->
    <div v-if="activeTab === 'sharing'">
      <h3>分表读数</h3>
      <p class="page-desc">登记或补录某家某月的分表用电量后，该站点当月账单立即按分表占比重算分摊。</p>
      <form class="inline-form" @submit.prevent="submitReading">
        <input v-model="readingForm['所属站点']" placeholder="所属站点" required />
        <input v-model="readingForm['缴费月份']" placeholder="缴费月份 2026-09" required />
        <input v-model="readingForm['运营商']" placeholder="运营商 电信/移动/联通" required />
        <input v-model.number="readingForm['用电量']" placeholder="分表用电量 kWh" type="number" min="0" step="1" required />
        <button class="btn primary" type="submit">登记 / 更新读数</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>所属站点</th><th>缴费月份</th><th>运营商</th><th>分表表码</th><th>用电量(kWh)</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in readings" :key="String(r.id)">
            <td>{{ r['所属站点'] }}</td><td>{{ r['缴费月份'] }}</td><td>{{ r['运营商'] }}</td>
            <td>{{ r['分表读数'] ?? '—' }}</td><td>{{ r['用电量'] }}</td>
          </tr>
          <tr v-if="!readings.length"><td colspan="5" class="empty-state">暂无分表读数</td></tr>
        </tbody>
      </table>

      <h3 class="block-title">站点分摊口径与合同比例</h3>
      <p class="page-desc">
        切换口径或调整合同比例后，存量账单按新口径重算分摊金额，重算前原值进留档；账单总额始终不变。
      </p>
      <div v-for="(cfg, site) in sharingBySite" :key="site" class="sharing-card">
        <div class="sharing-head">
          <strong>{{ site }}</strong>
          <label>口径：
            <select
              :value="cfg[0]['分摊口径']"
              @change="changeBasis(site, ($event.target as HTMLSelectElement).value)"
            >
              <option value="分表">分表（按各家用电量）</option>
              <option value="合同比例">合同比例（缺读数兜底）</option>
            </select>
          </label>
        </div>
        <table class="sub-table">
          <thead><tr><th>运营商</th><th>合同分摊比例</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in cfg" :key="String(row.id)">
              <td>{{ row['运营商'] }}</td>
              <td>
                <input
                  v-model.number="ratioDraft[site + '|' + row['运营商']]"
                  type="number" min="0" max="1" step="0.05" class="ratio-input"
                />
              </td>
              <td>
                <button class="link" type="button" @click="saveRatio(site, row['运营商'])">保存比例并重算</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 重算留档 ============ -->
    <div v-if="activeTab === 'archive'">
      <form class="filter-bar" @submit.prevent="reloadArchive">
        <label class="filter-item"><span>所属站点</span><input v-model="archiveFilter.site" placeholder="按站点检索" /></label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>账单</th><th>所属站点</th><th>缴费月份</th><th>账单总额(元)</th>
            <th>原分摊口径</th><th>原各家分摊(元)</th><th>留档原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in archives" :key="String(a.id)">
            <td>{{ a['记录编号'] }}</td>
            <td>{{ a['所属站点'] }}</td>
            <td>{{ a['缴费月份'] }}</td>
            <td>{{ fmt(a['账单金额']) }}</td>
            <td>{{ a['原分摊口径'] }}</td>
            <td>
              <span v-for="d in a['原分摊明细']" :key="d['运营商']" class="archive-share">
                {{ d['运营商'] }} {{ fmt(d['分摊金额']) }}；
              </span>
            </td>
            <td>{{ a['留档原因'] }}</td>
          </tr>
          <tr v-if="!archives.length"><td colspan="7" class="empty-state">尚无口径变更留档（历史账单原值将在此保存）</td></tr>
        </tbody>
      </table>
    </div>

    <!-- ============ 登记账单弹窗 ============ -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记电费账单</h3>
        <label v-for="f in createFields" :key="f.key" class="modal-field">
          <span>{{ f.label }}</span>
          <input v-model="createForm[f.key]" :type="f.type || 'text'" :placeholder="f.placeholder" required />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" type="submit">登记并计算环比分摊</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/electricbill'
const statuses = ['待缴费', '已缴费', '电费异常', '已核实']

type AllocDetail = { 运营商: string; 分表用电量: number | null; 合同分摊比例: number; 分摊金额: number; 分摊来源: string }
type Bill = {
  id: number
  status: string
  abnormal: boolean
  分摊明细: AllocDetail[]
  环比涨幅: number | null
  超阈值幅度: number | null
  [key: string]: string | number | boolean | null | AllocDetail[]
}

const activeTab = ref<'bills' | 'abnormal' | 'sharing' | 'archive'>('bills')
const tabs = computed(() => [
  { key: 'bills' as const, label: '站点账单', badge: 0 },
  { key: 'abnormal' as const, label: '异常待核实', badge: abnormalSites.value.length },
  { key: 'sharing' as const, label: '分表与分摊口径', badge: 0 },
  { key: 'archive' as const, label: '重算留档', badge: 0 },
])

const bills = ref<Bill[]>([])
const billTotal = ref(0)
const abnormalSites = ref<any[]>([])
const readings = ref<any[]>([])
const sharingRows = ref<any[]>([])
const archives = ref<any[]>([])
const threshold = ref(0.5)
const message = ref('')
const messageOk = ref(true)
const expanded = reactive<Record<number, boolean>>({})
const expandedSite = ref('')
const showCreate = ref(false)

const billFilters = reactive({ site: '', month: '', keyword: '', status: '' })
const archiveFilter = reactive({ site: '' })
const readingForm = reactive({ 所属站点: '', 缴费月份: '', 运营商: '', 用电量: '' as number | string })
const ratioDraft = reactive<Record<string, number>>({})

const createFields = [
  { key: '记录编号', label: '记录编号', placeholder: 'ELEC-0012' },
  { key: '所属站点', label: '所属站点', placeholder: '滨江东路机房' },
  { key: '缴费月份', label: '缴费月份', placeholder: '2026-09' },
  { key: '用电量', label: '总用电量(kWh)', type: 'number', placeholder: '15200' },
  { key: '电费金额', label: '电费金额(元)', type: 'number', placeholder: '12160.00' },
  { key: '票据编号', label: '票据编号', placeholder: 'INV-2609-05' },
]
const createForm = reactive<Record<string, string | number>>({})

const stats = computed(() => [
  { label: '账单总数', value: billTotal.value },
  { label: '环比异常待核实', value: abnormalSites.value.reduce((n, i) => n + i['异常账单数'], 0) },
  { label: '异常站点', value: abnormalSites.value.length },
  { label: '环比异常阈值', value: fmtRate(threshold.value) },
])

const sharingBySite = computed(() => {
  const map: Record<string, any[]> = {}
  for (const row of sharingRows.value) {
    ;(map[row['所属站点']] ||= []).push(row)
    const key = `${row['所属站点']}|${row['运营商']}`
    if (ratioDraft[key] === undefined) ratioDraft[key] = row['合同分摊比例']
  }
  return map
})

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : String(v)
}
function fmtRate(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}
function rateClass(rate: number | null, abnormal: boolean): string {
  if (rate === null || rate === undefined) return 'muted'
  if (abnormal) return 'rate-up'
  return rate >= 0 ? 'rate-flat' : 'rate-down'
}
function statusBadge(status: string): string {
  if (status === '电费异常') return 'danger'
  if (status === '已核实') return 'ok'
  if (status === '已缴费') return 'info'
  return 'warn'
}
function siteBills(site: string) {
  return abnormalSites.value.find((i) => i['所属站点'] === site)?.['待核实账单'] ?? []
}

function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function getJson(url: string): Promise<any> {
  const res = await request(url)
  if (!res.ok) throw new Error(`接口返回 ${res.status}`)
  return res.json()
}
async function postJson(url: string, body: Record<string, unknown>): Promise<{ ok: boolean; message: string; entry?: any }> {
  const res = await request(url, { method: 'POST', body: JSON.stringify(body) })
  return res.json()
}
async function putJson(url: string, body: Record<string, unknown>): Promise<{ ok: boolean; message: string; entry?: any }> {
  const res = await request(url, { method: 'PUT', body: JSON.stringify(body) })
  return res.json()
}

async function reloadBills() {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(billFilters)) if (v) qs.set(k === 'keyword' ? 'keyword' : k, v)
  const payload = await getJson(`${ENDPOINT}?${qs.toString()}`)
  bills.value = payload.items ?? []
  billTotal.value = payload.total ?? bills.value.length
}

async function reloadAbnormal() {
  const payload = await getJson(`${ENDPOINT}/abnormal-sites`)
  abnormalSites.value = payload.items ?? []
}

async function reloadReadings() {
  const payload = await getJson(`${ENDPOINT}/readings`)
  readings.value = payload.items ?? []
}

async function reloadSharing() {
  const payload = await getJson(`${ENDPOINT}/sharing`)
  sharingRows.value = payload.items ?? []
}

async function reloadArchive() {
  const qs = new URLSearchParams()
  if (archiveFilter.site) qs.set('site', archiveFilter.site)
  const payload = await getJson(`${ENDPOINT}/archive?${qs.toString()}`)
  archives.value = payload.items ?? []
}

async function reloadAll() {
  const cfg = await getJson(`${ENDPOINT}/config`)
  threshold.value = cfg['环比异常阈值']
  await Promise.all([reloadBills(), reloadAbnormal(), reloadReadings(), reloadSharing(), reloadArchive()])
}

function switchTab(key: typeof activeTab.value) {
  activeTab.value = key
}
function resetBillFilters() {
  Object.assign(billFilters, { site: '', month: '', keyword: '', status: '' })
  void reloadBills()
}
function toggleDetail(id: number) {
  expanded[id] = !expanded[id]
}
function toggleSite(site: string) {
  expandedSite.value = expandedSite.value === site ? '' : site
}
function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, id: number) {
  const result = await postJson(`${ENDPOINT}/${id}/actions`, { action })
  notify(result.message, result.ok)
  if (result.ok) await reloadAll()
}

async function submitReading() {
  const result = await postJson(`${ENDPOINT}/readings`, { values: { ...readingForm } })
  notify(result.message, result.ok)
  if (result.ok) {
    Object.assign(readingForm, { 所属站点: '', 缴费月份: '', 运营商: '', 用电量: '' })
    await reloadAll()
  }
}

async function changeBasis(site: string, mode: string) {
  const result = await putJson(`${ENDPOINT}/sharing/basis`, { values: { 所属站点: site, 分摊口径: mode } })
  notify(result.message, result.ok)
  await reloadAll()
  if (!result.ok) {
    // 切换被拒绝时回滚下拉框显示
    const cfg = sharingBySite.value[site]
    if (cfg) await reloadSharing()
  }
}

async function saveRatio(site: string, carrier: string) {
  const value = ratioDraft[`${site}|${carrier}`]
  const result = await putJson(`${ENDPOINT}/sharing/ratio`, {
    values: { 所属站点: site, 运营商: carrier, 合同分摊比例: value },
  })
  notify(result.message, result.ok)
  if (result.ok) await reloadAll()
}

async function submitCreate() {
  const result = await postJson(ENDPOINT, { values: { ...createForm } })
  notify(result.message, result.ok)
  if (result.ok) {
    showCreate.value = false
    Object.keys(createForm).forEach((k) => delete createForm[k])
    await reloadAll()
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
.tab-btn { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 13px; color: var(--muted); border-bottom: 2px solid transparent; }
.tab-btn.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
.tab-badge { background: #f04438; color: #fff; border-radius: 10px; padding: 0 6px; font-size: 11px; margin-left: 4px; }
.muted { color: var(--muted); }
.rate-up { color: #b42318; font-weight: 600; }
.rate-down { color: #027a48; }
.rate-flat { color: #344054; }
.row-abnormal td { background: #fff5f4; }
.badge { display: inline-block; border-radius: 4px; padding: 1px 6px; font-size: 12px; background: #e4e7ec; color: #344054; }
.badge.danger { background: #fee4e2; color: #b42318; }
.badge.ok { background: #d1fadf; color: #027a48; }
.badge.info { background: #e0efff; color: #1849a9; }
.badge.warn { background: #fef0c7; color: #b54708; }
.detail-cell { background: #f9fafb !important; padding: 10px 14px !important; }
.sub-table { width: 100%; border-collapse: collapse; }
.sub-table th, .sub-table td { border: 1px solid var(--border); padding: 5px 8px; font-size: 12px; text-align: left; }
.alloc-note { margin: 6px 0 0; font-size: 12px; color: var(--muted); }
.ok-text { color: #027a48; }
.form-msg { font-size: 13px; margin: 4px 0 8px; }
.inline-form { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 12px; }
.inline-form input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.block-title { margin-top: 20px; }
.sharing-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; background: #fff; }
.sharing-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 13px; }
.ratio-input { width: 90px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; }
.verify-list { list-style: none; margin: 0; padding: 0; }
.verify-list li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 6px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.archive-share { white-space: nowrap; font-size: 12px; }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 420px; display: flex; flex-direction: column; gap: 8px; }
.modal-field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.modal-field input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
h3 { font-size: 15px; margin: 14px 0 6px; }
</style>
