import { API_BASE_URL } from '../constants/config';
import { PredictRequest, PredictResponse } from '../types';

export async function predict(req: PredictRequest): Promise<PredictResponse> {
  const res = await fetch(`${API_BASE_URL}/predict`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  const body = await res.json();
  if (!res.ok) {
    throw new Error(body?.error ?? `Predict failed (${res.status})`);
  }
  return body as PredictResponse;
}
