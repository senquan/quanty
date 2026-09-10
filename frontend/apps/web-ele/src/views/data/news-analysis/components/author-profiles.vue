<script lang="ts" setup>
import type { AuthorProfile } from '../types';

import { computed } from 'vue';

import { ElProgress, ElTable, ElTableColumn, ElTag, ElTooltip } from 'element-plus';

const props = defineProps<{ profiles: AuthorProfile[] }>();

const sorted = computed(() =>
  props.profiles.toSorted((a, b) => b.totalMentions - a.totalMentions),
);

function bullPct(p: AuthorProfile): number {
  const t = p.totalMentions || 1;
  return Math.round((p.stanceDist.bullish / t) * 100);
}
function bearPct(p: AuthorProfile): number {
  const t = p.totalMentions || 1;
  return Math.round((p.stanceDist.bearish / t) * 100);
}

// P2-5 样本量红线：accuracy 样本 < 30 显示「样本不足」而非数字；0 表示行情窗口未到（待行情）
const ACC_REDLINE = 30;
function accuracyLabel(p: AuthorProfile): string {
  const n = p.accuracySampleSize || 0;
  if (n <= 0) return '待行情';
  if (n < ACC_REDLINE) return '样本不足';
  return `${n} 样本·20/60d`;
}
function accuracyInsufficient(p: AuthorProfile): boolean {
  return (p.accuracySampleSize || 0) < ACC_REDLINE;
}

// ---- P4-4 风格总结 ----
function hasStyle(p: AuthorProfile): boolean {
  return Boolean(p.styleSummary);
}
/** 未生成总结时的占位文案（区分「样本太少没跑」与「尚未生成」） */
function stylePlaceholder(p: AuthorProfile): string {
  if (p.styleStatus === 'skipped') return '样本不足·未生成';
  if (p.styleStatus === 'api_fail') return 'LLM 调用失败';
  if (p.styleStatus === 'schema_fail') return '输出不合规';
  return '未生成';
}
function styleTip(p: AuthorProfile): string {
  const parts: string[] = [p.styleSummary || ''];
  if (p.styleSectors?.length) parts.push(`覆盖行业：${p.styleSectors.join('、')}`);
  if (p.styleCaveats) parts.push(`局限：${p.styleCaveats}`);
  return parts.filter(Boolean).join('\n');
}
</script>

<template>
  <div class="author-profiles">
    <ElTable :data="sorted" stripe empty-text="暂无画像数据">
      <ElTableColumn prop="profileKey" label="画像" min-width="150" fixed>
        <template #default="{ row }">
          <div class="pk">
            <span class="name">{{ row.profileKey }}</span>
            <ElTag
              :type="row.profileType === 'source' ? 'primary' : 'warning'"
              size="small"
              effect="plain"
            >
              {{ row.profileType === 'source' ? '来源' : '作者' }}
            </ElTag>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="totalMentions" label="提及数" width="100" sortable />
      <ElTableColumn prop="totalDocs" label="文档数" width="100" sortable />
      <ElTableColumn prop="uniqueSymbols" label="标的数" width="100" sortable />
      <ElTableColumn label="看多 / 看空占比" width="200">
        <template #default="{ row }">
          <ElProgress
            :percentage="bullPct(row)"
            :stroke-width="14"
            :show-text="false"
            color="#e5484d"
          />
          <div class="pct-row">
            <span class="bull">看多 {{ bullPct(row) }}%</span>
            <span class="bear">看空 {{ bearPct(row) }}%</span>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="Top 标的" min-width="200">
        <template #default="{ row }">
          <span class="syms">{{ (row.topSymbols || []).join('  ') }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="样本状态" width="110">
        <template #default="{ row }">
          <ElTag v-if="row.sampleInsufficient" type="warning" size="small" effect="dark">
            样本不足
          </ElTag>
          <ElTag v-else type="success" size="small" effect="dark">充足</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="风格总结（LLM）" min-width="280">
        <template #default="{ row }">
          <div v-if="hasStyle(row)">
            <div class="tags">
              <ElTag
                v-for="t in row.styleTags || []"
                :key="t"
                size="small"
                effect="plain"
                type="info"
              >
                {{ t }}
              </ElTag>
            </div>
            <ElTooltip :content="styleTip(row)" placement="top" :show-after="200">
              <div class="summary">{{ row.styleSummary }}</div>
            </ElTooltip>
            <div v-if="row.styleConfidence !== null && row.styleConfidence !== undefined" class="conf">
              可靠度 {{ (row.styleConfidence * 100).toFixed(0) }}%
            </div>
          </div>
          <span v-else class="muted">{{ stylePlaceholder(row) }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="准确度" width="100" align="center">
        <template #default="{ row }">
          <span :class="accuracyInsufficient(row) ? 'muted' : 'ok'">{{ accuracyLabel(row) }}</span>
        </template>
      </ElTableColumn>
    </ElTable>
  </div>
</template>

<style scoped>
.author-profiles {
  padding: 4px;
}
.pk {
  display: flex;
  align-items: center;
  gap: 8px;
}
.name {
  font-weight: 600;
}
.pct-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  margin-top: 2px;
}
.bull {
  color: #e5484d;
}
.bear {
  color: #46a758;
}
.syms {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 4px;
}
.summary {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-regular);
  cursor: help;
}
.conf {
  margin-top: 2px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.ok {
  color: var(--el-color-success);
  font-weight: 600;
}
</style>
