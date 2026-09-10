<script lang="ts" setup>
/**
 * P4-1 人工投喂：批量上传文章文件
 *
 * 支持 html/htm/mhtml（浏览器导出、剪藏）/ txt、md（纯文本）/ csv、txt（URL 清单），
 * 以及把这些打包后的 zip（服务端自动解压，包内文件名中文按 GBK 还原）。
 * 上传只完成「入库」，后续仍需理解层抽取（抽取→画像→因子）才进入全链路。
 * dry-run 只解析不写库，用来先验解析质量再决定是否真入库。
 */
import { computed, ref } from 'vue';

import {
  ElAlert,
  ElButton,
  ElForm,
  ElFormItem,
  ElMessage,
  ElOption,
  ElRadio,
  ElRadioGroup,
  ElSelect,
  ElSwitch,
  ElTag,
  ElUpload,
} from 'element-plus';
import type { UploadUserFile } from 'element-plus';

import type { IntelUploadResult } from '../types';

import { newsService } from '../news-service';

const emit = defineEmits<{ done: [] }>();

const ACCEPT = '.html,.htm,.mhtml,.txt,.md,.csv,.zip';

const fileList = ref<UploadUserFile[]>([]);
const sourceName = ref('');
const sourceType = ref<'manual' | 'wechat'>('wechat');
const dryRun = ref(false);
// 入库后立即抽取：省掉再登服务器敲脚本，但会产生 LLM 成本，故默认关
const extract = ref(false);
const submitting = ref(false);
const result = ref<IntelUploadResult | null>(null);

// ElUpload 的 raw 是 UploadRawFile（File + uid 等附加字段），结构上可直接当 File 用
const rawFiles = computed<File[]>(
  () => fileList.value.map((item) => item.raw).filter(Boolean) as File[],
);

const canSubmit = computed(() => rawFiles.value.length > 0 && !submitting.value);

function clearFiles() {
  fileList.value = [];
  result.value = null;
}

async function submit() {
  if (!rawFiles.value.length) {
    ElMessage.warning('请先选择要导入的文件');
    return;
  }
  submitting.value = true;
  result.value = null;
  try {
    const res = await newsService.uploadArticles({
      dryRun: dryRun.value,
      extract: extract.value && !dryRun.value,
      files: rawFiles.value,
      sourceName: sourceName.value.trim(),
      sourceType: sourceType.value,
    });
    result.value = res;

    if (res.failed > 0) {
      ElMessage.warning(
        `导入完成，但有 ${res.failed} 个文件失败（见下方明细）`,
      );
    } else if (res.dryRun) {
      ElMessage.success(`解析成功：${res.accepted} 个文件（未写库）`);
    } else if (res.extraction?.status === 'ok') {
      ElMessage.success(
        `导入完成：新增 ${res.new} 篇，抽取 ${res.extraction.extracted ?? 0} 条观点`,
      );
    } else {
      ElMessage.success(
        `导入完成：新增 ${res.new} 篇，重复 ${res.dup} 篇，转载 ${res.reposts} 篇`,
      );
    }
    if (!res.dryRun && res.new > 0) {
      emit('done'); // 有新增 → 通知父页刷新列表
    }
  } catch (e: any) {
    ElMessage.error(`上传失败：${e?.message ?? e}`);
  } finally {
    submitting.value = false;
  }
}

function statusTag(status: string) {
  if (status === 'ok') return { text: '成功', type: 'success' as const };
  if (status === 'dry_run') return { text: '试解析', type: 'info' as const };
  return { text: '失败', type: 'danger' as const };
}
</script>

