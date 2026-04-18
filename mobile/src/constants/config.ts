// Set EXPO_PUBLIC_API_BASE_URL in .env or app.json extra, or hardcode below.
// e.g. https://abc123.execute-api.us-east-1.amazonaws.com
export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ?? 'https://mcn3odyqlk.execute-api.us-west-2.amazonaws.com';
