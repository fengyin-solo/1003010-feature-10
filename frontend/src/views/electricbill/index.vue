<template>
  <section class="page" data-module="electricbill">
    <header class="page-head">
      <div>
        <h2>电费管理</h2>
        <p class="page-desc">按站点与缴费月份做环比比对，越阈自动标异常；共站站点按分表用电量分摊、缺读数按合同比例兜底，口径变更后存量记录自动重算、历史账单原值留档。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记电费记录</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ 'stat-warn': item.warn }">{{ item.value }}</strong>
      </article>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span v-if="tab.key === 'pending'" class="tab-badge">{{ pendingItems.length }}</span>
      </button>
    </div>

    <p v-if="message" class="tip" :class="{ 'tip-error': !messageOk }">{{ message }}</p>

    <!-- ===================== Tab 1：账单比对 ===================== -->
    <template v-if="activeTab === 'bills'">
      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>所属站点</span>
          <input v-model="filters.site" list="site-options" placeholder="全部站点" />
        </label>
        <label class="filter-item">
          <span>缴费月份</span>
          <input v-model="filters.month" type="month" placeholder="YYYY-MM" />
        </label>
        <label class="filter-item">
          <span>缴费状态</span>
          <select v-model="filters.status">
            <option value="">全部</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>异常标记</span>
          <select v-model="filters.abnormal">
            <option value="">全部</option>
            <option value="true">仅异常</option>
            <option value="false">仅正常</option>
          </select>
        </label>
        <label class="filter-item">
          <span>关键字</span>
          <input v-model="filters.keyword" placeholder="记录编号 / 站点" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置</button>
      </form>
      <datalist id="site-options">
        <option v-for="s in sites" :key="s" :value="s"></option>
      </datalist>

      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>所属站点</th>
            <th>缴费月份</th>
            <th class="num">电费金额(元)</th>
            <th class="num">环比涨幅</th>
            <th>异常说明</th>
            <th>分摊</th>
            <th>缴费状态</th>
            <th>留档</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="String(row.id)">
            <tr :class="{ 'row-abnormal': row.异常标记 }">
              <td>{{ row.记录编号 }}</td>
              <td>{{ row.所属站点 }}</td>
              <td>{{ row.缴费月份 }}</td>
              <td class="num">{{ money(row.电费金额) }}</td>
              <td class="num">
                <span v-if="row.环比涨幅 === null || row.环比涨幅 === undefined">—</span>
                <span v-else :class="row.环比涨幅 > threshold ? 'rate-bad' : 'rate-ok'">
                  {{ percent(row.环比涨幅) }}
                </span>
              </td>
              <td class="cell-note">
                <span v-if="row.异常标记" class="tag tag-abnormal">异常</span>
                {{ row.异常说明 || '—' }}
              </td>
              <td>
                <button class="link" type="button" @click="toggleDetail(row.id)">
                  {{ expanded.has(row.id) ? '收起' : `${(row.分摊明细 ?? []).length} 家明细` }}
                </button>
              </td>
              <td>
                <span class="tag" :class="statusClass(row.status)">{{ row.status }}</span>
              </td>
              <td>
                <button v-if="(row.历史账单 ?? []).length" class="link" type="button" @click="toggleHistory(row.id)">
                  {{ row.历史账单.length }} 版
                </button>
                <span v-else>—</span>
              </td>
              <td class="row-actions">
                <button v-if="!row.已缴费" class="link" type="button" @click="runAction(row, '缴纳电费')">缴费</button>
                <button class="link" type="button" @click="runAction(row, '核实确认')">核实确认</button>
                <button class="link" type="button" @click="openEdit(row)">订正</button>
              </td>
            </tr>
            <tr v-if="expanded.has(row.id)">
              <td colspan="10" class="detail-cell">
                <table class="inner-table">
                  <thead>
                    <tr>
                      <th>运营商</th>
                      <th class="num">分表用电量(度)</th>
                      <th class="num">分摊比例</th>
                      <th class="num">分摊金额(元)</th>
                      <th>分摊依据</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="d in row.分摊明细" :key="d.运营商">
                      <td>{{ d.运营商 }}</td>
                      <td class="num">{{ d.分表用电量 === null || d.分表用电量 === undefined ? '未抄表' : d.分表用电量 }}</td>
                      <td class="num">{{ percent(d.分摊比例) }}</td>
                      <td class="num">{{ money(d.分摊金额) }}</td>
                      <td>{{ d.分摊依据 }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-if="hasMissingMeter(row)" class="cell-hint">该账单存在未抄分表，已按合同约定比例兜底分摊。</p>
              </td>
            </tr>
            <tr v-if="historyId === row.id">
              <td colspan="10" class="detail-cell">
                <p class="cell-hint">历史账单按原值留档：账单总额始终为 {{ money(row.电费金额) }} 元，留档的是各次口径下的分摊结果。</p>
                <div v-for="(h, i) in row.历史账单" :key="i" class="history-card">
                  <div class="history-head">
                    <span class="tag tag-done">第 {{ row.历史账单.length - i }} 版</span>
                    <strong>{{ h.变更原因 }}</strong>
                    <span class="cell-hint">留档时间 {{ h.留档时间 }} ｜ 当时账单总额 {{ money(h.电费金额) }} 元 ｜ 依据：{{ h.分摊依据 || '—' }}</span>
                  </div>
                  <span v-for="d in h.分摊明细" :key="d.运营商" class="history-share">
                    {{ d.运营商 }}：{{ money(d.分摊金额) }} 元（{{ percent(d.分摊比例) }}）
                  </span>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!rows.length">
            <td colspan="10" class="empty-state">暂无符合条件的电费记录</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ total }} 条记录</span>
        <span class="pager">
          <button class="btn" type="button" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
          第 {{ page }} 页
          <button class="btn" type="button" :disabled="page * pageSize >= total" @click="changePage(1)">下一页</button>
        </span>
      </footer>
    </template>

    <!-- ===================== Tab 2：待核实清单 ===================== -->
    <template v-else-if="activeTab === 'pending'">
      <table class="data-table" v-if="pendingItems.length">
        <thead>
          <tr>
            <th>异常站点</th>
            <th class="num">异常账单数</th>
            <th>最近异常月份</th>
            <th class="num">最近环比涨幅</th>
            <th class="num">当月电费(元)</th>
            <th>异常说明（超出阈值的具体金额）</th>
            <th>待核实账单</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="item in pendingItems" :key="item.所属站点">
            <tr class="row-abnormal">
              <td>{{ item.所属站点 }}</td>
              <td class="num">{{ item.异常账单数 }}</td>
              <td>{{ item.最近异常月份 }}</td>
              <td class="num rate-bad">{{ percent(item.最近环比涨幅) }}</td>
              <td class="num">{{ money(item.最近电费金额) }}</td>
              <td>{{ item.异常说明 }}</td>
              <td>
                <button class="link" type="button" @click="pendingOpenId = pendingOpenId === item.所属站点 ? '' : item.所属站点">
                  {{ pendingOpenId === item.所属站点 ? '收起' : `查看 ${item.异常账单数} 条` }}
                </button>
              </td>
            </tr>
            <tr v-if="pendingOpenId === item.所属站点">
              <td colspan="7" class="detail-cell">
                <table class="inner-table">
                  <thead>
                    <tr>
                      <th>记录编号</th>
                      <th>缴费月份</th>
                      <th class="num">电费金额(元)</th>
                      <th class="num">环比涨幅</th>
                      <th>状态</th>
                      <th>说明</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="b in item.待核实账单" :key="String(b.id)">
                      <td>{{ b.记录编号 }}</td>
                      <td>{{ b.缴费月份 }}</td>
                      <td class="num">{{ money(b.电费金额) }}</td>
                      <td class="num rate-bad">{{ percent(b.环比涨幅) }}</td>
                      <td>{{ b.status }}</td>
                      <td>{{ b.异常说明 }}</td>
                      <td><button class="link" type="button" @click="verifyById(b.id)">核实确认</button></td>
                    </tr>
                  </tbody>
                </table>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-else class="empty-state pad">当前没有越阈或挂账的异常站点，全部电费记录已核实。</p>
    </template>

    <!-- ===================== Tab 3：分摊口径 ===================== -->
    <template v-else>
      <div class="policy-head">
        <div class="threshold-box">
          <label>环比涨幅阈值：
            <input v-model.number="thresholdInput" type="number" min="0.01" step="0.05" class="threshold-input" />
            <span class="cell-hint">（{{ percent(thresholdInput) }}，仅高于阈值才标异常）</span>
          </label>
          <button class="btn primary" type="button" :disabled="savingThreshold" @click="saveThreshold">
            {{ savingThreshold ? '重算中…' : '调整阈值并重算存量比对' }}
          </button>
        </div>
      </div>

      <h3 class="block-title">共站分摊口径</h3>
      <form class="filter-bar policy-form" @submit.prevent="savePolicy">
        <label class="filter-item">
          <span>所属站点</span>
          <input v-model="policySite" list="site-options" placeholder="站点名称" required />
        </label>
        <div class="operators-editor">
          <div v-for="(op, i) in policyOperators" :key="i" class="operator-row">
            <input v-model="op.运营商" placeholder="运营商名称（如：移动）" required />
            <input
              v-model.number="op.合同比例"
              type="number"
              min="0"
              step="0.05"
              placeholder="合同比例（小数）"
            />
            <button class="btn ghost" type="button" @click="policyOperators.splice(i, 1)">移除</button>
          </div>
          <button class="btn" type="button" @click="addOperator">增加一家运营商</button>
          <span class="cell-hint">比例之和不必等于 1，系统自动归一；全部填 0 表示合同未约定，按家数均摊。</span>
        </div>
        <button class="btn primary" type="button" @click="savePolicy">保存口径并重算该站点存量账单</button>
        <button class="btn ghost" type="button" @click="resetPolicy">清空表单</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>站点名称</th>
            <th>站点性质</th>
            <th>运营商及合同比例</th>
            <th>最近更新</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in policyRows" :key="String(p.id)">
            <td>{{ p.站点名称 }}</td>
            <td>{{ p.是否共站 ? '共站' : '独站' }}</td>
            <td>
              <span v-for="op in p.运营商" :key="op.运营商" class="tag tag-ratio">
                {{ op.运营商 }} {{ percent(op.合同比例) }}
              </span>
            </td>
            <td>{{ p.更新时间 || '—' }}</td>
            <td><button class="link" type="button" @click="editPolicy(p)">载入到表单</button></td>
          </tr>
          <tr v-if="!policyRows.length">
            <td colspan="5" class="empty-state">尚未登记分摊口径的站点，登记账单时默认独站全额承担</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- ===================== 登记 / 订正弹窗 ===================== -->
    <div v-if="formOpen" class="modal-mask" @click.self="formOpen = false">
      <div class="modal">
        <h3>{{ formMode === 'create' ? '登记电费记录' : `订正电费记录 ${formState.记录编号 ?? ''}` }}</h3>
        <p v-if="formMode === 'edit'" class="cell-hint">订正后按当前口径重算分摊与环比，订正前的分摊结果自动归入历史账单留档。</p>
        <div class="form-grid">
          <label v-if="formMode === 'create'">
            <span>记录编号</span>
            <input v-model="formState.记录编号" placeholder="留空自动生成 ELEC-序号" />
          </label>
          <label>
            <span>所属站点 *</span>
            <input v-model="formState.所属站点" list="site-options" required />
          </label>
          <label>
            <span>缴费月份 *</span>
            <input v-model="formState.缴费月份" type="month" required />
          </label>
          <label>
            <span>电费金额(元) *</span>
            <input v-model.number="formState.电费金额" type="number" min="0" step="0.01" required />
          </label>
          <label>
            <span>电表读数</span>
            <input v-model.number="formState.电表读数" type="number" />
          </label>
          <label>
            <span>总用电量(度)</span>
            <input v-model.number="formState.用电量" type="number" />
          </label>
          <label class="full">
            <span>票据编号</span>
            <input v-model="formState.票据编号" />
          </label>
        </div>

        <div class="meter-block">
          <div class="meter-head">
            <strong>各家分表用电量</strong>
            <span class="cell-hint">所有在册运营商都填了读数才按用电量分摊，任一缺读则按合同比例兜底。</span>
          </div>
          <div v-for="(m, i) in formState.分表读数" :key="i" class="operator-row">
            <input v-model="m.运营商" placeholder="运营商" />
            <input v-model.number="m.分表用电量" type="number" min="0" step="1" placeholder="本月分表用电量（度）" />
            <button class="btn ghost" type="button" @click="formState.分表读数.splice(i, 1)">移除</button>
          </div>
          <button class="btn" type="button" @click="formState.分表读数.push({ 运营商: '', 分表用电量: null })">补一条分表读数</button>
        </div>

        <div class="modal-actions">
          <button class="btn" type="button" @click="formOpen = false">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitForm">
            {{ submitting ? '提交中…' : '保存并自动比对/分摊' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Allocation = {
  运营商: string
  分表用电量: number | null
  分摊比例: number
  分摊金额: number
  分摊依据: string
}
type HistorySnapshot = {
  留档时间: string
  变更原因: string
  电费金额: number
  分摊依据?: string
  分摊明细: Allocation[]
}
type Bill = {
  id: number
  记录编号: string
  所属站点: string
  缴费月份: string
  电费金额: number
  电表读数?: number | null
  用电量?: number | null
  票据编号?: string
  已缴费: boolean
  已核实: boolean
  status: string
  异常标记: boolean
  异常说明: string
  环比涨幅: number | null
  分摊明细: Allocation[]
  分表读数?: Array<{ 运营商: string; 分表用电量: number | null }>
  历史账单: HistorySnapshot[]
}
type PendingSite = {
  所属站点: string
  异常账单数: number
  最近异常月份: string
  最近环比涨幅: number
  最近电费金额: number
  异常说明: string
  待核实账单: Array<{ id: number; 记录编号: string; 缴费月份: string; 电费金额: number; 环比涨幅: number; 异常说明: string; status: string }>
}
type Policy = {
  id: number
  站点名称: string
  是否共站: boolean
  运营商: Array<{ 运营商: string; 合同比例: number }>
  更新时间: string
}

const ENDPOINT = '/api/electricbill'
const statuses = ['待缴费', '已缴费', '电费异常', '已核实']
const tabs = [
  { key: 'bills', label: '账单比对' },
  { key: 'pending', label: '待核实清单' },
  { key: 'policy', label: '分摊口径' },
] as const
type TabKey = (typeof tabs)[number]['key']

const activeTab = ref<TabKey>('bills')
const rows = ref<Bill[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filters = reactive({ site: '', month: '', status: '', abnormal: '', keyword: '' })
const expanded = reactive(new Set<number>())
const historyId = ref<number | null>(null)
const pendingItems = ref<PendingSite[]>([])
const pendingOpenId = ref('')
const policyRows = ref<Policy[]>([])
const sites = ref<string[]>([])
const threshold = ref(0.5)
const thresholdInput = ref(0.5)
const savingThreshold = ref(false)
const message = ref('')
const messageOk = ref(true)

const stats = computed(() => [
  { label: '账单总数', value: total.value, warn: false },
  { label: '待缴费', value: rows.value.filter(r => r.status === '待缴费').length, warn: false },
  { label: '电费异常（本页）', value: rows.value.filter(r => r.异常标记).length, warn: true },
  { label: '异常站点待核实', value: pendingItems.value.length, warn: pendingItems.value.length > 0 },
])

function money(value: unknown): string {
  const n = Number(value ?? 0)
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function percent(value: unknown): string {
  const n = Number(value ?? 0)
  return `${(n * 100).toFixed(1)}%`
}
function statusClass(status: string): string {
  if (status === '已缴费') return 'tag-paid'
  if (status === '电费异常') return 'tag-abnormal'
  if (status === '已核实') return 'tag-done'
  return 'tag-pending'
}
function hasMissingMeter(row: Bill): boolean {
  return row.分摊明细.some(d => d.分表用电量 === null || d.分表用电量 === undefined)
}
function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
  window.setTimeout(() => { message.value = '' }, 5000)
}

function toggleDetail(id: number) {
  if (expanded.has(id)) expanded.delete(id)
  else expanded.add(id)
}
function toggleHistory(id: number) {
  historyId.value = historyId.value === id ? null : id
}
function changePage(delta: number) {
  page.value += delta
  void reload()
}
function resetFilters() {
  filters.site = ''
  filters.month = ''
  filters.status = ''
  filters.abnormal = ''
  filters.keyword = ''
  page.value = 1
  void reload()
}

async function reload() {
  const params = new URLSearchParams({ page: String(page.value), size: String(pageSize) })
  if (filters.site) params.set('site', filters.site)
  if (filters.month) params.set('month', filters.month)
  if (filters.status) params.set('status', filters.status)
  if (filters.abnormal) params.set('abnormal', filters.abnormal)
  if (filters.keyword) params.set('keyword', filters.keyword)
  const resp = await request(`${ENDPOINT}?${params.toString()}`)
  if (!resp.ok) {
    notify('电费记录列表读取失败', false)
    return
  }
  const payload = await resp.json()
  rows.value = (payload.items ?? []) as Bill[]
  total.value = payload.total ?? 0
}

async function reloadPending() {
  const resp = await request(`${ENDPOINT}/abnormal-pending`)
  if (resp.ok) {
    const payload = await resp.json()
    pendingItems.value = (payload.items ?? []) as PendingSite[]
  }
}

async function reloadPolicies() {
  const [settingsResp, sitesResp] = await Promise.all([
    request(`${ENDPOINT}/settings`),
    request(`${ENDPOINT}/sites`),
  ])
  if (settingsResp.ok) {
    const setting = await settingsResp.json()
    threshold.value = Number(setting.环比涨幅阈值 ?? 0.5)
    thresholdInput.value = threshold.value
  }
  if (sitesResp.ok) {
    const payload = await sitesResp.json()
    policyRows.value = (payload.items ?? []) as Policy[]
    sites.value = policyRows.value.map(p => p.站点名称)
  }
}

function switchTab(key: TabKey) {
  activeTab.value = key
  if (key === 'pending') void reloadPending()
  if (key === 'policy') void reloadPolicies()
}

async function runAction(row: Bill, action: string) {
  const resp = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action } }),
  })
  const payload = await resp.json().catch(() => ({ ok: false, message: '操作未生效' }))
  notify(payload.message ?? (resp.ok ? '操作成功' : '操作失败'), resp.ok && payload.ok !== false)
  await Promise.all([reload(), reloadPending()])
}

