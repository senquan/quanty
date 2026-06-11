import type { RouteRecordRaw } from 'vue-router';

import { $t } from '#/locales';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:chart-candlestick',
      order: 10,
      title: $t('page.market.title'),
    },
    name: 'Market',
    path: '/market',
    children: [
      {
        name: 'MarketDashboard',
        path: '/market/dashboard',
        component: () => import('#/views/market/dashboard/index.vue'),
        meta: {
          affixTab: true,
          icon: 'lucide:activity',
          title: $t('page.market.dashboard'),
        },
      },
      {
        name: 'Fundflow',
        path: '/market/fundflow',
        component: () => import('#/views/market/fundflow/index.vue'),
        meta: {
          icon: 'lucide:trending-up-down',
          title: $t('page.market.fundflow'),
        },
      },
    ],
  },
];

export default routes;
