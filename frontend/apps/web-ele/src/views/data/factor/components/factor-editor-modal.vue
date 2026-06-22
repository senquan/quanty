<script lang="ts" setup>
import { ref, watch } from 'vue';

import { ElButton, ElDialog, ElFormItem, ElInput, ElOption, ElSelect, ElTag } from 'element-plus';

import type { Factor, FactorCategory, UpdateFrequency } from '../types';
import { generateFactorPerformance } from '../mock/factor-data';

const props = defineProps<{
  visible: boolean;
  factor: Factor | null;
}>();

const emit = defineEmits<{
  'update:visible': [value: boolean];
  save: [factor: Factor];
}>();

const form = ref({
  name: '',
  code: '',
  category: 'momentum' as FactorCategory,
  frequency: 'Daily' as UpdateFrequency,
  description: '',
  formula: '(close - delay(close, 20)) / std(close, 20)',
  dataSources: ['日K收盘价'] as string[],
});

const sandboxMetrics = ref<{
  icMean: number;
  ir: number;
  sharpe: number;
  maxDd: number;
} | null>(null);

const availableSources = [
  '日K收盘价',
  '日K最低价',
  '日K最高价',
  '日K开盘价',
  '日K成交量',
  '合并利润表/营业收入',
  '合并利润表/净利润',
  '合并财务报表/股东权益',
  '股本公告与分红派息公告',
  '第三方股吧舆情监控量',
];

watch(
  () => [props.visible, props.factor],
  () => {
    if (props.visible && props.factor) {
      form.value = {
        name: props.factor.name,
        code: props.factor.code,
        category: props.factor.category,
        frequency: props.factor.frequency,
        description: props.factor.description,
        formula: props.factor.formula,
        dataSources: [...props.factor.dataSources],
      };
      sandboxMetrics.value = {
        icMean: props.factor.icMean,
        ir: props.factor.ir,
        sharpe: props.factor.sharpeRatio,
        maxDd: props.factor.maxDrawdown * 100,
      };
    } else if (props.visible) {
      form.value = {
        name: '',
        code: '',
        category: 'momentum',
        frequency: 'Daily',
        description: '',
        formula: '(close - delay(close, 20)) / std(close, 20)',
        dataSources: ['日K收盘价'],
      };
      sandboxMetrics.value = null;
    }
  },
);

function handleClose() {
  emit('update:visible', false);
}

function handleRunSandbox() {
  const mock = generateFactorPerformance(
    form.value.name || 'Sandbox',
    form.value.code || 'SANDBOX',
    form.value.formula,
    form.value.category,
  );
  sandboxMetrics.value = {
    icMean: mock.icMean,
    ir: mock.ir,
    sharpe: mock.sharpeRatio,
    maxDd: mock.maxDrawdown * 100,
  };
}

function handleSave() {
  if (!form.value.name || !form.value.code || !form.value.formula) return;

  const generated = generateFactorPerformance(
    form.value.name,
    form.value.code.toUpperCase(),
    form.value.formula,
    form.value.category,
  );

  const result: Factor = {
    ...generated,
    id: props.factor?.id || `user_${form.value.code.toLowerCase()}_${Date.now()}`,
    frequency: form.value.frequency,
    dataSources: form.value.dataSources,
    author: props.factor?.author || 'user',
    createdAt: props.factor?.createdAt || new Date().toISOString().split('T')[0]!,
    description: form.value.description || generated.description,
  };

  emit('save', result);
  handleClose();
}

function appendFormula(text: string) {
  form.value.formula += text;
  sandboxMetrics.value = null;
}

const formulaSnippets = [
  'close',
  'high',
  'volume',
  'delay(X, 10)',
  'std(X, 20)',
  'rsi(X, 14)',
  'mean(X, 5)',
  'ema(X, 12)',
];
</script>

