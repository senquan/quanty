<script lang="ts" setup>
import type { MentionPage, Stance } from '../types';

/**
 * 资讯抽取结果列表（**服务端分页，20 行一页**）
 *
 * 为什么不下发全量再前端切页：抽取结果随每日构建持续增长（已 800+ 条），
 * 全量下发既拖慢首屏，也会让筛选失真（只能筛到已下载的那一页）。
 * 故关键字 / 倾向 / 来源 / 分页全部下推到后端，这里只负责渲染当前页。
 *
 * 关键字输入做了 350ms 防抖：每敲一个字打一次接口既浪费又会把输入框卡住。
 */
import { onMounted, ref, watch } from 'vue';

import {
  ElInput,
  ElOption,
  ElPagination,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { newsService } from '../news-service';

const DEFAULT_PAGE_SIZE = 20;

const page = ref<MentionPage>({
  items: [],
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  sources: [],
  total: 0,
});
const loading = ref(false);

const searchQuery = ref('');
const debouncedQuery = ref('');
const selectedStance = ref<'all' | Stance>('all');
const selectedSource = ref<string>('all');
const currentPage = ref(1);
const pageSize = ref(DEFAULT_PAGE_SIZE);

async function load() {
  loading.value = true;
  try {
    page.value = await newsService.getMentions({
      page: currentPage.value,
      pageSize: pageSize.value,
      q: debouncedQuery.value.trim() || undefined,
      source: selectedSource.value,
      stance: selectedStance.value,
    });
    // 后端会夹紧 page（例如筛选后总页数变少），以服务端返回为准，
    // 否则会停在"第 8 页 0 条"这种空页面上。
    currentPage.value = page.value.page || 1;
  } finally {
    loading.value = false;
  }
}

let timer: ReturnType<typeof setTimeout> | undefined;
watch(searchQuery, (v) => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    debouncedQuery.value = v;
    currentPage.value = 1;
    load();
  }, 350);
});

// 改筛选条件一律回到第一页：停在第 5 页再改来源，几乎必然落在空页上
watch([selectedStance, selectedSource], () => {
  currentPage.value = 1;
  load();
});

function onPageChange(p: number) {
  currentPage.value = p;
  load();
}

function onSizeChange(s: number) {
  pageSize.value = s;
  currentPage.value = 1;
  load();
}

/** 外部（上传完成等）触发刷新：回到第一页重新拉 */
function reload() {
  currentPage.value = 1;
  return load();
}

onMounted(load);

defineExpose({ reload });

const stanceMeta: Record<Stance, { label: string; type: 'danger' | 'info' | 'success' }> = {
  bullish: { label: '看多', type: 'danger' },
  neutral: { label: '中性', type: 'info' },
  bearish: { label: '看空', type: 'success' },
};

function fmtTime(s: string): string {
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}
</script>

<template>
  <div class="news-list">
    <div class="toolbar">
      <ElInput
        v-model="searchQuery"
        placeholder="搜索标的 / 标题 / 论点 / 证据"
        clearable
        style="width: 280px"
      />
      <ElSelect v-model="selectedStance" style="width: 120px">
        <ElOption label="全部倾向" value="all" />
        <ElOption label="看多" value="bullish" />
        <ElOption label="中性" value="neutral" />
        <ElOption label="看空" value="bearish" />
      </ElSelect>
      <ElSelect v-model="selectedSource" style="width: 160px">
        <ElOption label="全部来源" value="all" />
        <ElOption v-for="s in page.sources" :key="s" :label="s" :value="s" />
      </ElSelect>
      <span class="count">共 {{ page.total }} 条</span>
    </div>

    <ElTable
      v-loading="loading"
      :data="page.items"
      stripe
      height="520"
      empty-text="暂无资讯抽取结果"
    >
      <ElTableColumn prop="symbol" label="标的" width="110" fixed>
        <template #default="{ row }">
          <span class="symbol">{{ row.symbol }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="stance" label="倾向" width="80">
        <template #default="{ row }">
          <ElTag :type="stanceMeta[row.stance as Stance].type" size="small" effect="dark">
            {{ stanceMeta[row.stance as Stance].label }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="confidence" label="置信" width="70">
        <template #default="{ row }">{{ (row.confidence * 100).toFixed(0) }}%</template>
      </ElTableColumn>
      <ElTableColumn prop="title" label="资讯标题" min-width="220" show-overflow-tooltip />
      <ElTableColumn prop="thesis" label="论点" min-width="220" show-overflow-tooltip />
      <ElTableColumn prop="evidence" label="证据" min-width="220" show-overflow-tooltip />
      <ElTableColumn prop="source" label="来源" width="120" />
      <ElTableColumn prop="publishedAt" label="时间" width="100">
        <template #default="{ row }">{{ fmtTime(row.publishedAt) }}</template>
      </ElTableColumn>
    </ElTable>

    <div class="pager">
      <ElPagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="page.total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
.news-list {
  padding: 4px;
}
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.count {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.symbol {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 600;
}
</style>
