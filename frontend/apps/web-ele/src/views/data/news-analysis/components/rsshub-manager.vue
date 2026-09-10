<script lang="ts" setup>
import type {
  RsshubSource,
  RsshubStatus,
  RsshubTestResult,
} from '../types';

/**
 * P4-3 RSSHub 源管理
 *
 * RSSHub 是**第三方中继**，稳定性与合规性都不保证（设计文档 §7.1），因此：
 *   - 新增的源**默认停用**，必须先点「测试」确认能拉通，再手动启用；
 *   - 健康状态如实展示（dc 侧成功也标 degraded），不粉饰成"正常"；
 *   - 已入库过文档的源**不允许删除**（会破坏 doc_mentions 的溯源），只能停用。
 *
 * 端点全部经主后端转发 dc（前端不直连 dc），业务规则在 dc 侧，这里只做交互。
 */
import { computed, onMounted, reactive, ref } from 'vue';

import {
  ElAlert,
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElPagination,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTooltip,
} from 'element-plus';

import { newsService } from '../news-service';

const status = ref<null | RsshubStatus>(null);
const sources = ref<RsshubSource[]>([]);
const loading = ref(false);
const running = ref(false);
const testing = ref(false);
const submitting = ref(false);
const testResult = ref<null | RsshubTestResult>(null);

// ---- 新增 / 编辑 ----
const dialogVisible = ref(false);
const editingId = ref<null | number>(null);
const form = reactive({ name: '', url: '', enabled: false, credibility: 'low' });

// ---- 列表分页（源数量级很小，客户端分页即可）----
const page = ref(1);
const pageSize = ref(20);
const pagedSources = computed(() =>
  sources.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value),
);

async function loadStatus() {
  try {
    status.value = await newsService.getRsshubStatus();
  } catch {
    status.value = null; // 顶部警示条由 status===null 兜底呈现
  }
}

