# Delegation note

The Android FCM registration client currently posts its rotating device token
without the dashboard owner-key header or an Origin header. The new
`POST /api/tts/app` route requires the owner key, JSON, and the dashboard's
own origin, as required for this move. Updating the native client to supply
those authenticated request details is outside this server-side task.

Until that Android handoff lands, a device token can be registered only by an
authenticated dashboard request. The server-side FCM sender, ring fallback,
and live-call routes are otherwise ready.
