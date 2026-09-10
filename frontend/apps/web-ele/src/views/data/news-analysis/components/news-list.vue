<script lang="ts" setup>
import { computed, ref } from 'vue';

import { ElInput, ElOption, ElSelect, ElTable, ElTableColumn, ElTag } from 'element-plus';

import type { NewsMention, Stance } from '../types';

const props = defineProps<{ mentions: NewsMention[] }>();

const searchQuery = ref('');
const selectedStance = ref<Stance | 'all'>('all');
const selectedSource = ref<string>('all');

const sourceOptions = computed(() => {
  const set = new Set(props.mentions.map((m) => m.source));
  return Array.from(set).sort();
});

const filtered = computed(() =>
  props.mentions.filter((m) => {
    const q = searchQuery.value.trim().toLowerCase();
    const matches =
      !q ||
      m.symbol.toLowerCase().includes(q) ||
      m.title.toLowerCase().includes(q) ||
      m.thesis.toLowerCase().includes(q) ||
      m.evidence.toLowerCase().includes(q);
    const matchesStance = selectedStance.value === 'all' || m.stance === selectedStance.value;
    const matchesSource = selectedSource.value === 'all' || m.source === selectedSource.value;
    return matches && matchesStance && matchesSource;
  }),
);

const stanceMeta: Record<Stance, { label: string; type: 'success' | 'info' | 'danger' }> = {
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
        <ElOption v-for="s in sourceOptions" :key="s" :label="s" :value="s" />
      </ElSelect>
      <span class="count">共 {{ filtered.length }} 条</span>
    </div>

    <ElTable :data="filtered" stripe height="560" empty-text="暂无资讯抽取结果">
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
.symbol {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 600;
}
</style>
