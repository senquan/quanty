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
  /* 布局把可用内容高度写入 --vben-content-height（ResizeObserver 维护，见
     packages/@core/composables/src/use-layout-style.ts），直接用它而不是 100% 继承——
     父级高度不确定时 height:100% 会退化成 auto，导致怎么都撑不满。
     兜底 100%：脱离布局单独使用时仍有合理表现。 */
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: var(--vben-content-height, 100%);
}

/* ElTabs 吃掉页面剩余高度，并把高度一路传导到 tab pane（资讯抽取据此撑满） */
.news-page :deep(.el-tabs) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
.news-page :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
}
.news-page :deep(.el-tab-pane) {
  height: 100%;
  /* 必须给 overflow：pane 现在是**定高**盒子，像「作者·来源画像」这种内容 table
     未设 height、会一直长高——没有 overflow 就会被裁掉且不出现滚动条。 */
  overflow: auto;
}

.mb-3 {
  /* 别让提示条被 flex 压缩（tabs 用 flex:1 吸收剩余空间） */
  flex: none;
  margin-bottom: 12px;
}
</style>
