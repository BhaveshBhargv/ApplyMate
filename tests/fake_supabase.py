"""In-memory stand-in for Supabase (Auth + PostgREST + the schema's RPCs), with
RLS-style per-user filtering and the schema's row limits. Test use only."""
import json
import re
import uuid
from urllib.parse import urlparse

LIMITS = {"resumes": 5, "cover_letters": 10, "saved_jobs": 50}
AI_LIMIT = 15
STRICT = True
SERVICE_KEY = "sb_secret_testservice"


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class FakeSupabase:
    def __init__(self, confirm_email=False, has_primary=True):
        self.has_primary = has_primary
        self.outbox = []     # (email, type, code) -- the 'emails' a user would receive
        self.otps = {}       # (email, type) -> code
        self._n = 0   # False = a database that predates the is_primary column
        self.users = {}        # email -> {id, password}
        self.tokens = {}       # access token -> user id
        self.refresh = {}      # refresh token -> user id
        self.tables = {t: [] for t in ["profiles", "resumes", "cover_letters", "saved_jobs", "ai_usage", "daily_picks"]}
        self.logs = []
        self.confirm_email = confirm_email
        self.calls = []

    # ---- helpers
    def _session(self, uid, email):
        at, rt = "at_" + uuid.uuid4().hex, "rt_" + uuid.uuid4().hex
        self.tokens[at] = uid
        self.refresh[rt] = uid
        return {"access_token": at, "refresh_token": rt, "expires_in": 3600,
                "user": {"id": uid, "email": email}}

    def _uid(self, headers):
        auth = headers.get("Authorization", "")
        return self.tokens.get(auth.replace("Bearer ", ""))

    # ---- entry point (replaces requests.request)
    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        headers, params = headers or {}, params or {}
        path = urlparse(url).path
        self.calls.append((method, path))
        if STRICT:
            assert urlparse(url).netloc.endswith("supabase.co"), url
        is_admin = path.startswith("/auth/v1/admin/") or path.endswith(("/admin_user_id_by_email", "/admin_revoke_sessions"))
        if is_admin:
            assert headers.get("apikey") == SERVICE_KEY, "admin endpoints need the service key"
        else:   # the service key must never be used for ordinary (user) calls
            assert headers.get("apikey", "").startswith("sb_publishable_"), "only the publishable key is allowed here"
        if path.startswith("/auth/v1/"):
            return self._auth(method, path, headers, params, json or {})
        if path.startswith("/rest/v1/rpc/"):
            return self._rpc(path.split("/")[-1], headers, json or {})
        if path.startswith("/rest/v1/"):
            return self._rest(method, path.split("/")[-1], headers, params, json)
        return Resp(404, {"message": "nope"})

    def post(self, url, headers=None, json=None, timeout=None):   # the logging thread
        return self.request("POST", url, headers, None, json, timeout)

    def _send(self, email, kind):
        self._n += 1
        code = f"{(self._n * 137531) % 900000 + 100000}"
        self.otps[(email, kind)] = code
        self.outbox.append((email, kind, code))

    def last_code(self, email, kind):
        return next(c for e, k, c in reversed(self.outbox) if e == email and k == kind)

    # ---- auth
    def _auth(self, method, path, headers, params, body):
        if path.startswith("/auth/v1/admin/users"):
            if method == "POST":
                email = body["email"]
                if email in self.users:
                    return Resp(422, {"error_code": "email_exists",
                                      "msg": "A user with this email address has already been registered"})
                if len(body["password"]) < 6:
                    return Resp(422, {"error_code": "weak_password", "msg": "Password should be at least 6 characters."})
                uid = str(uuid.uuid4())
                self.users[email] = {"id": uid, "password": body["password"], "confirmed": bool(body.get("email_confirm"))}
                return Resp(200, {"id": uid, "email": email})
            if method == "PUT":
                uid = path.rsplit("/", 1)[-1]
                u = next((v for v in self.users.values() if v["id"] == uid), None)
                if u is None:
                    return Resp(404, {"msg": "User not found"})
                if len(body["password"]) < 6:
                    return Resp(422, {"error_code": "weak_password", "msg": "Password should be at least 6 characters."})
                u["password"] = body["password"]
                return Resp(200, {"id": uid})
        if path.endswith("/signup"):
            email = body["email"]
            if email in self.users:
                if self.confirm_email:        # real Supabase hides existing accounts: 200, no email
                    return Resp(200, {"id": str(uuid.uuid4()), "email": email, "identities": []})
                return Resp(422, {"error_code": "user_already_exists", "msg": "User already registered"})
            if len(body["password"]) < 6:
                return Resp(422, {"error_code": "weak_password", "msg": "Password should be at least 6 characters."})
            uid = str(uuid.uuid4())
            self.users[email] = {"id": uid, "password": body["password"], "confirmed": not self.confirm_email}
            if self.confirm_email:
                self._send(email, "signup")
                return Resp(200, {"id": uid, "email": email})
            return Resp(200, self._session(uid, email))
        if path.endswith("/token"):
            if params.get("grant_type") == "password":
                u = self.users.get(body["email"])
                if not u or u["password"] != body["password"]:
                    return Resp(400, {"error_code": "invalid_credentials", "msg": "Invalid login credentials"})
                if not u.get("confirmed", True):
                    return Resp(400, {"error_code": "email_not_confirmed", "msg": "Email not confirmed"})
                return Resp(200, self._session(u["id"], body["email"]))
            uid = self.refresh.pop(body.get("refresh_token"), None)
            if not uid:
                return Resp(400, {"error_code": "refresh_token_not_found", "msg": "Invalid Refresh Token"})
            email = next(e for e, u in self.users.items() if u["id"] == uid)
            return Resp(200, self._session(uid, email))
        if path.endswith("/verify"):
            key = (body["email"], body["type"])
            if self.otps.get(key) != body["token"]:
                return Resp(403, {"error_code": "otp_expired", "msg": "Token has expired or is invalid"})
            self.otps.pop(key)                                     # single use
            u = self.users[body["email"]]
            u["confirmed"] = True
            return Resp(200, self._session(u["id"], body["email"]))
        if path.endswith("/resend"):
            u = self.users.get(body["email"])
            if u and not u.get("confirmed", True):
                self._send(body["email"], body["type"])
            return Resp(200, {})
        if path.endswith("/recover"):
            if body["email"] in self.users:                        # silently nothing for unknown emails
                self._send(body["email"], "recovery")
            return Resp(200, {})
        if path.endswith("/user") and method == "PUT":
            uid = self._uid(headers)
            if uid is None:
                return Resp(401, {"msg": "not signed in"})
            u = next(v for v in self.users.values() if v["id"] == uid)
            if body["password"] == u["password"]:
                return Resp(422, {"error_code": "same_password", "msg": "New password should be different from the old password."})
            if len(body["password"]) < 6:
                return Resp(422, {"error_code": "weak_password", "msg": "Password should be at least 6 characters."})
            u["password"] = body["password"]
            return Resp(200, {"id": uid})
        if path.endswith("/logout"):
            self.tokens.pop(headers["Authorization"].replace("Bearer ", ""), None)
            return Resp(204)
        return Resp(404, {"message": "nope"})

    # ---- tables
    def _match(self, row, params):
        for k, v in params.items():
            if k in ("select", "order", "on_conflict"):
                continue
            op, _, val = v.partition(".")
            if op == "eq" and str(row.get(k)) != val:
                return False
        return True

    def _rest(self, method, table, headers, params, body):
        uid = self._uid(headers)
        if uid is None:
            return Resp(401, {"message": "JWT expired or missing", "code": "PGRST301"})
        rows = [r for r in self.tables[table] if r["user_id"] == uid] if table != "profiles" else \
            [r for r in self.tables[table] if r["user_id"] == uid]
        prefer = headers.get("Prefer", "")
        if table == "resumes" and not self.has_primary and (
                "is_primary" in str(params.get("select", "")) or
                any("is_primary" in (i or {}) for i in (body if isinstance(body, list) else []))):
            return Resp(400, {"code": "42703", "message": "column resumes.is_primary does not exist"})
        if method == "GET":
            out = [r for r in rows if self._match(r, params)]
            order = params.get("order")
            if order:
                col, _, d = order.partition(".")
                out.sort(key=lambda r: r.get(col, ""), reverse=(d == "desc"))
            sel = params.get("select")
            if sel and sel != "*":
                cols = sel.split(",")
                out = [{c: r.get(c) for c in cols} for r in out]
            return Resp(200, out)
        if method == "POST":
            made = []
            for item in body:
                if item.get("user_id") != uid:      # RLS WITH CHECK
                    return Resp(403, {"code": "42501", "message": "new row violates row-level security policy"})
                item = dict(item)
                conflict = None
                keycols = ["user_id"] if table in ("profiles", "daily_picks") else (
                    ["user_id", "url_hash"] if params.get("on_conflict") else ["id"])
                for r in self.tables[table]:
                    if all(r.get(c) == item.get(c) for c in keycols):
                        conflict = r
                if conflict:
                    if "merge-duplicates" in prefer:      # upsert
                        conflict.update({k: v for k, v in item.items() if k != "user_id"})
                        continue
                    if "ignore-duplicates" in prefer:
                        continue
                    return Resp(409, {"code": "23505", "message": "duplicate key"})
                if table in LIMITS and len(rows) + len(made) >= LIMITS[table]:
                    return Resp(400, {"code": "P0001", "message": f"limit_reached:{table}"})
                item.setdefault("status", "saved") if table == "saved_jobs" else None
                if table == "resumes" and self.has_primary:
                    item.setdefault("is_primary", False)
                item["updated_at"] = item["created_at"] = item.get("created_at", "2026-10-05T10:00:00+00:00")
                item["saved_at"] = "2026-10-05T10:00:00+00:00"
                self.tables[table].append(item)
                made.append(item)
            return Resp(201, made if "return=representation" in prefer else None)
        if method == "PATCH":
            n = 0
            for r in rows:
                if self._match(r, params):
                    r.update(body)
                    r["updated_at"] = "2026-10-05T11:00:00+00:00"
                    n += 1
            return Resp(204)
        if method == "DELETE":
            keep = [r for r in self.tables[table] if not (r["user_id"] == uid and self._match(r, params))]
            self.tables[table][:] = keep
            return Resp(204)
        return Resp(405, {})

    # ---- rpc
    def _rpc(self, name, headers, body):
        uid = self._uid(headers)
        if name in ("admin_user_id_by_email", "admin_revoke_sessions"):
            uid = "admin"
        if name == "log_event":
            self.logs.append({"user_id": uid, **body})
            return Resp(204)
        if uid is None:
            return Resp(401, {"message": "JWT missing"})
        if name in ("ai_quota", "consume_ai_call"):
            row = next((r for r in self.tables["ai_usage"] if r["user_id"] == uid), None)
            if row is None:
                row = {"user_id": uid, "calls": 0}
                self.tables["ai_usage"].append(row)
            if name == "consume_ai_call":
                if row["calls"] >= AI_LIMIT:
                    return Resp(200, {"allowed": False, "used": row["calls"], "limit": AI_LIMIT})
                row["calls"] += 1
                return Resp(200, {"allowed": True, "used": row["calls"], "limit": AI_LIMIT})
            return Resp(200, {"used": row["calls"], "limit": AI_LIMIT})
        if name == "admin_user_id_by_email":
            u = self.users.get(body["p_email"].lower())
            return Resp(200, u["id"] if u else None)
        if name == "admin_revoke_sessions":
            self.tokens = {t: v for t, v in self.tokens.items() if v != body["p_user"]}
            self.refresh = {t: v for t, v in self.refresh.items() if v != body["p_user"]}
            self.revoked = getattr(self, "revoked", []) + [body["p_user"]]
            return Resp(204)
        if name == "set_primary_resume":
            if not self.has_primary:
                return Resp(404, {"message": "Could not find the function public.set_primary_resume"})
            for r in self.tables["resumes"]:
                if r["user_id"] == uid:
                    r["is_primary"] = (r["id"] == body["p_id"])
            return Resp(204)
        if name == "delete_my_account":
            for t in self.tables:
                self.tables[t][:] = [r for r in self.tables[t] if r["user_id"] != uid]
            self.users = {e: u for e, u in self.users.items() if u["id"] != uid}
            self.logs.append({"user_id": None, "p_event": "account_deleted"})
            return Resp(204)
        return Resp(404, {"message": "no such function"})
