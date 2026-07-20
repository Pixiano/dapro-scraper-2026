# Hosting via ngrok — plan (not implemented yet)

Goal: expose the locally running app to the internet through an ngrok tunnel for
phone/remote access. No GPU/queue concerns — this is a small quota-bound API, so
the plan is: tunnel setup + a short list of app-specific things to lock down first.

## Tunnel setup

1. Install ngrok (`winget install ngrok`), create the free account, then
   `ngrok config add-authtoken <token>` (one-time).
2. Keep uvicorn bound to `127.0.0.1:8123` (the current default). The tunnel is
   then the *only* public path in — don't switch to `--host 0.0.0.0`.
3. `ngrok http 8123` → public `https://…ngrok-free.app` URL. The free tier gives
   one **static domain**; use `ngrok http --url=<name>.ngrok-free.app 8123` so the
   URL survives restarts. Browsers see ngrok's one-click interstitial on first
   visit (fine for personal use).
4. Recommended: put edge-level auth on the tunnel instead of adding auth code to
   the app: `ngrok http 8123 --basic-auth "user:strongpass"`. Zero code changes,
   and the whole concern list below shrinks because strangers can't reach it.

## App-specific concerns before exposing publicly

1. **API key** — already server-side only (`.env`, gitignored, never in responses
   or logs; verified during the build). Note the Google Cloud **IP restriction
   still works with ngrok**: outbound calls to Google originate from this
   machine's IP, not from ngrok's edge, so restrict the key to the home IP as the
   README says. Nothing to change, just don't skip that step.

2. **Rate limiting breaks behind the tunnel — needs a fix before public use.**
   slowapi keys on the socket peer address; through ngrok every visitor arrives
   as `127.0.0.1`, so *all* public visitors share one 10/min bucket. That is
   accidentally protective of the quota but means one stranger rate-limits
   everyone including you. Fix (when implementing): in `backend/ratelimit.py`,
   switch the `key_func` to read `X-Forwarded-For` (ngrok sets it) **behind a
   config flag** (`TRUST_PROXY=true`), because trusting XFF when *not* behind a
   proxy lets clients spoof their identity. Default stays off.

3. **Quota ledger is the real damage cap.** Strangers can at worst burn the daily
   budget; the 9,500 soft stop then serves cache-only until midnight Pacific.
   While publicly exposed, consider dropping `QUOTA_SOFT_STOP` (e.g. to 2,000) in
   `.env` so a bad day costs a fifth of the budget, not all of it. The expensive
   endpoints (captions 50u, search 100u) are already confirm-gated and 3/min.

4. **SQLite cache + ledger (`data/app.db`)** — contains only public YouTube data
   and unit counts, no secrets; single-writer SQLite is fine at tunnel-scale
   traffic. It grows unbounded; not a blocker, but a periodic purge of expired
   rows is a nice-to-have if the tunnel stays up for weeks.

5. **Instagram tier: keep `ENABLE_INSTAGRAM=false` while public.** Anonymous
   instaloader requests triggered by strangers would come from the *home IP* and
   get it rate-limited or blocked by Instagram. Don't let the public spend that.

6. **FastAPI `/docs` is public** — harmless for a read-only API but it advertises
   every endpoint to anyone probing. Optional hardening: `docs_url=None` behind
   the same public-mode flag. Skip if using `--basic-auth`, which covers it.

## Run sequence (when we implement)

1. Start the server (existing launch config, port 8123).
2. `ngrok http 8123 --url=<static-domain> --basic-auth "user:pass"`.
3. Phone test: resolve a channel, confirm the quota meter moves, repeat lookup
   is a cache hit, rate-limit fires from a second device/network.
4. Watch `GET /api/quota` for unexpected burn the first day.
