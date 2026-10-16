# IMD token renewal

FloodNet renews the IMD bearer token automatically using IMD's token-generation endpoint with the registered IMD
account credentials, as long as those credentials remain valid.

## 1. The endpoint

`POST https://api.imd.gov.in/api/oauth/token.php`

| Item | Status |
|---|---|
| The endpoint is IMD's official token endpoint | Confirmed by the IMD account holder. It is **not** listed on the public `public/api_reference.html` (checked 2026-09-21) |
| Request body | JSON `{"email": "...", "password": "..."}`. Checked 2026-09-21: an empty or form-encoded body gets HTTP 400 "email and password required"; JSON with fake credentials gets HTTP 401 "Invalid credentials" |
| Success response | HTTP 200 with `access_token`, `token_type` "Bearer" and `expires_in` 3600 (verified live 2026-09-21, §5) |
| CAPTCHA | Not required by this endpoint. The portal *web* login (`login.php`) still uses a CAPTCHA; FloodNet never touches that form |

The token is sent to the data API as `Authorization: Bearer <token>`, next to the separate, IP-bound `X-API-Key`
(`IMD_API_KEY`). The API key plays no part in token generation.

## 2. Credentials (backend-only secrets)

| Variable | Meaning |
|---|---|
| `IMD_EMAIL`, `IMD_PASSWORD` | The registered IMD portal account |
| `IMD_CREDENTIALS_PROVIDER` | `env` (default) · `aws-secretsmanager` (`IMD_CREDENTIALS_SECRET_ID`, JSON `{"IMD_EMAIL", "IMD_PASSWORD"}`) · `aws-ssm` (`IMD_EMAIL_SSM_PARAMETER`, `IMD_PASSWORD_SSM_PARAMETER`, SecureString) |
| `IMD_TOKEN_URL` | Override for the endpoint (default above) |
| `FLOODNET_IMD_AUTO_RENEW` | `0` turns off the background keeper; renewal at request time still runs |

These are never sent to the frontend (no `VITE_` variable), and never logged or returned by any API. The same
goes for the issued token and the `Authorization` header. If IMD's error text ever echoes the email, it is dropped.

## 3. Lifecycle (`floodnet/rainfall/imd_auth.py`, `imd_token_issuer.py`)

1. **No token yet** (first start): the app starts without contacting IMD. A background keeper pre-warms a token
   shortly after start-up and then checks every 60 s; the first IMD-backed request also generates one if needed. If
   IMD is unreachable, start-up is not affected.
2. **Under 10 min left** (from the token's own `exp`, or from `expires_in`): a new token is generated and swapped
   in atomically before the old one lapses. If generation fails, the current token keeps being used until it expires.
3. **HTTP 401 from IMD**: the token is marked rejected, then **one** token-generation request is made, and the
   original IMD request is retried **once**. Only if generation or the retry fails does "Best available source" fall
   back (radar-derived → ECMWF → cached → demo).
4. **HTTP 403**: key, IP or permission problem. There is **no** token generation.
5. **Single-flight**: concurrent renewals share one in-flight generation *and its outcome*. Six simultaneous 401s
   cost one token request, whether it succeeds or fails.
6. **Back-off**: after a failed generation, no new attempt is made for 60 s, or 15 min if IMD refused the credentials.
   A wrong password cannot hammer the account.
7. **Secret-store fallback**: if generation is not configured or fails, renewal re-reads the configured token
   source (`IMD_TOKEN_PROVIDER`: env / file / aws-secretsmanager / aws-ssm). A manually rotated token still works.

Precedence of the token in use: admin-endpoint rotation > generated token > configured token source.

## 4. Status and UI

`GET /api/data-status` gives the following fields:

- `imd_auth_status`: VALID / EXPIRING_SOON / RENEWING / EXPIRED / UNAVAILABLE
- `imd_refresh_status`: idle / refreshing / renewed / failed
- `imd_auto_renewal`: whether credentials are configured
- `imd_token_expires_at` and `imd_token_expiry_basis`: `jwt_exp` / `issuer_expires_in` / `loaded_at+ttl`
- `active_rainfall_source` and `active_source_timestamp`

Badge: **IMD RENEWING** (renewal running, or token expired with auto-renewal on) → **IMD LIVE** after a successful
authenticated request → **IMD UNAVAILABLE** only if renewal failed. Without credentials, an expired token reads
**IMD AUTH EXPIRED**. IMD LIVE is never shown after a failed IMD request.

## 5. Verification

**Live result (2026-09-21, development laptop, run through the FastAPI app with no JWT supplied):**
- The backend called the token endpoint by itself: HTTP 200, `token_type` Bearer, `expires_in` 3600, exactly one
  token request. Automatic token generation is therefore **verified live**.
- The following IMD data request got HTTP 403 "IP address not authorized". The laptop's public IP no longer matches
  the IP that `IMD_API_KEY` was registered for. The 403 correctly did not trigger another token request.
- Still **not verified live**: IMD data loading with a generated token, and the replace → 401 → renew → retry path.
  Both need a key registered for the machine that runs the test (DEPLOYMENT.md §3). Rerun the live test below
  on the production host.

- Automated tests: `backend/tests/test_imd_oauth_renewal.py`, run with fakes and no real credentials. They cover
  generation success and failure, expiry-triggered renewal, a 401 followed by one generation and one retry (success
  and failure), single-flight on success and on failure, no renewal on 403, the AWS credential store, the
  secret-store fallback, and no leak of the email, password, token or header.
- Live test (opt-in). It runs the real app with no JWT supplied: first request → token generated → IMD data; then
  the token is replaced with one IMD rejects → 401 → exactly one new token → one retry. It prints only the HTTP
  status, token_type, expires_in and success/failure, never the token:
  `RUN_LIVE_IMD=1 .venv/Scripts/python.exe -m pytest tests/test_imd_oauth_live.py -v -s -m live`

## 6. Limits

- Renewal works only while the account credentials remain valid. A password change or account suspension turns
  every generation into a failure (IMD UNAVAILABLE, back-off, fallback) until the stored credentials are updated.
- The API key remains bound to one public IP. On Vercel that is the project's Static IP in bom1 (DEPLOYMENT.md §3).
