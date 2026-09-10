<script lang="ts" setup>
import type { AuthorProfile, NewsMention } from './types';

import { onMounted, ref } from 'vue';

import { ElAlert, ElTabPane, ElTabs } from 'element-plus';

import ArticleUpload from './components/article-upload.vue';
import AuthorProfiles from './components/author-profiles.vue';
import NewsList from './components/news-list.vue';
import { newsService } from './news-service';

const mentions = ref<NewsMention[]>([]);
const profiles = ref<AuthorProfile[]>([]);
const loading = ref(false);
const activeTab = ref('mentions');

async function load() {
  loading.value = true;
  try {
    const [m, p] = await Promise.all([newsService.getMentions(), newsService.getProfiles()]);
    mentions.value = m;
    profiles.value = p;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
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
      description="当前为前端 mock 数据，形态对齐 intel 模块真实产出。样本量普遍 <30、accuracy 待 raw_bars 后续行情（约 10 月初）自动补算，结论仅供参考。"
    />

    <ElTabs v-model="activeTab" type="border-card">
      <ElTabPane label="资讯抽取" name="mentions">
        <NewsList :mentions="mentions" />
      </ElTabPane>
      <ElTabPane label="作者 · 来源画像" name="profiles">
        <AuthorProfiles :profiles="profiles" />
      </ElTabPane>
      <ElTabPane label="文章投喂" name="upload">
        <ArticleUpload @done="load" />
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
