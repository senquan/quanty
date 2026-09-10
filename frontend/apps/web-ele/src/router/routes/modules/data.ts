import type { RouteRecordRaw } from 'vue-router';

import { $t } from '#/locales';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:database',
      order: 20,
      title: $t('page.data.title'),
    },
    name: 'Data',
    path: '/data',
    children: [
      {
        name: 'Factor',
        path: '/data/factor',
        component: () => import('#/views/data/factor/index.vue'),
        meta: {
          affixTab: true,
          icon: 'lucide:component',
          title: $t('page.data.factor'),
        },
      },
      {
        name: 'NewsAnalysis',
        path: '/data/news-analysis',
        component: () => import('#/views/data/news-analysis/index.vue'),
        meta: {
          affixTab: false,
          icon: 'lucide:newspaper',
          title: $t('page.data.news'),
        },
      },
    ],
  },
];

export default routes;
