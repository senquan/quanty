<script lang="ts" setup>
import { onMounted, reactive, ref, watch } from 'vue';

import { useDebounceFn } from '@vueuse/core';

import { FilePenLine, Plus, Search, Trash } from '@lucide/vue';
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

import type { Role } from '#/api/core/roles';
import { getRolesApi } from '#/api/core/roles';
import type { SystemUser, UserCreate } from '#/api/core/user';
import {
  createUserApi,
  deleteUserApi,
  getUsersApi,
  updateUserApi,
} from '#/api/core/user';

const searchKeyword = ref('');
const currentPage = ref(1);
const pageSize = ref(10);
const total = ref(0);

const users = ref<SystemUser[]>([]);
const roles = ref<Role[]>([]);
const loading = ref(false);
const submitting = ref(false);

const dialogVisible = ref(false);
const dialogTitle = ref('新增用户');
const isEdit = ref(false);
const editId = ref(0);

const userForm = reactive({
  username: '',
  nickname: '',
  email: '',
  phone: '',
  password: '',
  role_id: null as number | null,
  is_active: true,
});

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

function getRoleName(row: SystemUser) {
  return row.role?.name ?? '-';
}

async function fetchRoles() {
  try {
    roles.value = await getRolesApi({ skip: 0, limit: 100 });
  } catch {
    roles.value = [];
  }
}

async function fetchUsers() {
  loading.value = true;
  try {
    const search = searchKeyword.value.trim();
    const result = await getUsersApi({
      skip: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
      search: search || undefined,
    });
    users.value = result.items;
    total.value = result.total;
  } catch {
    ElMessage.error('加载用户列表失败');
  } finally {
    loading.value = false;
  }
}

const debouncedFetch = useDebounceFn(() => {
  currentPage.value = 1;
  fetchUsers();
}, 300);

onMounted(async () => {
  await fetchRoles();
  await fetchUsers();
});

watch(searchKeyword, () => {
  debouncedFetch();
});

watch([currentPage, pageSize], () => {
  fetchUsers();
});

function resetForm() {
  Object.assign(userForm, {
    username: '',
    nickname: '',
    email: '',
    phone: '',
    password: '',
    role_id: null,
    is_active: true,
  });
}

function toPayload(): UserCreate {
  return {
    username: userForm.username.trim(),
    email: userForm.email.trim(),
    password: userForm.password,
    full_name: userForm.nickname.trim() || undefined,
    phone: userForm.phone.trim() || undefined,
    is_active: userForm.is_active,
    role_id: userForm.role_id,
  };
}

const handleAdd = () => {
  dialogTitle.value = '新增用户';
  isEdit.value = false;
  editId.value = 0;
  resetForm();
  dialogVisible.value = true;
};

const handleEdit = (row: SystemUser) => {
  dialogTitle.value = '编辑用户';
  isEdit.value = true;
  editId.value = row.id;
  Object.assign(userForm, {
    username: row.username,
    nickname: row.full_name ?? '',
    email: row.email,
    phone: row.phone ?? '',
    password: '',
    role_id: row.role_id ?? null,
    is_active: row.is_active,
  });
  dialogVisible.value = true;
};

const handleDelete = (row: SystemUser) => {
  const displayName = row.full_name || row.username;
  ElMessageBox.confirm(`确定删除用户「${displayName}」吗？`, '提示', {
    type: 'warning',
    confirmButtonText: '确定',
    cancelButtonText: '取消',
  })
    .then(async () => {
      try {
        await deleteUserApi(row.id);
        ElMessage.success('删除成功');
        if (users.value.length === 1 && currentPage.value > 1) {
          currentPage.value -= 1;
        }
        await fetchUsers();
      } catch {
        ElMessage.error('删除失败');
      }
    })
    .catch(() => {});
};

const handleSubmit = async () => {
  if (!userForm.username.trim()) {
    ElMessage.warning('请输入用户名');
    return;
  }
  if (!userForm.email.trim()) {
    ElMessage.warning('请输入邮箱');
    return;
  }
  if (!isEdit.value && !userForm.password) {
    ElMessage.warning('请输入密码');
    return;
  }

  submitting.value = true;
  try {
    if (isEdit.value) {
      const { password: _, ...updateData } = toPayload();
      await updateUserApi(editId.value, updateData);
      ElMessage.success('更新成功');
    } else {
      await createUserApi(toPayload());
      ElMessage.success('创建成功');
    }
    dialogVisible.value = false;
    await fetchUsers();
  } catch {
    ElMessage.error(isEdit.value ? '更新失败' : '创建失败');
  } finally {
    submitting.value = false;
  }
};
</script>

<template>
  <div class="user-management p-4">
    <ElCard shadow="never">
      <template #header>
        <ElRow justify="space-between" align="middle">
          <ElCol>
            <ElRow :gutter="12">
              <ElCol>
                <ElInput
                  v-model="searchKeyword"
                  placeholder="搜索用户名/昵称/邮箱..."
                  clearable
                  style="width: 280px"
                >
                  <template #prefix>
                    <Search class="w-4 h-4" />
                  </template>
                </ElInput>
                <ElButton type="primary" class="ml-2" @click="handleAdd">
                  <Plus class="w-4 h-4 mr-1" />新增用户
                </ElButton>
              </ElCol>
            </ElRow>
          </ElCol>
        </ElRow>
      </template>

      <ElTable v-loading="loading" :data="users" stripe empty-text="暂无用户数据">
        <ElTableColumn prop="username" label="用户名" width="140" />
        <ElTableColumn label="昵称" width="120">
          <template #default="{ row }">{{ row.full_name || '-' }}</template>
        </ElTableColumn>
        <ElTableColumn prop="email" label="邮箱" min-width="200" />
        <ElTableColumn prop="phone" label="手机号" width="140">
          <template #default="{ row }">{{ row.phone || '-' }}</template>
        </ElTableColumn>
        <ElTableColumn label="角色" width="120">
          <template #default="{ row }">{{ getRoleName(row) }}</template>
        </ElTableColumn>
        <ElTableColumn prop="is_active" label="状态" width="80" align="center">
          <template #default="{ row }">
            <ElTag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '启用' : '禁用' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
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

      <ElPagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        class="mt-4"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
      />
    </ElCard>

    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="500px">
      <ElForm :model="userForm" label-width="80px">
        <ElFormItem label="用户名" required>
          <ElInput v-model="userForm.username" :disabled="isEdit" />
        </ElFormItem>
        <ElFormItem label="昵称">
          <ElInput v-model="userForm.nickname" />
        </ElFormItem>
        <ElFormItem label="邮箱" required>
          <ElInput v-model="userForm.email" />
        </ElFormItem>
        <ElFormItem label="手机号">
          <ElInput v-model="userForm.phone" />
        </ElFormItem>
        <ElFormItem v-if="!isEdit" label="密码" required>
          <ElInput v-model="userForm.password" type="password" show-password />
        </ElFormItem>
        <ElFormItem label="角色">
          <ElSelect
            v-model="userForm.role_id"
            style="width: 100%"
            clearable
            placeholder="请选择角色"
          >
            <ElOption
              v-for="role in roles"
              :key="role.id"
              :label="role.name"
              :value="role.id"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="状态">
          <ElSwitch v-model="userForm.is_active" />
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
.user-management {
  min-height: 100%;
}
</style>
