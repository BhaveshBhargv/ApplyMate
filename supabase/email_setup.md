# Email verification & password reset — setup

ApplyMate sends its **own** one-time codes. Supabase's email system is not used for this.

| When | What the user does |
|---|---|
| **Sign-up** | Enters email + password → ApplyMate emails a 6-digit code → user types it → the account is created (already verified) and they're logged in. **No account exists until the code is entered.** |
| **Forgot password** | Enters their email → gets a code → types it with a new password → logs in with the new password. Every other device is signed out. |

Codes: 6 digits, valid 10 minutes, single use, max 5 wrong guesses, one email per address per minute,
max 5 per address per hour. Only an HMAC of the code is kept, in the user's server-side session —
never in the browser or database. Passwords are never stored by ApplyMate; Supabase keeps a bcrypt hash.

## What you need to set up (one time)

### 1. The sender mailbox (applymate.info@gmail.com)
Codes are sent from **applymate.info@gmail.com** through Gmail's SMTP server. Add to
`.streamlit/secrets.toml` (and the Streamlit Cloud secrets):

```toml
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "applymate.info@gmail.com"
SMTP_PASSWORD = "xxxx xxxx xxxx xxxx"     # a Gmail App Password (see below), NOT the Gmail password
SMTP_FROM = "ApplyMate <applymate.info@gmail.com>"
```
**Getting the app password:** sign in to that Google account -> Manage your Google Account ->
Security -> turn on **2-Step Verification** -> then search "App passwords" -> create one named
"ApplyMate" -> copy the 16 characters into `SMTP_PASSWORD`. (Normal Gmail passwords are rejected
for SMTP.) Put it only in the secrets file -- never in chat, code or git.

Gmail limits: about 500 emails/day for a normal account, and Gmail always shows the
authenticated address as the sender. Fine for a small app; switch to a transactional provider
(Resend, Brevo, Postmark ...) with your own domain and SPF/DKIM if volume or spam placement
becomes a problem -- only the `SMTP_*` values change.

### 2. The Supabase service_role key
Creating an already-verified account and setting a password are admin-only operations, so the
server needs the project's **service_role** key (Supabase → Project Settings → API Keys → the
*secret* / service_role key):

```toml
SUPABASE_SERVICE_KEY = "sb_secret_..."
```
**Treat it like a root password.** It bypasses all Row Level Security. It lives only in server
secrets; only `utils/supabase_admin.py` reads it; that module exposes just four narrow
operations (create verified user, find user id by email, set password, revoke sessions) and
never touches résumé data. Never commit it, paste it in chat, or put it in the browser. If it
leaks, rotate it in Supabase immediately.

### 3. Run the updated SQL
Re-run `supabase/schema.sql` (safe to repeat). It adds two functions that only the service role
can call: `admin_user_id_by_email` and `admin_revoke_sessions`.

### 4. Turn OFF public sign-ups in Supabase
Authentication → Sign In / Providers → **disable "Allow new users to sign up"**.
Otherwise anyone could call Supabase's public sign-up endpoint directly and skip the email check.
(Accounts created by ApplyMate go through the admin API, which still works with sign-ups off.)
Existing users are unaffected.

## Notes
- A reset **does not lose data**: résumés are encrypted with a key wrapped by the app's master key,
  not by the password.
- The "Forgot password" screen answers the same way whether or not the email has an account, and
  sends nothing for unknown addresses.
- Signing up with an address that already has an account: that address is emailed a "you already
  have an ApplyMate account -- log in or use Forgot password" notice instead of a code. The sign-up
  screen looks identical to the new-address case (no "already exists" message), and no code can
  succeed, so the existing account can't be taken over and its owner learns about it immediately.
- If the code screen is refreshed or the server restarts mid-way, the code is lost: request a new one.
- If SMTP or the service key is missing, sign-up and reset show "isn't set up yet" instead of failing.
