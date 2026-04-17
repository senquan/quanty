<script lang="ts" setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue';

interface Props {
  modelValue?: string;
  language?: string;
  theme?: string;
  height?: string;
  readOnly?: boolean;
  minimap?: boolean;
  fontSize?: number;
  wordWrap?: 'on' | 'off' | 'wordWrapColumn' | 'bounded';
  automaticLayout?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  language: 'python',
  theme: 'vs-dark',
  height: '600px',
  readOnly: false,
  minimap: true,
  fontSize: 14,
  wordWrap: 'on',
  automaticLayout: true,
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'change', value: string): void;
  (e: 'editorMounted', editor: any): void;
}>();

const editorContainer = ref<HTMLElement | null>(null);
let editor: any = null;
let monaco: any = null;
let isEditorReady = false;
let isLoading = ref(true);
let loadError = ref('');

// CDN URLs for Monaco Editor
const MONACO_CDN_BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0';

async function loadMonacoFromCDN(): Promise<any> {
  return new Promise((resolve, reject) => {
    // Load the loader script
    const script = document.createElement('script');
    script.src = `${MONACO_CDN_BASE}/min/vs/loader.js`;
    script.onload = () => {
      const win = window as any;
      win.require.config({
        paths: { vs: `${MONACO_CDN_BASE}/min/vs` },
      });
      win.require(['vs/editor/editor.main'], (monacoInstance: any) => {
        resolve(monacoInstance);
      }, (err: any) => {
        reject(new Error('Failed to load Monaco editor main'));
      });
    };
    script.onerror = () => reject(new Error('Failed to load Monaco loader script'));
    document.head.appendChild(script);
  });
}

async function tryLoadMonaco(): Promise<any> {
  // Try loading from npm package first (if available)
  try {
    const mod = await import('monaco-editor');
    return mod;
  } catch {
    // Fallback to CDN
    console.log('[MonacoEditor] npm package not available, loading from CDN...');
    return loadMonacoFromCDN();
  }
}

async function initEditor() {
  if (!editorContainer.value) return;

  try {
    isLoading.value = true;
    loadError.value = '';

    monaco = await tryLoadMonaco();

    // Register Python language configuration
    if (monaco.languages?.register) {
      try {
        monaco.languages.register({ id: 'python' });
        monaco.languages.setMonarchTokensProvider('python', {
          tokenizer: {
            root: [
              [/#.*$/, 'comment'],
              [/"""[\s\S]*?"""/, 'string'],
              [/'''[\s\S]*?'''/, 'string'],
              [/"([^"\\]|\\.)*"/, 'string'],
              [/'([^'\\]|\\.)*'/, 'string'],
              [/\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|yield|lambda|pass|break|continue|raise|assert|in|not|and|or|is|True|False|None|self)\b/, 'keyword'],
              [/\b(int|float|str|bool|list|dict|tuple|set|type|len|range|print|open|super|property|staticmethod|classmethod)\b/, 'type'],
              [/\b\d+(\.\d+)?\b/, 'number'],
              [/\b[A-Z_][A-Z0-9_]*\b/, 'constant'],
              [/[a-zA-Z_]\w*(?=\s*\()/, 'function'],
            ],
          },
        });
      } catch {
        // Language may already be registered
      }
    }

    editor = monaco.editor.create(editorContainer.value, {
      value: props.modelValue,
      language: props.language,
      theme: props.theme,
      readOnly: props.readOnly,
      minimap: { enabled: props.minimap },
      fontSize: props.fontSize,
      wordWrap: props.wordWrap,
      automaticLayout: props.automaticLayout,
      lineNumbers: 'on',
      roundedSelection: true,
      scrollBeyondLastLine: false,
      renderWhitespace: 'selection',
      tabSize: 4,
      insertSpaces: true,
      detectIndentation: false,
      folding: true,
      foldingStrategy: 'indentation',
      showFoldingControls: 'mouseover',
      bracketPairColorization: { enabled: true },
      padding: { top: 8, bottom: 8 },
    });

    // Listen for content changes
    editor.onDidChangeModelContent(() => {
      const value = editor.getValue();
      emit('update:modelValue', value);
      emit('change', value);
    });

    isEditorReady = true;
    isLoading.value = false;
    emit('editorMounted', editor);
  } catch (error: any) {
    console.error('[MonacoEditor] Init error:', error);
    loadError.value = error.message || 'Failed to load editor';
    isLoading.value = false;
  }
}

// Watch for external value changes
watch(
  () => props.modelValue,
  (newValue) => {
    if (editor && isEditorReady && editor.getValue() !== newValue) {
      editor.setValue(newValue);
    }
  },
);

// Watch for language changes
watch(
  () => props.language,
  (newLang) => {
    if (editor && monaco && isEditorReady) {
      monaco.editor.setModelLanguage(editor.getModel(), newLang);
    }
  },
);

// Watch for theme changes
watch(
  () => props.theme,
  (newTheme) => {
    if (monaco && isEditorReady) {
      monaco.editor.setTheme(newTheme);
    }
  },
);

onMounted(() => {
  nextTick(() => initEditor());
});

onUnmounted(() => {
  if (editor) {
    editor.dispose();
    editor = null;
  }
});

// Expose editor instance for parent components
defineExpose({
  getEditor: () => editor,
  getValue: () => editor?.getValue() ?? '',
  setValue: (value: string) => editor?.setValue(value),
  focus: () => editor?.focus(),
  getMonaco: () => monaco,
});
</script>

<template>
  <div class="monaco-editor-wrapper">
    <div
      ref="editorContainer"
      class="monaco-editor-container"
      :style="{ height: props.height }"
    />
    <div v-if="isLoading" class="monaco-editor-loading">
      <div class="loading-spinner" />
      <span>加载编辑器中...</span>
    </div>
    <div v-if="loadError" class="monaco-editor-error">
      <span>编辑器加载失败: {{ loadError }}</span>
      <ElButton size="small" type="primary" @click="initEditor">
        重试
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.monaco-editor-wrapper {
  position: relative;
  width: 100%;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--el-border-color-lighter, #dcdfe6);
}

.monaco-editor-container {
  width: 100%;
  min-height: 200px;
}

.monaco-editor-loading,
.monaco-editor-error {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: rgba(30, 30, 30, 0.9);
  color: #cccccc;
  font-size: 14px;
  z-index: 10;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid rgba(255, 255, 255, 0.2);
  border-top-color: #007acc;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