async function verifyById(id: number) {
  const resp = await request(`${ENDPOINT}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action: '核实确认' } }),
  })
  const payload = await resp.json().catch(() => ({ ok: false }))
  notify(payload.message ?? '已核实', resp.ok && payload.ok !== false)
  await Promise.all([reloadPending(), reload()])
}

// ----------------------- 阈值与分摊口径 -----------------------

async function saveThreshold() {
  if (!thresholdInput.value || thresholdInput.value <= 0) {
    notify('阈值需为大于 0 的小数（0.5 表示 50%）', false)
    return
  }
  savingThreshold.value = true
  const resp = await request(`${ENDPOINT}/settings/threshold`, {
    method: 'PUT',
    body: JSON.stringify({ 环比涨幅阈值: thresholdInput.value }),
  })
  const payload = await resp.json().catch(() => ({ ok: false, message: '阈值未更新' }))
  savingThreshold.value = false
  notify(payload.message, resp.ok && payload.ok !== false)
  if (resp.ok && payload.ok !== false) {
    threshold.value = thresholdInput.value
    await Promise.all([reload(), reloadPending()])
  }
}

const policySite = ref('')
const policyOperators = ref<Array<{ 运营商: string; 合同比例: number | null }>>([])

function addOperator() {
  policyOperators.value.push({ 运营商: '', 合同比例: null })
}
function resetPolicy() {
  policySite.value = ''
  policyOperators.value = []
}
function editPolicy(p: Policy) {
  policySite.value = p.站点名称
  policyOperators.value = p.运营商.map(op => ({ 运营商: op.运营商, 合同比例: op.合同比例 }))
}
async function savePolicy() {
  if (!policySite.value.trim()) {
    notify('请填写站点名称', false)
    return
  }
  const operators = policyOperators.value
    .filter(op => op.运营商.trim())
    .map(op => ({ 运营商: op.运营商.trim(), 合同比例: op.合同比例 ?? 0 }))
  if (!operators.length) {
    notify('至少登记一家运营商', false)
    return
  }
  const resp = await request(`${ENDPOINT}/sites/policy`, {
    method: 'PUT',
    body: JSON.stringify({ 站点名称: policySite.value.trim(), 运营商: operators }),
  })
  const payload = await resp.json().catch(() => ({ ok: false, message: '口径未保存' }))
  notify(payload.message, resp.ok && payload.ok !== false)
  if (resp.ok && payload.ok !== false) {
    await Promise.all([reloadPolicies(), reload(), reloadPending()])
  }
}

// ----------------------- 登记 / 订正 -----------------------

const formOpen = ref(false)
const submitting = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingId = ref<number | null>(null)
const formState = reactive({
  记录编号: '',
  所属站点: '',
  缴费月份: '',
  电费金额: null as number | null,
  电表读数: null as number | null,
  用电量: null as number | null,
  票据编号: '',
  分表读数: [] as Array<{ 运营商: string; 分表用电量: number | null }>,
})

function openCreate() {
  formMode.value = 'create'
  editingId.value = null
  Object.assign(formState, {
    记录编号: '',
    所属站点: filters.site || '',
    缴费月份: '',
    电费金额: null,
    电表读数: null,
    用电量: null,
    票据编号: '',
    分表读数: [],
  })
  formOpen.value = true
}

function openEdit(row: Bill) {
  formMode.value = 'edit'
  editingId.value = row.id
  Object.assign(formState, {
    记录编号: row.记录编号,
    所属站点: row.所属站点,
    缴费月份: row.缴费月份,
    电费金额: row.电费金额,
    电表读数: row.电表读数 ?? null,
    用电量: row.用电量 ?? null,
    票据编号: row.票据编号 ?? '',
    分表读数: (row.分表读数 ?? []).map(m => ({ ...m })),
  })
  formOpen.value = true
}

async function submitForm() {
  if (!formState.所属站点.trim() || !formState.缴费月份 || formState.电费金额 === null) {
    notify('所属站点、缴费月份、电费金额为必填', false)
    return
  }
  const values: Record<string, unknown> = {
    所属站点: formState.所属站点.trim(),
    缴费月份: formState.缴费月份,
    电费金额: formState.电费金额,
    电表读数: formState.电表读数,
    用电量: formState.用电量,
    票据编号: formState.票据编号,
    分表读数: formState.分表读数
      .filter(m => m.运营商.trim() && m.分表用电量 !== null)
      .map(m => ({ 运营商: m.运营商.trim(), 分表用电量: m.分表用电量 })),
  }
  if (formMode.value === 'create' && formState.记录编号.trim()) {
    values.记录编号 = formState.记录编号.trim()
  }
  submitting.value = true
  const url = formMode.value === 'create'
    ? ENDPOINT
    : `${ENDPOINT}/${editingId.value}`
  const resp = await request(url, {
    method: formMode.value === 'create' ? 'POST' : 'PUT',
    body: JSON.stringify({ values }),
  })
  const payload = await resp.json().catch(() => ({ ok: false, message: '提交失败' }))
  submitting.value = false
  notify(payload.message, resp.ok && payload.ok !== false)
  if (resp.ok && payload.ok !== false) {
    formOpen.value = false
    await Promise.all([reload(), reloadPending(), reloadPolicies()])
  }
}

onMounted(() => {
  void reload()
  void reloadPending()
  void reloadPolicies()
})
</script>

<style scoped>
.stat-warn { color: #b42318; }
.tabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid var(--border); }
.tab-btn { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 14px; color: var(--muted); border-bottom: 2px solid transparent; }
.tab-btn.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
.tab-badge { display: inline-block; min-width: 18px; padding: 0 5px; margin-left: 4px; border-radius: 9px; background: #fee4e2; color: #b42318; font-size: 11px; line-height: 18px; text-align: center; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.rate-bad { color: #b42318; font-weight: 600; }
.rate-ok { color: #067647; }
.row-abnormal { background: #fffbfa; }
.cell-note { max-width: 320px; font-size: 12px; color: #475467; }
.cell-hint { font-size: 12px; color: var(--muted); }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; }
.tag-pending { background: #f2f4f7; color: #475467; }
.tag-paid { background: #e6f4ea; color: #067647; }
.tag-abnormal { background: #fee4e2; color: #b42318; margin-right: 4px; }
.tag-done { background: #e0edff; color: #1f6feb; }
.tag-ratio { background: #eef2ff; color: #3538cd; margin: 0 4px 4px 0; }
.detail-cell { background: #fafbfc; padding: 10px 14px; }
.inner-table { width: 100%; border-collapse: collapse; margin-bottom: 6px; }
.inner-table th, .inner-table td { border: 1px solid var(--border); padding: 5px 8px; font-size: 12px; text-align: left; }
.history-card { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; background: #fff; }
.history-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; font-size: 12px; }
.history-share { display: inline-block; margin: 2px 12px 2px 0; font-size: 12px; color: #344054; }
.tip { background: #e6f4ea; border: 1px solid #a6dcb8; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.tip-error { background: #fef3f2; border-color: #fda29b; color: #b42318; }
.pad { padding: 24px; }
.pager { display: flex; gap: 8px; align-items: center; }
.pager .btn:disabled { opacity: 0.5; cursor: not-allowed; }
.policy-head { margin: 8px 0 14px; }
.threshold-box { display: flex; gap: 12px; align-items: center; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; }
.threshold-input { width: 90px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; }
.block-title { margin: 18px 0 8px; font-size: 14px; }
.policy-form { align-items: flex-start; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.operators-editor { display: flex; flex-direction: column; gap: 6px; }
.operator-row { display: flex; gap: 8px; }
.operator-row input { padding: 5px 8px; border: 1px solid var(--border); border-radius: 4px; min-width: 180px; }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 20px; width: 640px; max-height: 88vh; overflow: auto; }
.modal h3 { margin: 0 0 8px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }
.form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.form-grid label.full { grid-column: 1 / -1; }
.form-grid input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 4px; }
.meter-block { border-top: 1px dashed var(--border); padding-top: 10px; margin-top: 6px; }
.meter-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
</style>
