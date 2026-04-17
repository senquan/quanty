<script lang="ts" setup>
import { reactive, ref } from 'vue';

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

interface MenuItem {
  id: number;
  name: string;
  type: number; // 0: 目录, 1: 菜单, 2: 按钮
  path: string;
  label: string;
  component?: string;
  icon?: string;
  oidx: number;
  parent_id?: number;
  is_enabled: boolean;
  is_cached: boolean;
  is_hidden: boolean;
  permission?: string;
}

const searchKeyword = ref('');
const menuList = ref<MenuItem[]>([
  { id: 1, name: '系统管理', type: 0, path: '/system', label: '系统管理', icon: 'lucide:settings', oidx: 1, parent_id: 0, is_enabled: true, is_cached: false, is_hidden: false },
  { id: 2, name: '用户管理', type: 1, path: '/system/user', label: '用户管理', component: 'system/user/index', icon: 'lucide:users', oidx: 1, parent_id: 1, is_enabled: true, is_cached: true, is_hidden: false },
  { id: 3, name: '角色管理', type: 1, path: '/system/role', label: '角色管理', component: 'system/role/index', icon: 'lucide:shield', oidx: 2, parent_id: 1, is_enabled: true, is_cached: true, is_hidden: false },
  { id: 4, name: '菜单管理', type: 1, path: '/system/menu', label: '菜单管理', component: 'system/menu/index', icon: 'lucide:menu', oidx: 3, parent_id: 1, is_enabled: true, is_cached: true, is_hidden: false },
  { id: 5, name: '量化交易', type: 0, path: '/quant', label: '量化交易', icon: 'lucide:bar-chart-3', oidx: 10, parent_id: 0, is_enabled: true, is_cached: false, is_hidden: false },
  { id: 6, name: '量化概览', type: 1, path: '/quant/dashboard', label: '量化概览', component: 'quant/dashboard/index', icon: 'lucide:layout-dashboard', oidx: 1, parent_id: 5, is_enabled: true, is_cached: false, is_hidden: false },
  { id: 7, name: '策略管理', type: 1, path: '/quant/strategy', label: '策略管理', component: 'quant/strategy/index', icon: 'lucide:file-code-2', oidx: 2, parent_id: 5, is_enabled: true, is_cached: false, is_hidden: false },
  { id: 8, name: '回测结果', type: 1, path: '/quant/backtest', label: '回测结果', component: 'quant/backtest/index', icon: 'lucide:trending-up', oidx: 3, parent_id: 5, is_enabled: true, is_cached: false, is_hidden: false },
  { id: 9, name: '交易管理', type: 1, path: '/quant/trading', label: '交易管理', component: 'quant/trading/index', icon: 'lucide:arrow-left-right', oidx: 4, parent_id: 5, is_enabled: true, is_cached: false, is_hidden: false },
]);

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

const getTypeLabel = (type: number) => {
  const map: Record<number, string> = { 0: '目录', 1: '菜单', 2: '按钮' };
  return map[type] || '未知';
};

const getTypeTag = (type: number): "danger" | "info" | "primary" | "success" | "warning" | undefined => {
  const map: Record<number, "primary" | "success" | "warning"> = { 0: 'primary', 1: 'success', 2: 'warning' };
  return map[type] || undefined;
};

const getTreeData = () => {
  const buildTree = (parentId: number): any[] => {
    return menuList.value
      .filter(m => m.parent_id === parentId)
      .toSorted((a, b) => a.oidx - b.oidx)
      .map(m => ({
        id: m.id,
        label: m.label,
        children: buildTree(m.id),
      }));
  };
  return buildTree(0);
};

const handleAdd = () => {
  dialogTitle.value = '新增菜单';
  isEdit.value = false;
  Object.assign(menuForm, {
    name: '', type: 1, path: '', label: '', component: '',
    icon: '', oidx: 0, parent_id: 0, is_enabled: true,
    is_cached: false, is_hidden: false, permission: '',
  });
  dialogVisible.value = true;
};

