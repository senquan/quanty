import type { RouteRecordRaw } from 'vue-router';

import { $t } from '#/locales';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:bar-chart-3',
      order: 30,
      title: $t('page.quant.title'),
    },
    name: 'Quant',
    path: '/quant',
    children: [
      {
        name: 'QuantDashboard',
        path: '/quant/dashboard',
        component: () => import('#/views/quant/dashboard/index.vue'),
        meta: {
          affixTab: true,
          icon: 'lucide:layout-dashboard',
          title: $t('page.quant.dashboard'),
        },
      },
      {
        name: 'QuantStrategy',
        path: '/quant/strategy',
        component: () => import('#/views/quant/strategy/index.vue'),
        meta: {
          icon: 'lucide:file-code-2',
          title: $t('page.quant.strategy'),
        },
      },
      {
        name: 'QuantFactorStrategyDetail',
        path: '/quant/strategy/factor-detail',
        component: () =>
          import('#/views/quant/strategy/factor/factor-strategy-detail.vue'),
        meta: {
          hideInMenu: true,
          icon: 'lucide:layers',
          title: $t('page.quant.factorStrategyDetail'),
        },
      },
      {
        name: 'QuantStrategyEdit',
        path: '/quant/strategy/edit',
        component: () => import('#/views/quant/strategy/edit.vue'),
        meta: {
          hideInMenu: true,
          icon: 'lucide:pencil',
          title: $t('page.quant.strategyEdit'),
        },
      },
      {
        name: 'QuantBacktest',
        path: '/quant/backtest',
        component: () => import('#/views/quant/backtest/index.vue'),
        meta: {
          icon: 'lucide:trending-up',
          title: $t('page.quant.backtest'),
        },
      },
      {
        name: 'QuantTrading',
        path: '/quant/trading',
        component: () => import('#/views/quant/trading/index.vue'),
        meta: {
          icon: 'lucide:arrow-left-right',
          title: $t('page.quant.trading'),
        },
      },
      {
        name: 'QuantWatchlist',
        path: '/quant/watchlist',
        component: () => import('#/views/quant/watchlist/index.vue'),
        meta: {
          icon: 'lucide:star',
          title: $t('page.quant.watchlist'),
        },
      },
    ],
  },
];

export default routes;
