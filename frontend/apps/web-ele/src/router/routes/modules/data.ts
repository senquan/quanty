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
    ],
  },
];

export default routes;
