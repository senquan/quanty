<script lang="ts" setup>
import { computed, onMounted, reactive, ref, watch } from 'vue';

import { useDebounceFn } from '@vueuse/core';

import { FilePenLine, Plus, Trash } from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCol,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElRow,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTree,
} from 'element-plus';

import type { Menu, MenuCreate } from '#/api/core/menus';
import {
  createMenuApi,
  deleteMenuApi,
  getMenusApi,
  updateMenuApi,
} from '#/api/core/menus';

const searchKeyword = ref('');
const menuList = ref<Menu[]>([]);
const loading = ref(false);
const submitting = ref(false);

const dialogVisible = ref(false);
const dialogTitle = ref('新增菜单');
const isEdit = ref(false);
const editId = ref(0);

const menuForm = reactive({
  name: '',
  type: 1,
  path: '',
  label: '',
  component: '',
  icon: '',
  oidx: 0,
  parent_id: 0,
  is_enabled: true,
  is_cached: false,
  is_hidden: false,
  permission: '',
});

const typeOptions = [
  { label: '目录', value: 0 },
  { label: '菜单', value: 1 },
  { label: '按钮', value: 2 },
];

function normalizeParentId(parentId?: number | null) {
  return parentId == null || parentId === 0 ? 0 : parentId;
}

const directoryOptions = computed(() =>
  menuList.value.filter((m) => m.type === 0),
);

const getTypeLabel = (type: number) => {
  const map: Record<number, string> = { 0: '目录', 1: '菜单', 2: '按钮' };
  return map[type] || '未知';
};

const getTypeTag = (
  type: number,
): 'danger' | 'info' | 'primary' | 'success' | 'warning' | undefined => {
  const map: Record<number, 'primary' | 'success' | 'warning'> = {
    0: 'primary',
    1: 'success',
    2: 'warning',
  };
  return map[type] || undefined;
};

interface MenuTreeNode {
  id: number;
  label: string;
  children: MenuTreeNode[];
}

const getTreeData = (): MenuTreeNode[] => {
  const buildTree = (parentId: number): MenuTreeNode[] => {
    return menuList.value
      .filter((m) => normalizeParentId(m.parent_id) === parentId)
      .toSorted((a, b) => a.oidx - b.oidx)
      .map((m) => ({
        id: m.id,
        label: m.label,
        children: buildTree(m.id),
      }));
  };
  return buildTree(0);
};

async function fetchMenus() {
  loading.value = true;
  try {
    const search = searchKeyword.value.trim();
    menuList.value = await getMenusApi({
      search: search || undefined,
    });
  } catch {
    ElMessage.error('加载菜单列表失败');
  } finally {
    loading.value = false;
  }
}

const debouncedFetch = useDebounceFn(fetchMenus, 300);

onMounted(() => {
  fetchMenus();
});

watch(searchKeyword, () => {
  debouncedFetch();
});

function resetForm() {
  Object.assign(menuForm, {
    name: '',
    type: 1,
    path: '',
    label: '',
    component: '',
    icon: '',
    oidx: 0,
    parent_id: 0,
    is_enabled: true,
    is_cached: false,
    is_hidden: false,
    permission: '',
  });
}

function toPayload(): MenuCreate {
  return {
    name: menuForm.name.trim(),
    type: menuForm.type,
    path: menuForm.path.trim(),
    label: menuForm.label.trim(),
    component: menuForm.component.trim() || undefined,
    icon: menuForm.icon.trim() || undefined,
    oidx: menuForm.oidx,
    parent_id: menuForm.parent_id,
    is_enabled: menuForm.is_enabled,
    is_cached: menuForm.is_cached,
    is_hidden: menuForm.is_hidden,
    permission: menuForm.permission.trim() || undefined,
  };
}

const handleAdd = () => {
  dialogTitle.value = '新增菜单';
  isEdit.value = false;
  editId.value = 0;
  resetForm();
  dialogVisible.value = true;
};

const handleEdit = (row: Menu) => {
  dialogTitle.value = '编辑菜单';
  isEdit.value = true;
  editId.value = row.id;
  Object.assign(menuForm, {
    name: row.name,
    type: row.type,
    path: row.path ?? '',
    label: row.label,
    component: row.component || '',
    icon: row.icon || '',
    oidx: row.oidx,
    parent_id: normalizeParentId(row.parent_id),
    is_enabled: row.is_enabled,
    is_cached: row.is_cached,
    is_hidden: row.is_hidden,
    permission: row.permission || '',
  });
  dialogVisible.value = true;
};