<template>
  <div class="upload-panel">
    <ElAlert
      type="info"
      :closable="false"
      show-icon
      class="mb-3"
      title="投喂说明"
      description="支持浏览器导出的 html / mhtml、剪藏网页、纯文本 txt / md、每行一个 URL 的 txt / csv 清单，以及把这些打包后的 zip（服务端自动解压，中文文件名按 GBK 还原，嵌套 zip 不二次解压）。同一篇文章重复上传不会重复入库（按内容 hash 去重）。投喂后文章仅完成入库，需经理解层抽取才会进入画像与因子。"
    />

    <ElForm label-width="110px" class="mb-3">
      <ElFormItem label="源名">
        <ElSelect
          v-model="sourceName"
          allow-create
          clearable
          filterable
          placeholder="如公众号名；留空记为「上传」"
          class="w-80"
        >
          <ElOption label="（留空，记为『上传』）" value="" />
        </ElSelect>
      </ElFormItem>

      <ElFormItem label="来源类型">
        <ElRadioGroup v-model="sourceType">
          <ElRadio value="wechat">公众号</ElRadio>
          <ElRadio value="manual">其他（手动投喂）</ElRadio>
        </ElRadioGroup>
        <span class="tip">解析方式相同，仅影响溯源标记</span>
      </ElFormItem>

      <ElFormItem label="只试解析">
        <ElSwitch v-model="dryRun" />
        <span class="tip">开启后只解析不写库，用于先验解析质量</span>
      </ElFormItem>

      <ElFormItem label="立即抽取">
        <ElSwitch v-model="extract" :disabled="dryRun" />
        <span class="tip">
          入库后立刻跑理解层抽取（约 ¥0.018/篇），省去再登服务器敲脚本；会产生 LLM
          成本，大批量建议关闭，交给每日任务
        </span>
      </ElFormItem>
    </ElForm>

    <ElUpload
      v-model:file-list="fileList"
      drag
      multiple
      :accept="ACCEPT"
      :auto-upload="false"
      :limit="200"
    >
      <div class="drop-inner">
        <p class="drop-title">把文章文件拖到这里，或点击选择</p>
        <p class="drop-tip">
          支持 {{ ACCEPT }}，单文件 ≤ 20MB，一次最多 200 个
        </p>
      </div>
    </ElUpload>

    <div class="actions">
      <ElButton type="primary" :disabled="!canSubmit" :loading="submitting" @click="submit">
        {{ dryRun ? '开始试解析' : `开始导入（${rawFiles.length} 个文件）` }}
      </ElButton>
      <ElButton :disabled="submitting" @click="clearFiles">清空</ElButton>
    </div>

    <div v-if="result" class="result">
      <div class="stats">
        <div class="stat">
          <span class="n">{{ result.uploaded }}</span><span class="l">选中</span>
        </div>
        <div class="stat">
          <span class="n">{{ result.accepted }}</span><span class="l">通过校验</span>
        </div>
        <div v-if="result.extracted" class="stat">
          <span class="n">{{ result.extracted }}</span><span class="l">zip 解出</span>
        </div>
        <div class="stat ok">
          <span class="n">{{ result.new }}</span><span class="l">新增</span>
        </div>
        <div class="stat">
          <span class="n">{{ result.dup }}</span><span class="l">重复</span>
        </div>
        <div class="stat">
          <span class="n">{{ result.reposts }}</span><span class="l">转载</span>
        </div>
        <div class="stat" :class="{ bad: result.failed > 0 }">
          <span class="n">{{ result.failed }}</span><span class="l">失败</span>
        </div>
      </div>

      <!-- 开启「立即抽取」时才有：把钱花在哪了必须让用户看见 -->
      <div v-if="result.extraction" class="extract-box">
        <b>立即抽取</b>
        <span v-if="result.extraction.status === 'ok' || result.extraction.status === 'budget_stopped'">
          送审 {{ result.extraction.done }} 篇，预筛命中
          {{ result.extraction.prescreenHit ?? 0 }}，抽出
          {{ result.extraction.extracted ?? 0 }} 条观点，成本
          ¥{{ (result.extraction.costCny ?? 0).toFixed(4) }}
        </span>
        <span v-else-if="result.extraction.status === 'skipped'">
          已跳过 — {{ result.extraction.reason }}
        </span>
        <span v-else class="bad">失败 — {{ result.extraction.error }}</span>
        <span v-if="result.extraction.truncated" class="warn">
          本次只抽前 {{ result.extraction.done }} 篇，剩余
          {{ result.extraction.requested - result.extraction.done }} 篇留待后续
        </span>
      </div>

      <ul v-if="result.rejected.length" class="failed-list">
        <li v-for="r in result.rejected" :key="r.file">
          <b>{{ r.file }}</b> — {{ r.error }}
        </li>
      </ul>

      <table v-if="result.files.length" class="file-table">
        <thead>
          <tr>
            <th>文件</th>
            <th class="num">状态</th>
            <th class="num">新增</th>
            <th class="num">重复</th>
            <th class="num">转载</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in result.files" :key="f.file">
            <td>{{ f.file }}</td>
            <td class="num">
              <ElTag :type="statusTag(f.status).type" size="small">
                {{ statusTag(f.status).text }}
              </ElTag>
            </td>
            <td class="num">{{ f.new ?? '-' }}</td>
            <td class="num">{{ f.dup ?? '-' }}</td>
            <td class="num">{{ f.reposts ?? '-' }}</td>
            <td class="msg">
              <span v-if="f.error">{{ f.error }}</span>
              <span v-else-if="f.titles?.length">
                解析 {{ f.parsed }} 篇：{{ f.titles.join(' / ') }}
              </span>
              <span v-else>-</span>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="result.next && !result.dryRun" class="next">
        下一步：{{ result.next }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.upload-panel {
  padding: 4px 2px;
}
.mb-3 {
  margin-bottom: 12px;
}
.w-80 {
  width: 320px;
  max-width: 100%;
}
.tip {
  margin-left: 10px;
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
}
.drop-inner {
  padding: 16px 8px;
}
.drop-title {
  margin: 0 0 4px;
  font-size: 14px;
}
.drop-tip {
  margin: 0;
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
}
.actions {
  margin: 14px 0 18px;
}
.result {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  padding: 14px 16px;
}
.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.stat {
  min-width: 82px;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 6px;
  text-align: center;
}
.stat .n {
  display: block;
  font-size: 18px;
  font-weight: 600;
}
.stat .l {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.stat.ok .n {
  color: #16a34a;
}
.stat.bad .n {
  color: #dc2626;
}
.file-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.file-table th,
.file-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
  text-align: left;
}
.num {
  text-align: center;
  white-space: nowrap;
}
.msg {
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
}
.failed-list {
  margin: 0 0 10px;
  padding-left: 18px;
  color: #dc2626;
  font-size: 12.5px;
}
.extract-box {
  margin: 0 0 10px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 6px;
  background: var(--el-fill-color-lighter, #fafafa);
  font-size: 12.5px;
  line-height: 1.7;
}
.extract-box b {
  margin-right: 8px;
}
.extract-box .bad {
  color: #dc2626;
}
.extract-box .warn {
  display: block;
  color: #d46b08;
}
.next {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
</style>
