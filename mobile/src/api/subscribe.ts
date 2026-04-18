import { API_BASE_URL } from '../constants/config';
import { SubscribeRequest, SubscribeResponse } from '../types';

export async function subscribe(req: SubscribeRequest): Promise<SubscribeResponse> {
  const res = await fetch(`${API_BASE_URL}/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  const body = await res.json();
  if (!res.ok) {
    throw new Error(body?.error ?? `Subscribe failed (${res.status})`);
  }
  return body as SubscribeResponse;
}
