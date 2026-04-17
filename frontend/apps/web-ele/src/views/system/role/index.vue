<script lang="ts" setup>
import { reactive, ref } from 'vue';

import { FilePenLine, KeyRound, Plus, Trash } from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCol,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElRow,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTree,
} from 'element-plus';

interface Role {
  id: number;
  name: string;
  code: string;
  description: string;
  is_active: boolean;
  menu_ids: number[];
  created_at: string;
}

const roles = ref<Role[]>([
  { id: 1, name: '超级管理员', code: 'admin', description: '拥有所有权限', is_active: true, menu_ids: [1,2,3,4,5,6,7,8,9], created_at: '2026-01-01' },
  { id: 2, name: '普通用户', code: 'user', description: '基本查看权限', is_active: true, menu_ids: [5,6,7,8,9], created_at: '2026-02-15' },
  { id: 3, name: '交易员', code: 'trader', description: '量化交易权限', is_active: true, menu_ids: [5,6,7,8,9], created_at: '2026-03-20' },
]);

const menuTree = ref([
  { id: 1, label: '系统管理', children: [
    { id: 2, label: '用户管理' },
    { id: 3, label: '角色管理' },
    { id: 4, label: '菜单管理' },
  ]},
  { id: 5, label: '量化交易', children: [
    { id: 6, label: '量化概览' },
    { id: 7, label: '策略管理' },
    { id: 8, label: '回测结果' },
    { id: 9, label: '交易管理' },
  ]},
]);

const dialogVisible = ref(false);
const dialogTitle = ref('新增角色');
const isEdit = ref(false);
const permissionDialogVisible = ref(false);
const currentRoleId = ref(0);

const roleForm = reactive({ name: '', code: '', description: '', is_active: true });
const selectedMenus = ref<number[]>([]);

const handleAdd = () => {
  dialogTitle.value = '新增角色';
  isEdit.value = false;
  Object.assign(roleForm, { name: '', code: '', description: '', is_active: true });
  dialogVisible.value = true;
};

const handleEdit = (row: Role) => {
  dialogTitle.value = '编辑角色';
  isEdit.value = true;
  Object.assign(roleForm, { name: row.name, code: row.code, description: row.description, is_active: row.is_active });
  dialogVisible.value = true;
};

const handleDelete = (row: Role) => {
  ElMessageBox.confirm(`确定删除角色 "${row.name}" 吗？`, '提示', { type: 'warning' })
    .then(() => { roles.value = roles.value.filter(r => r.id !== row.id); ElMessage.success('删除成功'); });
};

const handlePermission = (row: Role) => {
  currentRoleId.value = row.id;
  selectedMenus.value = [...row.menu_ids];
  permissionDialogVisible.value = true;
};

const handleSavePermission = () => {
  const role = roles.value.find(r => r.id === currentRoleId.value);
  if (role) { role.menu_ids = [...selectedMenus.value]; }
  ElMessage.success('权限分配成功');
  permissionDialogVisible.value = false;
};

const handleSubmit = () => {
  if (isEdit.value) { ElMessage.success('更新成功'); }
  else { const newId = roles.value.length + 1; roles.value.push({ id: newId, ...roleForm, menu_ids: [], created_at: new Date().toISOString().slice(0,10) } as Role); ElMessage.success('创建成功'); }
  dialogVisible.value = false;
};
</script>

<template>
  <div class="role-management p-4">
    <ElCard shadow="never">
      <template #header>
        <ElRow justify="space-between" align="middle">
          <ElCol><ElButton type="primary" @click="handleAdd"><Plus class="w-4 h-4 mr-1" />新增角色</ElButton></ElCol>
        </ElRow>
      </template>
      <ElTable :data="roles" stripe>
        <ElTableColumn prop="name" label="角色名称" width="140" />
        <ElTableColumn prop="code" label="角色编码" width="120" />
        <ElTableColumn prop="description" label="描述" />
        <ElTableColumn prop="is_active" label="状态" width="80" align="center">
          <template #default="{ row }"><ElTag :type="row.is_active ? 'success' : 'danger'">{{ row.is_active ? '启用' : '禁用' }}</ElTag></template>
        </ElTableColumn>
        <ElTableColumn prop="created_at" label="创建时间" width="110" />
        <ElTableColumn label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click="handleEdit(row)"><FilePenLine class="w-4 h-4 mr-1" />编辑</ElButton>
            <ElButton link type="success" size="small" @click="handlePermission(row)"><KeyRound class="w-4 h-4 mr-1" />权限</ElButton>
            <ElButton link type="danger" size="small" @click="handleDelete(row)"><Trash class="w-4 h-4" /></ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="500px">
      <ElForm :model="roleForm" label-width="80px">
        <ElFormItem label="角色名称" required><ElInput v-model="roleForm.name" /></ElFormItem>
        <ElFormItem label="角色编码" required><ElInput v-model="roleForm.code" /></ElFormItem>
        <ElFormItem label="描述"><ElInput v-model="roleForm.description" type="textarea" :rows="3" /></ElFormItem>
        <ElFormItem label="状态"><ElSwitch v-model="roleForm.is_active" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="dialogVisible = false">取消</ElButton><ElButton type="primary" @click="handleSubmit">确定</ElButton></template>
    </ElDialog>

    <ElDialog v-model="permissionDialogVisible" title="分配权限" width="400px">
      <ElTree :data="menuTree" show-checkbox node-key="id" :default-checked-keys="selectedMenus" :props="{ label: 'label', children: 'children' }" @check="(_, { checkedKeys }) => selectedMenus = checkedKeys.map(Number)" />
      <template #footer><ElButton @click="permissionDialogVisible = false">取消</ElButton><ElButton type="primary" @click="handleSavePermission">保存</ElButton></template>
    </ElDialog>
  </div>
</template>

<style scoped>.role-management { min-height: 100%; }</style>