const handleEdit = (row: MenuItem) => {
  dialogTitle.value = '编辑菜单';
  isEdit.value = true;
  editId.value = row.id;
  Object.assign(menuForm, {
    name: row.name, type: row.type, path: row.path, label: row.label,
    component: row.component || '', icon: row.icon || '', oidx: row.oidx,
    parent_id: row.parent_id || 0, is_enabled: row.is_enabled,
    is_cached: row.is_cached, is_hidden: row.is_hidden, permission: row.permission || '',
  });
  dialogVisible.value = true;
};

const handleDelete = (row: MenuItem) => {
  ElMessageBox.confirm(`确定删除菜单 "${row.name}" 吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(() => {
    menuList.value = menuList.value.filter(m => m.id !== row.id);
    ElMessage.success('删除成功');
  });
};

const handleSubmit = () => {
  if (isEdit.value) {
    const idx = menuList.value.findIndex(m => m.id === editId.value);
    if (idx !== -1) {
      Object.assign(menuList.value[idx]!, { ...menuForm });
    }
    ElMessage.success('更新成功');
  } else {
    const newId = Math.max(...menuList.value.map(m => m.id)) + 1;
    menuList.value.push({ id: newId, ...menuForm } as MenuItem);
    ElMessage.success('创建成功');
  }
  dialogVisible.value = false;
};
</script>

<template>
  <div class="menu-management p-4">
    <ElRow :gutter="16">
      <!-- 左侧：菜单树 -->
      <ElCol :span="6">
        <ElCard shadow="never" header="菜单树">
          <ElInput v-model="searchKeyword" placeholder="搜索菜单..." class="mb-3" clearable />
          <ElTree
            :data="getTreeData()"
            :props="{ label: 'label', children: 'children' }"
            default-expand-all
            highlight-current
          />
        </ElCard>
      </ElCol>

      <!-- 右侧：菜单列表 -->
      <ElCol :span="18">
        <ElCard shadow="never">
          <template #header>
            <ElRow justify="space-between" align="middle">
              <ElCol>
                <ElButton type="primary" @click="handleAdd"><Plus class="w-4 h-4 mr-1" />新增菜单</ElButton>
              </ElCol>
            </ElRow>
          </template>

          <ElTable :data="menuList" stripe row-key="id" default-expand-all>
            <ElTableColumn prop="label" label="菜单名称" width="160" />
            <ElTableColumn prop="type" label="类型" width="80" align="center">
              <template #default="{ row }">
                <ElTag :type="getTypeTag(row.type)">{{ getTypeLabel(row.type) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="path" label="路由路径" width="200" />
            <ElTableColumn prop="component" label="组件路径" width="220" />
            <ElTableColumn prop="icon" label="图标" width="150" />
            <ElTableColumn prop="oidx" label="排序" width="60" align="center" />
            <ElTableColumn prop="is_enabled" label="状态" width="80" align="center">
              <template #default="{ row }">
                <ElTag :type="row.is_enabled ? 'success' : 'danger'">
                  {{ row.is_enabled ? '启用' : '禁用' }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <ElButton link type="primary" size="small" @click="handleEdit(row)">
                  <FilePenLine class="w-4 h-4 mr-1" />编辑
                </ElButton>
                <ElButton link type="danger" size="small" @click="handleDelete(row)">
                  <Trash class="w-4 h-4" />
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>

    <!-- 新增/编辑弹窗 -->
    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="600px">
      <ElForm :model="menuForm" label-width="100px">
        <ElFormItem label="上级菜单">
          <ElSelect v-model="menuForm.parent_id" style="width: 100%" placeholder="选择上级菜单">
            <ElOption label="顶级菜单" :value="0" />
            <ElOption
              v-for="m in menuList.filter(m => m.type === 0)"
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
        <ElButton type="primary" @click="handleSubmit">确定</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.menu-management {
  min-height: 100%;
}
</style>
