import { AVIATIONSTACK_KEY } from '../constants/config';

const BASE = 'http://api.aviationstack.com/v1/flights';

export interface FlightFeatures {
  airline: string;
  origin: string;
  destination: string;
  dep_hour: number;
  day_of_week: string;
  month: string;
  distance: string;
  scheduled_departure: string | null;
  current_status: string | null;
  current_delay_minutes: number | null;
}

function parseIso(s: string | undefined): Date | null {
  if (!s) return null;
  try {
    return new Date(s);
  } catch {
    return null;
  }
}

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function toFeatures(flight: Record<string, unknown>): FlightFeatures {
  const dep = (flight.departure ?? {}) as Record<string, unknown>;
  const arr = (flight.arrival ?? {}) as Record<string, unknown>;
  const airline = (flight.airline ?? {}) as Record<string, unknown>;
  const dt = parseIso(dep.scheduled as string) ?? new Date();
  return {
    airline: (airline.name as string) ?? 'Unknown',
    origin: (dep.iata as string) ?? '???',
    destination: (arr.iata as string) ?? '???',
    dep_hour: dt.getUTCHours(),
    day_of_week: DAYS[dt.getUTCDay()],
    month: MONTHS[dt.getUTCMonth()],
    distance: 'unknown',
    scheduled_departure: (dep.scheduled as string) ?? null,
    current_status: (flight.flight_status as string) ?? null,
    current_delay_minutes: (dep.delay as number) ?? null,
  };
}

async function fetchFlights(params: Record<string, string>): Promise<unknown[]> {
  if (!AVIATIONSTACK_KEY) throw new Error('AVIATIONSTACK_KEY is not configured');
  const qs = new URLSearchParams({ access_key: AVIATIONSTACK_KEY, ...params }).toString();
  console.log('[aviationstack] fetching', params);
  const res = await fetch(`${BASE}?${qs}`);
  if (!res.ok) throw new Error(`Aviationstack error ${res.status}`);
  const json = await res.json();
  const flights = (json.data as unknown[]) ?? [];
  console.log(`[aviationstack] received ${flights.length} flight(s)`);
  return flights;
}

export async function fetchFlightByIata(
  flightIata: string,
  flightDate: string,
): Promise<FlightFeatures | null> {
  const flights = await fetchFlights({ flight_iata: flightIata, flight_date: flightDate });
  if (!flights.length) {
    console.log('[aviationstack] no flights found for', flightIata, flightDate);
    return null;
  }
  const features = toFeatures(flights[0] as Record<string, unknown>);
  console.log('[aviationstack] features extracted:', features);
  return features;
}

export async function fetchFlightByRoute(
  origin: string,
  destination: string,
  flightDate: string,
  departureTime: string,
): Promise<FlightFeatures | null> {
  const flights = await fetchFlights({
    dep_iata: origin,
    arr_iata: destination,
    flight_date: flightDate,
  });
  if (!flights.length) {
    console.log('[aviationstack] no flights found for route', origin, '->', destination, flightDate);
    return null;
  }

  // Pick the flight matching the requested departure time
  for (const f of flights) {
    const fl = f as Record<string, unknown>;
    const dep = (fl.departure ?? {}) as Record<string, unknown>;
    const dt = parseIso(dep.scheduled as string);
    if (dt) {
      const hhmm = `${String(dt.getUTCHours()).padStart(2, '0')}:${String(dt.getUTCMinutes()).padStart(2, '0')}`;
      if (hhmm === departureTime) {
        const features = toFeatures(fl);
        console.log('[aviationstack] matched departure time, features:', features);
        return features;
      }
    }
  }
  const features = toFeatures(flights[0] as Record<string, unknown>);
  console.log('[aviationstack] no exact time match, using first flight, features:', features);
  return features;
}
