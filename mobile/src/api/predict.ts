import { API_BASE_URL } from '../constants/config';
import { PredictRequest, PredictResponse } from '../types';
import { fetchFlightByIata, fetchFlightByRoute } from './aviationstack';
import { postJSON } from './client';

export async function predict(req: PredictRequest): Promise<PredictResponse> {
  // Fetch flight data from the device (not AWS) to bypass Aviationstack free-plan IP block
  let prefetched_features: object | undefined;
  try {
    if ('flight_iata' in req) {
      prefetched_features =
        (await fetchFlightByIata(req.flight_iata, req.flight_date)) ?? undefined;
    } else {
      prefetched_features =
        (await fetchFlightByRoute(
          req.origin,
          req.destination,
          req.flight_date,
          req.departure_time,
        )) ?? undefined;
    }
  } catch (err) {
    console.warn('[predict] Aviationstack fetch failed — Lambda will predict without live flight data:', err);
  }

  if (prefetched_features) {
    console.log('[predict] sending prefetched_features to Lambda:', prefetched_features);
  } else {
    console.warn('[predict] no prefetched_features — Lambda will attempt its own Aviationstack call (may fail on free plan)');
  }

  return postJSON<PredictResponse>(`${API_BASE_URL}/predict`, {
    ...req,
    ...(prefetched_features ? { prefetched_features } : {}),
  });
}
