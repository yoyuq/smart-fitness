# Smart Fitness Android App

Kotlin Android client for Smart Fitness V2.

## Server
- REST: `http://192.168.123.56:8080/api/v2/`
- WebSocket: `ws://192.168.123.56:8080/api/v2/ws/session/{session_id}`
- Auth: JWT Bearer token in `Authorization` header

## Build
Open the `android_app` folder in Android Studio (Hedgehog+). Gradle 8.5 / AGP 8.2 / Kotlin 1.9.20. Min SDK 24, Target SDK 34.

## Screens
- **Login** — username + password; toggles between login and register. Device id auto-generated and persisted.
- **Home** — daily stats (sessions / reps / minutes / avg score) + recent plans. Pull to refresh.
- **Training** — enter a session id, connect via WebSocket, stream pose updates.
- **Plans** — list / create / delete workout plans.
- **Profile** — user info, devices, register-this-phone, log out.

## Key files
- `api/ApiClient.kt` — Retrofit + OkHttp singleton, JWT interceptor, prefs-backed token.
- `api/ApiService.kt` — Retrofit interface for all `/api/v2/*` endpoints.
- `api/WebSocketManager.kt` — OkHttp WebSocket wrapper with Bearer auth header.
- `model/Models.kt` — all request / response data classes.
