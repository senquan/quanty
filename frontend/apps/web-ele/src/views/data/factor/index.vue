<script lang="ts" setup>
import type { Factor, FactorCategory } from './types';

import { onMounted, ref } from 'vue';

import { ElMessage, ElMessageBox, ElTabPane, ElTabs } from 'element-plus';

import BacktestStudio from './components/backtest-studio.vue';
import CleanerService from './components/cleaner-service.vue';
import CorrelationHeatmap from './components/correlation-heatmap.vue';
import EfficacyDeepDive from './components/efficacy-deep-dive.vue';
import FactorEditorModal from './components/factor-editor-modal.vue';
import FactorLibrary from './components/factor-library.vue';
import RegistryLibrary from './components/registry-library.vue';
import { factorService } from './factor-service';

// Global state
const factors = ref<Factor[]>([]);
const selectedFactor = ref<Factor | null>(null);
const activeTab = ref('library');

// Editor modal state
const editorVisible = ref(false);
const editingFactor = ref<Factor | null>(null);

// Load factors
async function loadFactors() {
  factors.value = await factorService.getFactors();
  if (factors.value.length > 0 && !selectedFactor.value) {
    selectedFactor.value = factors.value[0]!;
  }
}

// Factor actions
function handleSelectFactor(factor: Factor) {
  selectedFactor.value = factor;
  activeTab.value = 'efficacy';
}

function handleEditFactor(factor: Factor) {
  editingFactor.value = factor;
  editorVisible.value = true;
}

function handleAddFactor() {
  editingFactor.value = null;
  editorVisible.value = true;
}

async function handleDeleteFactor(id: string) {
  await ElMessageBox.confirm('确认删除该因子？此操作不可恢复。', '删除确认', {
    type: 'warning',
  });
  await factorService.deleteFactor(id);
  await loadFactors();
  ElMessage.success('因子已删除');
}

async function handleSaveFactor(factor: Factor) {
  const exists = factors.value.find((f) => f.code === factor.code);
  await (exists ? factorService.updateFactor(exists.id, factor) : factorService.createFactor(factor));
  await loadFactors();
  selectedFactor.value = factor;
  ElMessage.success('因子已保存');
}

async function handleAIGenerate(category: FactorCategory) {
  const factor = await factorService.generateAIFactor(category);
  await loadFactors();
  selectedFactor.value = factor;
  ElMessage.success(`AI 因子 ${factor.name} 已生成`);
}

onMounted(loadFactors);
</script>

<template>
  <div class="factor-page p-4">
    <ElTabs v-model="activeTab" type="border-card">
      <ElTabPane label="因子底册库" name="library">
        <FactorLibrary
          :factors="factors"
          :selected-factor-id="selectedFactor?.id"
          @select-factor="handleSelectFactor"
          @edit-factor="handleEditFactor"
          @delete-factor="handleDeleteFactor"
          @add-factor="handleAddFactor"
          @ai-generate="handleAIGenerate"
        />
      </ElTabPane>

      <ElTabPane label="多维评测详情" name="efficacy">
        <EfficacyDeepDive
          :factors="factors"
          :selected-factor="selectedFactor"
          @update:selected-factor="selectedFactor = $event"
        />
      </ElTabPane>

      <ElTabPane label="共线性探查" name="correlation">
        <CorrelationHeatmap :factors="factors" />
      </ElTabPane>

      <ElTabPane label="一键组合回测" name="backtest">
        <BacktestStudio :factors="factors" />
      </ElTabPane>

      <ElTabPane label="聚合因子底册" name="registry">
        <RegistryLibrary />
      </ElTabPane>

      <ElTabPane label="数据清洗服务" name="cleaner">
        <CleanerService />
      </ElTabPane>
    </ElTabs>

    <!-- Factor Editor Modal -->
    <FactorEditorModal
      v-model:visible="editorVisible"
      :factor="editingFactor"
      @save="handleSaveFactor"
    />
  </div>
</template>

<style scoped>
.factor-page {
  min-height: 100%;
}
</style>
