// Single source of truth for backend connection info — mirrors the
// pattern in backend/app/core/config.py. Override via a .env file (Vite
// env vars must be prefixed VITE_) if the backend isn't on localhost:8000.
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export const WS_BASE_URL: string =
  (import.meta.env.VITE_WS_BASE_URL as string | undefined) ?? "ws://localhost:8000/ws";
