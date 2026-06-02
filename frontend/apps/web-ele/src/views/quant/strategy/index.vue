<script lang="ts" setup>
import { onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { useDebounceFn } from '@vueuse/core';

import {
  ElButton,
  ElCard,
  ElCol,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElRow,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import { Plus, Search, Trash2, Edit } from '@lucide/vue';

import type { Strategy } from '#/api/quant';
import { deleteStrategyApi, getStrategiesApi } from '#/api/quant';

const router = useRouter();

const searchKeyword = ref('');
const strategies = ref<Strategy[]>([]);
const loading = ref(false);

function formatDateTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

async function fetchStrategies() {
  loading.value = true;
  try {
    const search = searchKeyword.value.trim();
    strategies.value = await getStrategiesApi({
      search: search || undefined,
    });
  } catch {
    ElMessage.error('加载策略列表失败');
  } finally {
    loading.value = false;
  }
}

const debouncedFetch = useDebounceFn(fetchStrategies, 300);

onMounted(() => {
  fetchStrategies();
});

watch(searchKeyword, () => {
  debouncedFetch();
});

const handleCreate = () => {
  router.push('/quant/strategy/edit');
};

const handleEdit = (row: Strategy) => {
  router.push({ path: '/quant/strategy/edit', query: { id: String(row.id) } });
};

const handleDelete = (row: Strategy) => {
  ElMessageBox.confirm(`确定删除策略「${row.name}」吗？关联的回测历史将一并删除。`, '提示', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
    .then(async () => {
      try {
        await deleteStrategyApi(row.id);
        ElMessage.success('删除成功');
        await fetchStrategies();
      } catch {
        ElMessage.error('删除失败');
      }
    })
    .catch(() => {});
};
</script>

<template>
  <div class="strategy-list p-4">
    <ElCard shadow="never">
      <template #header>
        <ElRow justify="space-between" align="middle">
          <ElCol>
            <ElInput
              v-model="searchKeyword"
              placeholder="搜索策略名称或描述..."
              clearable
              style="width: 280px"
            >
              <template #prefix>
                <Search class="w-4 h-4" />
              </template>
            </ElInput>
          </ElCol>
          <ElCol>
            <ElButton type="primary" @click="handleCreate">
              <Plus class="w-4 h-4 mr-1" />
              新建策略
            </ElButton>
          </ElCol>
        </ElRow>
      </template>

      <ElTable v-loading="loading" :data="strategies" stripe empty-text="暂无策略，点击「新建策略」创建">
        <ElTableColumn prop="name" label="策略名称" min-width="200">
          <template #default="{ row }">
            <div>
              <div class="font-medium">{{ row.name }}</div>
              <div v-if="row.description" class="text-xs text-gray-400 mt-1">
                {{ row.description }}
              </div>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="created_at" label="创建时间" width="170">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </ElTableColumn>
        <ElTableColumn prop="updated_at" label="更新时间" width="170">
          <template #default="{ row }">
            {{ formatDateTime(row.updated_at) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click="handleEdit(row)">
              <Edit class="w-4 h-4 mr-1" />编辑
            </ElButton>
            <ElButton link type="danger" size="small" @click="handleDelete(row)">
              <Trash2 class="w-4 h-4" />
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.strategy-list {
  min-height: 100%;
}
</style>