async function loadSources() {
  loading.value = true;
  try {
    const res = await newsService.listRsshubSources();
    sources.value = res.sources || [];
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  await Promise.all([loadStatus(), loadSources()]);
}

function openCreate() {
  editingId.value = null;
  form.name = '';
  form.url = '';
  form.enabled = false;
  form.credibility = 'low';
  testResult.value = null;
  dialogVisible.value = true;
}

function openEdit(row: RsshubSource) {
  editingId.value = row.id;
  form.name = row.name;
  form.url = row.url;
  form.enabled = row.enabled;
  form.credibility = row.credibility || 'low';
  testResult.value = null;
  dialogVisible.value = true;
}

/** 试探当前填写的 URL（不入库、不写 health），确认能拉通再保存 */
async function testUrl() {
  const url = form.url.trim();
  if (!url) {
    ElMessage.warning('请先填写 feed URL');
    return;
  }
  testing.value = true;
  try {
    testResult.value = await newsService.testRsshubUrl(url);
  } finally {
    testing.value = false;
  }
}

/** 对已登记的源复测一次（不入库），用于"之前能拉、现在拉不通了"的排查 */
async function testRow(row: RsshubSource) {
  testing.value = true;
  try {
    const res = await newsService.testRsshubUrl(row.url);
    if (res.ok) {
      ElMessage.success(
        `可拉通：解析到 ${res.count} 条（${res.latencyMs}ms）` +
          (res.titles.length > 0 ? `，例：${res.titles[0]}` : ''),
      );
    } else {
      ElMessage.error(`拉取失败：${res.error || '未知错误'}`);
    }
  } finally {
    testing.value = false;
  }
}

async function submit() {
  if (!form.name.trim() || !form.url.trim()) {
    ElMessage.warning('源名与 feed URL 都不能为空');
    return;
  }
  submitting.value = true;
  try {
    if (editingId.value === null) {
      await newsService.addRsshubSource({
        credibility: form.credibility,
        enabled: form.enabled,
        name: form.name.trim(),
        url: form.url.trim(),
      });
      ElMessage.success('已登记（默认停用，确认可拉取后再启用）');
    } else {
      await newsService.updateRsshubSource(editingId.value, {
        credibility: form.credibility,
        enabled: form.enabled,
        name: form.name.trim(),
        url: form.url.trim(),
      });
      ElMessage.success('已保存');
    }
    dialogVisible.value = false;
    await refresh();
  } finally {
    submitting.value = false;
  }
}

async function toggleEnabled(row: RsshubSource, val: unknown) {
  const next = Boolean(val);
  const prev = row.enabled;
  row.enabled = next; // 先乐观更新，失败回滚
  try {
    await newsService.updateRsshubSource(row.id, { enabled: next });
    if (status.value) {
      status.value.enabledCount += next ? 1 : -1;
    }
  } catch (error) {
    row.enabled = prev;
    ElMessage.error(`启停失败：${(error as Error)?.message || error}`);
  }
}

async function remove(row: RsshubSource) {
  try {
    await ElMessageBox.confirm(
      `确认删除源「${row.name}」？该源下已有 ${row.docCount} 篇文档时 dc 会拒绝删除。`,
      '删除确认',
      { type: 'warning' },
    );
  } catch {
    return; // 用户取消
  }
  try {
    await newsService.deleteRsshubSource(row.id);
    ElMessage.success('已删除');
    await refresh();
  } catch (error) {
    // dc 返回 409 是**预期行为**（有文档不许删），提示改成停用即可
    ElMessage.error(`删除失败：${(error as Error)?.message || error}`);
  }
}

async function runOnce() {
  running.value = true;
  try {
    const res = await newsService.runRsshubIngest();
    const failed = res.failed_sources ?? 0;
    ElMessage.success(
      `本轮完成：源 ${res.sources} 个，拉取 ${res.fetched} 条，新增 ${res.new} 条，失败源 ${failed} 个`,
    );
    await refresh();
  } finally {
    running.value = false;
  }
}

type TagType = 'danger' | 'info' | 'success' | 'warning';

const healthMeta: Record<string, { label: string; type: TagType }> = {
  ok: { label: '正常', type: 'success' },
  // 第三方中继即便这次拉通也只标 degraded，如实呈现
  degraded: { label: '降级（第三方中继）', type: 'warning' },
  partial: { label: '部分', type: 'warning' },
  failed: { label: '失败', type: 'danger' },
};

function healthOf(status: string | undefined): { label: string; type: TagType } {
  return (
    healthMeta[status || ''] ?? {
      label: status || '未知',
      type: 'info',
    }
  );
}

function fmtTime(s: string): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}

onMounted(refresh);
</script>