const handleDelete = (row: Menu) => {
  ElMessageBox.confirm(`确定删除菜单「${row.name}」吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(async () => {
      try {
        await deleteMenuApi(row.id);
        ElMessage.success('删除成功');
        await fetchMenus();
      } catch {
        ElMessage.error('删除失败');
      }
    })
    .catch(() => {});
};

const handleSubmit = async () => {
  if (!menuForm.name.trim()) {
    ElMessage.warning('请输入菜单名称');
    return;
  }
  if (!menuForm.label.trim()) {
    ElMessage.warning('请输入显示名称');
    return;
  }

  submitting.value = true;
  try {
    const payload = toPayload();
    if (isEdit.value) {
      await updateMenuApi(editId.value, payload);
      ElMessage.success('更新成功');
    } else {
      await createMenuApi(payload);
      ElMessage.success('创建成功');
    }
    dialogVisible.value = false;
    await fetchMenus();
  } catch {
    ElMessage.error(isEdit.value ? '更新失败' : '创建失败');
  } finally {
    submitting.value = false;
  }
};
</script>

<template>
  <div class="menu-management p-4">
    <ElRow :gutter="16">
      <ElCol :span="6">
        <ElCard shadow="never" header="菜单树">
          <ElInput
            v-model="searchKeyword"
            placeholder="搜索菜单..."
            class="mb-3"
            clearable
          />
          <ElTree
            v-loading="loading"
            :data="getTreeData()"
            :props="{ label: 'label', children: 'children' }"
            default-expand-all
            highlight-current
          />
        </ElCard>
      </ElCol>

      <ElCol :span="18">
        <ElCard shadow="never">
          <template #header>
            <ElRow justify="space-between" align="middle">
              <ElCol>
                <ElButton type="primary" @click="handleAdd">
                  <Plus class="w-4 h-4 mr-1" />新增菜单
                </ElButton>
              </ElCol>
            </ElRow>
          </template>

          <ElTable
            v-loading="loading"
            :data="menuList"
            stripe
            row-key="id"
            empty-text="暂无菜单数据"
          >
            <ElTableColumn prop="label" label="菜单名称" width="160" />
            <ElTableColumn prop="type" label="类型" width="80" align="center">
              <template #default="{ row }">
                <ElTag :type="getTypeTag(row.type)">{{ getTypeLabel(row.type) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="path" label="路由路径" min-width="200" />
            <ElTableColumn prop="component" label="组件路径" width="200" />
            <ElTableColumn prop="icon" label="图标" width="200" />
            <ElTableColumn prop="oidx" label="排序" width="60" align="center" />
            <ElTableColumn prop="is_enabled" label="状态" width="80" align="center">
              <template #default="{ row }">
                <ElTag :type="row.is_enabled ? 'success' : 'danger'">
                  {{ row.is_enabled ? '启用' : '禁用' }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <ElButton type="primary" @click="handleEdit(row)">
                  <FilePenLine class="w-4 h-4 mr-1" />编辑
                </ElButton>
                <ElButton type="danger" @click="handleDelete(row)">
                  <Trash class="w-4 h-4" />
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="600px">
      <ElForm :model="menuForm" label-width="100px">
        <ElFormItem label="上级菜单">
          <ElSelect v-model="menuForm.parent_id" style="width: 100%" placeholder="选择上级菜单">
            <ElOption label="顶级菜单" :value="0" />
            <ElOption
              v-for="m in directoryOptions"
              :key="m.id"
              :label="m.label"
              :value="m.id"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="菜单类型">
          <ElSelect v-model="menuForm.type" style="width: 100%">
            <ElOption v-for="t in typeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="菜单名称" required>
          <ElInput v-model="menuForm.name" placeholder="请输入菜单名称" />
        </ElFormItem>
        <ElFormItem label="显示名称" required>
          <ElInput v-model="menuForm.label" placeholder="请输入显示名称" />
        </ElFormItem>
        <ElFormItem label="路由路径">
          <ElInput v-model="menuForm.path" placeholder="如: /system/user" />
        </ElFormItem>
        <ElFormItem label="组件路径">
          <ElInput v-model="menuForm.component" placeholder="如: system/user/index" />
        </ElFormItem>
        <ElFormItem label="图标">
          <ElInput v-model="menuForm.icon" placeholder="如: lucide:users" />
        </ElFormItem>
        <ElFormItem label="排序">
          <ElInputNumber v-model="menuForm.oidx" :min="0" />
        </ElFormItem>
        <ElFormItem label="权限标识">
          <ElInput v-model="menuForm.permission" placeholder="如: system:user:list" />
        </ElFormItem>
        <ElFormItem label="是否启用">
          <ElSwitch v-model="menuForm.is_enabled" />
        </ElFormItem>
        <ElFormItem label="是否缓存">
          <ElSwitch v-model="menuForm.is_cached" />
        </ElFormItem>
        <ElFormItem label="是否隐藏">
          <ElSwitch v-model="menuForm.is_hidden" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="submitting" @click="handleSubmit">确定</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.menu-management {
  min-height: 100%;
}
</style>
