<script lang="ts" setup>
import { ref, reactive } from 'vue';

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
  ElOption,
  ElPagination,
  ElRow,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { Plus, Edit, Delete, Search } from 'lucide-vue-next';

interface User {
  id: number;
  username: string;
  nickname: string;
  email: string;
  phone: string;
  is_active: boolean;
  role: string;
  created_at: string;
}

const searchKeyword = ref('');
const currentPage = ref(1);
const pageSize = ref(10);

const users = ref<User[]>([
  { id: 1, username: 'admin', nickname: '管理员', email: 'admin@example.com', phone: '13800138000', is_active: true, role: '超级管理员', created_at: '2026-01-01' },
  { id: 2, username: 'zhangsan', nickname: '张三', email: 'zhangsan@example.com', phone: '13800138001', is_active: true, role: '普通用户', created_at: '2026-02-15' },
  { id: 3, username: 'lisi', nickname: '李四', email: 'lisi@example.com', phone: '13800138002', is_active: false, role: '普通用户', created_at: '2026-03-20' },
]);

const dialogVisible = ref(false);
const dialogTitle = ref('新增用户');
const isEdit = ref(false);

const userForm = reactive({
  username: '', nickname: '', email: '', phone: '', password: '', role: '普通用户', is_active: true,
});

const handleAdd = () => {
  dialogTitle.value = '新增用户';
  isEdit.value = false;
  Object.assign(userForm, { username: '', nickname: '', email: '', phone: '', password: '', role: '普通用户', is_active: true });
  dialogVisible.value = true;
};

const handleEdit = (row: User) => {
  dialogTitle.value = '编辑用户';
  isEdit.value = true;
  Object.assign(userForm, { username: row.username, nickname: row.nickname, email: row.email, phone: row.phone, role: row.role, is_active: row.is_active });
  dialogVisible.value = true;
};

const handleDelete = (row: User) => {
  ElMessageBox.confirm(`确定删除用户 "${row.nickname}" 吗？`, '提示', { type: 'warning' })
    .then(() => { users.value = users.value.filter(u => u.id !== row.id); ElMessage.success('删除成功'); });
};

const handleSubmit = () => {
  if (isEdit.value) { ElMessage.success('更新成功'); }
  else { const newId = users.value.length + 1; users.value.push({ id: newId, ...userForm, created_at: new Date().toISOString().slice(0, 10) } as User); ElMessage.success('创建成功'); }
  dialogVisible.value = false;
};
</script>

<template>
  <div class="user-management p-4">
    <ElCard shadow="never">
      <template #header>
        <ElRow justify="space-between" align="middle">
          <ElCol>
            <ElRow :gutter="12">
              <ElCol><ElInput v-model="searchKeyword" placeholder="搜索用户名/昵称..." clearable style="width:240px"><template #prefix><Search class="w-4 h-4" /></template></ElInput></ElCol>
            </ElRow>
          </ElCol>
          <ElCol><ElButton type="primary" @click="handleAdd"><Plus class="w-4 h-4 mr-1" />新增用户</ElButton></ElCol>
        </ElRow>
      </template>
      <ElTable :data="users" stripe>
        <ElTableColumn prop="username" label="用户名" width="120" />
        <ElTableColumn prop="nickname" label="昵称" width="100" />
        <ElTableColumn prop="email" label="邮箱" width="200" />
        <ElTableColumn prop="phone" label="手机号" width="140" />
        <ElTableColumn prop="role" label="角色" width="100" />
        <ElTableColumn prop="is_active" label="状态" width="80" align="center">
          <template #default="{ row }"><ElTag :type="row.is_active ? 'success' : 'danger'">{{ row.is_active ? '启用' : '禁用' }}</ElTag></template>
        </ElTableColumn>
        <ElTableColumn prop="created_at" label="创建时间" width="110" />
        <ElTableColumn label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click="handleEdit(row)"><Edit class="w-4 h-4 mr-1" />编辑</ElButton>
            <ElButton link type="danger" size="small" @click="handleDelete(row)"><Delete class="w-4 h-4" /></ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <ElPagination class="mt-4" v-model:current-page="currentPage" v-model:page-size="pageSize" :total="users.length" layout="total, prev, pager, next" />
    </ElCard>

    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="500px">
      <ElForm :model="userForm" label-width="80px">
        <ElFormItem label="用户名" required><ElInput v-model="userForm.username" /></ElFormItem>
        <ElFormItem label="昵称"><ElInput v-model="userForm.nickname" /></ElFormItem>
        <ElFormItem label="邮箱"><ElInput v-model="userForm.email" /></ElFormItem>
        <ElFormItem label="手机号"><ElInput v-model="userForm.phone" /></ElFormItem>
        <ElFormItem label="密码" v-if="!isEdit"><ElInput v-model="userForm.password" type="password" /></ElFormItem>
        <ElFormItem label="角色"><ElSelect v-model="userForm.role" style="width:100%"><ElOption label="超级管理员" value="超级管理员" /><ElOption label="普通用户" value="普通用户" /></ElSelect></ElFormItem>
        <ElFormItem label="状态"><ElSwitch v-model="userForm.is_active" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="dialogVisible=false">取消</ElButton><ElButton type="primary" @click="handleSubmit">确定</ElButton></template>
    </ElDialog>
  </div>
</template>

<style scoped>.user-management { min-height: 100%; }</style>
