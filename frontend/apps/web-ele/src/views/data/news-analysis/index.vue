<script lang="ts" setup>
import type { AuthorProfile } from './types';

import { onMounted, ref } from 'vue';

import { ElAlert, ElTabPane, ElTabs } from 'element-plus';

import ArticleUpload from './components/article-upload.vue';
import AuthorProfiles from './components/author-profiles.vue';
import NewsList from './components/news-list.vue';
import RsshubManager from './components/rsshub-manager.vue';
import { newsService } from './news-service';

const profiles = ref<AuthorProfile[]>([]);
const loading = ref(false);
const activeTab = ref('mentions');

// 资讯抽取由 NewsList 自己分页拉取（服务端分页），上传完成后只需让它回第一页重拉
const newsList = ref<InstanceType<typeof NewsList> | null>(null);

async function loadProfiles() {
  loading.value = true;
  try {
    profiles.value = await newsService.getProfiles();
  } finally {
    loading.value = false;
  }
}

function onUploadDone() {
  newsList.value?.reload();
  loadProfiles();
}

onMounted(loadProfiles);
</script>

<template>
  <div class="news-page p-4">
    <ElAlert
      v-if="profiles.some((p) => p.sampleInsufficient)"
      type="warning"
      :closable="false"
      show-icon
      class="mb-3"
      title="样本与准确度说明"
      description="样本量普遍 <30、accuracy 待 raw_bars 后续行情（约 10 月初）自动补算，结论仅供参考。"
    />

    <ElTabs v-model="activeTab" type="border-card">
      <ElTabPane label="资讯抽取" name="mentions">
        <NewsList ref="newsList" />
      </ElTabPane>
      <ElTabPane label="作者 · 来源画像" name="profiles">
        <AuthorProfiles :profiles="profiles" />
      </ElTabPane>
      <ElTabPane label="文章投喂" name="upload">
        <ArticleUpload @done="onUploadDone" />
      </ElTabPane>
      <ElTabPane label="RSSHub 源" name="rsshub">
        <RsshubManager />
      </ElTabPane>
    </ElTabs>
  </div>
</template>

<style scoped>
.news-page {
  min-height: 100%;
}
.mb-3 {
  margin-bottom: 12px;
}
</style>
