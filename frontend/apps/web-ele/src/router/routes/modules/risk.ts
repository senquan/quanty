import type { RouteRecordRaw } from 'vue-router';

import { $t } from '#/locales';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:siren',
      order: 40,
      title: $t('page.risk.title'),
    },
    name: 'Risk',
    path: '/risk',
    children: [
      {
        name: 'RiskDashboard',
        path: '/risk/dashboard',
        component: () => import('#/views/risk/dashboard/index.vue'),
        meta: {
          affixTab: true,
          icon: 'lucide:cctv',
          title: $t('page.risk.dashboard'),
        },
      },
      {
        name: 'Alert',
        path: '/risk/alert',
        component: () => import('#/views/risk/alert/index.vue'),
        meta: {
          icon: 'lucide:triangle-alert',
          title: $t('page.risk.alert'),
        },
      },
    ],
  },
];

export default routes;