<template>
  <div class="rsshub-manager">
    <ElAlert
      v-if="!status || !status.configured"
      type="warning"
      :closable="false"
      show-icon
      class="mb-3"
      title="RSSHub 接入未配置"
      description="未设置 INTEL_RSSHUB_BASE_URL 时，rsshub://<路由> 形式的源无法解析。请先在 data-cleaner 配置 base URL（自建或第三方实例），或直接使用完整 feed URL。"
    />
    <ElAlert
      v-else-if="status.total === 0"
      type="info"
      :closable="false"
      show-icon
      class="mb-3"
      title="尚未登记任何 RSSHub 源"
      :description="`实例地址 ${status.baseUrl}。RSSHub 为第三方中继，稳定性与合规性不保证，登记后默认停用——先点「测试」确认能拉通，再手动启用。`"
    />

    <div class="toolbar">
      <ElButton type="primary" @click="openCreate">新增源</ElButton>
      <ElButton :loading="running" :disabled="!status?.configured" @click="runOnce">
        立即拉取一轮
      </ElButton>
      <ElButton :loading="loading" @click="refresh">刷新</ElButton>
      <span v-if="status" class="meta">
        实例：<code>{{ status.baseUrl || '（未配置）' }}</code>
        · 源 {{ status.total }} 个 / 已启用 {{ status.enabledCount }} 个
      </span>
    </div>

    <ElTable v-loading="loading" :data="pagedSources" stripe empty-text="暂无 RSSHub 源">
      <ElTableColumn prop="name" label="源名" min-width="140" show-overflow-tooltip />
      <ElTableColumn label="路由 / URL" min-width="260">
        <template #default="{ row }">
          <div class="url-cell">
            <span>{{ row.url }}</span>
            <span v-if="row.resolvedUrl && row.resolvedUrl !== row.url" class="resolved">
              → {{ row.resolvedUrl }}
            </span>
            <span v-else-if="row.resolveError" class="err">（{{ row.resolveError }}）</span>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="启用" width="90">
        <template #default="{ row }">
          <ElSwitch
            :model-value="row.enabled"
            @update:model-value="(v) => toggleEnabled(row, v)"
          />
        </template>
      </ElTableColumn>
      <ElTableColumn label="可信度" width="90">
        <template #default="{ row }">
          <ElTag size="small" :type="row.credibility === 'low' ? 'info' : 'success'">
            {{ row.credibility || '—' }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="文档数" width="90" prop="docCount" />
      <ElTableColumn label="最近健康" min-width="200">
        <template #default="{ row }">
          <div v-if="row.health?.status" class="health">
            <ElTag size="small" :type="healthOf(row.health.status).type">
              {{ healthOf(row.health.status).label }}
            </ElTag>
            <span class="dim">{{ fmtTime(row.health.checkedAt) }}</span>
            <span v-if="row.health.latencyMs" class="dim">{{ row.health.latencyMs }}ms</span>
            <ElTooltip v-if="row.health.error" :content="row.health.error" placement="top">
              <span class="err">错误详情</span>
            </ElTooltip>
          </div>
          <span v-else class="dim">从未拉取</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" @click="openEdit(row)">编辑</ElButton>
          <ElButton link type="primary" @click="testRow(row)">测试</ElButton>
          <ElButton link type="danger" @click="remove(row)">删除</ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pager">
      <ElPagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="sources.length"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
      />
    </div>

    <ElDialog
      v-model="dialogVisible"
      :title="editingId === null ? '新增 RSSHub 源' : '编辑 RSSHub 源'"
      width="560px"
    >
      <ElForm label-width="88px">
        <ElFormItem label="源名" required>
          <ElInput v-model="form.name" placeholder="如：公众号-分红养老之路" />
        </ElFormItem>
        <ElFormItem label="Feed URL" required>
          <ElInput
            v-model="form.url"
            placeholder="rsshub://wechat/ershicimi/xxxx 或 http://host:1200/..."
          />
        </ElFormItem>
        <ElFormItem label="可信度">
          <ElSelect v-model="form.credibility" style="width: 160px">
            <ElOption label="low（第三方中继）" value="low" />
            <ElOption label="medium" value="medium" />
            <ElOption label="high" value="high" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="启用">
          <ElSwitch v-model="form.enabled" />
          <span class="dim ml-2">停用状态下不会被定时/手动摄取拉取</span>
        </ElFormItem>
      </ElForm>

      <div class="test-row">
        <ElButton :loading="testing" @click="testUrl">测试连接（不入库）</ElButton>
        <span v-if="testResult" :class="testResult.ok ? 'ok' : 'err'">
          <template v-if="testResult.ok">
            可拉通：解析到 {{ testResult.count }} 条 · {{ testResult.latencyMs }}ms
            <span v-if="testResult.titles.length > 0" class="dim">
              例：{{ testResult.titles[0] }}
            </span>
          </template>
          <template v-else>拉取失败：{{ testResult.error || '未知错误' }}</template>
        </span>
      </div>

      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="submitting" @click="submit">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.rsshub-manager {
  padding: 4px;
}
.mb-3 {
  margin-bottom: 12px;
}
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.meta {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.url-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}
.resolved,
.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.err {
  color: var(--el-color-danger);
  font-size: 12px;
}
.ok {
  color: var(--el-color-success);
  font-size: 12px;
}
.health {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.test-row {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 8px;
}
.ml-2 {
  margin-left: 8px;
}
</style>
