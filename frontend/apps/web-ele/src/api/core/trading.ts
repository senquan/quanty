/** 华泰模拟交易 API 接口层 */

import { requestClient } from '#/api/request';

export interface TradeOrder {
  order_id: string;
  symbol: string;
  order_type: string;
  side: string;
  quantity: number;
  price: number;
  filled_quantity: number;
  status: string;
  created_at: string;
  filled_at: string | null;
  commission: number;
}

export interface Position {
  symbol: string;
  side: string;
  quantity: number;
  avg_price: number;
  current_price: number;
  pnl: number;
  pnl_percent: number;
}

export interface Account {
  account_id: string;
  balance: number;
  available: number;
  positions: Position[];
  orders: TradeOrder[];
  total_pnl: number;
}

export interface OrderRequest {
  symbol: string;
  order_type: string;
  side: string;
  quantity: number;
  price?: number;
}

export async function getAccountApi() {
  return requestClient.get<Account>('/trading/account');
}

export async function getPositionsApi() {
  return requestClient.get<Position[]>('/trading/positions');
}

export async function getOrdersApi() {
  return requestClient.get<TradeOrder[]>('/trading/orders');
}

export async function createOrderApi(data: OrderRequest) {
  return requestClient.post<TradeOrder>('/trading/order', data);
}

export async function cancelOrderApi(orderId: string) {
  return requestClient.delete(`/trading/order/${orderId}`);
}