<template>
  <ElDialog
    :model-value="visible"
    :title="factor ? '编辑量化因子设定' : '创建新型量化因子'"
    width="800px"
    top="5vh"
    @update:model-value="emit('update:visible', $event)"
  >
    <div class="grid grid-cols-2 gap-6">
      <!-- Left: basic fields -->
      <div class="space-y-4">
        <ElFormItem label="因子名称" required>
          <ElInput v-model="form.name" placeholder="例如: 20日价格波动强度" />
        </ElFormItem>

        <div class="grid grid-cols-2 gap-4">
          <ElFormItem label="因子代码" required>
            <ElInput
              v-model="form.code"
              placeholder="PE_TTM"
              :disabled="!!factor"
              class="font-mono"
              @input="form.code = form.code.replace(/[^a-zA-Z0-9_\-]/g, '').toUpperCase()"
            />
          </ElFormItem>
          <ElFormItem label="因子类别">
            <ElSelect v-model="form.category" class="w-full">
              <ElOption label="趋势动量类" value="momentum" />
              <ElOption label="价格波动率" value="volatility" />
              <ElOption label="价值估值类" value="value" />
              <ElOption label="财务成长型" value="growth" />
              <ElOption label="资金与情绪" value="sentiment" />
              <ElOption label="算法自定义" value="custom" />
            </ElSelect>
          </ElFormItem>
        </div>

        <ElFormItem label="更新频率">
          <ElSelect v-model="form.frequency" class="w-full">
            <ElOption label="日频更新 (Daily)" value="Daily" />
            <ElOption label="周频更新 (Weekly)" value="Weekly" />
            <ElOption label="月频评估 (Monthly)" value="Monthly" />
            <ElOption label="季度复核 (Quarterly)" value="Quarterly" />
          </ElSelect>
        </ElFormItem>

        <ElFormItem label="因子说明">
          <ElInput
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="简述此量化因子设计的经济学逻辑..."
          />
        </ElFormItem>

        <ElFormItem label="数据来源">
          <div class="flex flex-wrap gap-2">
            <ElTag
              v-for="src in availableSources"
              :key="src"
              :type="form.dataSources.includes(src) ? 'primary' : 'info'"
              :effect="form.dataSources.includes(src) ? 'dark' : 'plain'"
              class="cursor-pointer text-xs"
              @click="
                form.dataSources.includes(src)
                  ? (form.dataSources = form.dataSources.filter((s) => s !== src))
                  : form.dataSources.push(src)
              "
            >
              {{ src }}
            </ElTag>
          </div>
        </ElFormItem>
      </div>

      <!-- Right: formula + sandbox -->
      <div class="space-y-4">
        <ElFormItem label="因子计算公式" required>
          <div class="w-full">
            <div class="flex flex-wrap gap-1 mb-2 p-2 bg-gray-50 rounded-lg border border-gray-100">
              <span class="text-[11px] text-gray-400 mr-1 self-center">快速插入:</span>
              <ElButton
                v-for="snippet in formulaSnippets"
                :key="snippet"
                size="small"
                text
                class="font-mono text-xs"
                @click="appendFormula(snippet)"
              >
                {{ snippet }}
              </ElButton>
            </div>
            <ElInput
              v-model="form.formula"
              type="textarea"
              :rows="4"
              class="font-mono"
              placeholder="(close - delay(close, 20)) / std(close, 20)"
            />
          </div>
        </ElFormItem>

        <!-- Sandbox -->
        <div class="p-4 rounded-xl border border-dashed border-gray-200 bg-gray-50/50 min-h-[200px]">
          <div class="flex items-center justify-between mb-3 pb-2 border-b border-gray-100">
            <span class="text-xs font-bold text-gray-600">沙盒模拟器</span>
            <ElButton type="success" size="small" @click="handleRunSandbox">
              编译并运算
            </ElButton>
          </div>

          <div v-if="!sandboxMetrics" class="flex flex-col items-center justify-center py-8 text-gray-400 text-xs">
            点击"编译并运算"按钮计算因子有效性参数
          </div>

          <div v-else class="grid grid-cols-2 gap-3">
            <div class="p-2 border border-gray-100 bg-white rounded-lg text-center">
              <div class="text-[9px] text-gray-400">预期 IC 均值</div>
              <div
                class="text-xs font-mono font-bold mt-0.5"
                :class="sandboxMetrics.icMean >= 0 ? 'text-emerald-500' : 'text-rose-500'"
              >
                {{ sandboxMetrics.icMean >= 0 ? '+' : '' }}{{ sandboxMetrics.icMean.toFixed(3) }}
              </div>
            </div>
            <div class="p-2 border border-gray-100 bg-white rounded-lg text-center">
              <div class="text-[9px] text-gray-400">信息比率 (IR)</div>
              <div class="text-xs font-mono font-bold mt-0.5 text-gray-700">
                {{ sandboxMetrics.ir.toFixed(2) }}
              </div>
            </div>
            <div class="p-2 border border-gray-100 bg-white rounded-lg text-center">
              <div class="text-[9px] text-gray-400">夏普比率</div>
              <div class="text-xs font-mono font-bold mt-0.5 text-gray-700">
                {{ sandboxMetrics.sharpe.toFixed(2) }}
              </div>
            </div>
            <div class="p-2 border border-gray-100 bg-white rounded-lg text-center">
              <div class="text-[9px] text-gray-400">最大回撤率</div>
              <div class="text-xs font-mono font-bold mt-0.5 text-rose-500">
                {{ sandboxMetrics.maxDd.toFixed(1) }}%
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <ElButton @click="handleClose">取消</ElButton>
      <ElButton type="primary" @click="handleSave">
        {{ factor ? '保存修改' : '保存并上架因子' }}
      </ElButton>
    </template>
  </ElDialog>
</template>
