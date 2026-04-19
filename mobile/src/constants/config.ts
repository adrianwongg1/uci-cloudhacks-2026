// Set EXPO_PUBLIC_API_BASE_URL in .env or app.json extra, or hardcode below.
// e.g. https://abc123.execute-api.us-east-1.amazonaws.com
export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ?? 'https://mcn3odyqlk.execute-api.us-west-2.amazonaws.com';

// Aviationstack is called directly from the app (device IPs are not blocked by the free plan).
export const AVIATIONSTACK_KEY = process.env.EXPO_PUBLIC_AVIATIONSTACK_KEY ?? '';
