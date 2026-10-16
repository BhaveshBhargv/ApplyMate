import os, sys

import pytest
import requests
from streamlit.testing.v1 import AppTest

from fake_supabase import FakeSupabase


@pytest.fixture()
def fake(monkeypatch):
    f = FakeSupabase()
    monkeypatch.setattr(requests, "request", f.request)
    monkeypatch.setattr(requests, "post", f.post)
    # make the logging thread synchronous so asserts are deterministic
    import types
    class Sync:
        def __init__(self, target, args=(), daemon=None):
            self.t, self.a = target, args
        def start(self):
            self.t(*self.a)
    from utils import applog
    monkeypatch.setattr(applog, "threading", types.SimpleNamespace(Thread=Sync))
    # ApplyMate sends its own codes: capture them instead of using SMTP, and give the app the
    # (fake) service key + SMTP settings it needs to consider email "configured".
    from utils import mailer, db, ai_assistant, embeddings
    from utils import supabase_client as sbc
    from utils import crypto
    # Hermetic: ignore .streamlit/secrets.toml entirely (it may hold REAL keys) -- config comes
    # only from the environment values set here, and every network call goes to the fake.
    env_only = lambda name: os.environ.get(name)    # noqa: E731
    for mod, attr in [(sbc, "_secret"), (mailer, "_secret"), (db, "_secret"),
                      (ai_assistant, "_get_secret"), (embeddings, "_get_secret")]:
        monkeypatch.setattr(mod, attr, env_only)
    for name in ("OPENROUTER_API_KEY", "EMBEDDINGS_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
                 "SMTP_USER", "SMTP_PASSWORD", "SMTP_HOST", "SMTP_FROM", "SMTP_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_testkey")
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", crypto.generate_master_key())
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_testservice")
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "no-reply@test")
    monkeypatch.setenv("SMTP_PASSWORD", "test-app-password")   # a configured login needs its password
    monkeypatch.setattr(mailer, "send_signup_code", lambda to, code, minutes: f.outbox.append((to, "signup", code)))
    monkeypatch.setattr(mailer, "send_reset_code", lambda to, code, minutes: f.outbox.append((to, "recovery", code)))
    monkeypatch.setattr(mailer, "send_account_exists", lambda to: f.outbox.append((to, "exists", "")))
    from utils import otp
    monkeypatch.setattr(otp, "allow_send", lambda email, now=None: True)   # tests send many codes
    CURRENT["fake"] = f
    return f


CURRENT = {}


def new_app():
    at = AppTest.from_file("app.py", default_timeout=60)
    return at.run()


def texts(at):
    out = []
    for coll in (at.markdown, at.error, at.success, at.info, at.warning, at.caption):
        out += [e.value for e in coll]
    return "\n".join(out)


def sign_up(at, email="a@example.com", pw="correct-horse"):
    at.session_state["login_mode__last"] = "Create account"
    at.run()
    at.text_input(key="signup_email").set_value(email)
    at.text_input(key="signup_password").set_value(pw)
    at.text_input(key="signup_password2").set_value(pw)
    at.checkbox(key="signup_agree").check()
    next(b for b in at.button if b.label == "Create account").click()
    at.run(); at.run()
    # step 2: the code ApplyMate emailed (captured by the fixture), then the account exists
    at.text_input(key="verify_code").set_value(CURRENT["fake"].last_code(email, "signup"))
    next(b for b in at.button if b.label == "Verify and continue").click()
    at.run()
    return at.run()


def test_logged_out_sees_only_login(fake):
    at = new_app()
    assert not at.exception
    assert "Log in to ApplyMate" in texts(at)
    assert not [b for b in at.button if b.label in ("Dashboard", "My Account")]


def test_signup_validation_and_errors(fake):
    at = new_app()
    at.session_state["login_mode__last"] = "Create account"
    at.run()
    at.text_input(key="signup_email").set_value("bad")
    next(b for b in at.button if b.label == "Create account").click()
    at.run()
    assert "valid email" in texts(at)
    at = new_app()
    at.session_state["login_mode__last"] = "Create account"; at.run()
    at.text_input(key="signup_email").set_value("a@example.com")
    at.text_input(key="signup_password").set_value("short")
    at.text_input(key="signup_password2").set_value("short")
    at.checkbox(key="signup_agree").check()
    next(b for b in at.button if b.label == "Create account").click(); at.run()
    assert "at least 8" in texts(at)
    at = new_app()
    at.text_input(key="login_email").set_value("nobody@example.com")
    at.text_input(key="login_password").set_value("whatever123")
    next(b for b in at.button if b.label == "Log in").click()
    at.run()
    assert "Incorrect email or password" in texts(at)


def test_full_flow_encrypts_and_isolates(fake):
    at = sign_up(new_app())
    assert not at.exception, at.exception
    # logged in: nav visible, profile + key created, tour marked done
    assert [b for b in at.button if b.label == "My Account"]
    assert len(fake.tables["profiles"]) == 1
    assert fake.tables["profiles"][0]["onboarding_completed_at"]
    assert fake.tables["profiles"][0]["wrapped_dek"].startswith("v1.")

    # edit the résumé -> autosaved, encrypted
    r = at.session_state["resume_data"]
    r.personal_info.full_name = "Zaphod Beeblebrox"
    r.personal_info.email = "zaphod@galaxy.example"
    r.personal_info.phone = "+441234567890"
    at.run()
    assert not at.exception, at.exception
    assert len(fake.tables["resumes"]) == 1
    blob = fake.tables["resumes"][0]["data_enc"]
    assert "Zaphod" not in blob and "galaxy" not in blob
    assert "Zaphod" not in str(fake.tables) and "galaxy.example" not in str(fake.tables)

    # an unchanged rerun does not write again
    before = [c for c in fake.calls if c[0] in ("POST", "PATCH") and "resumes" in c[1]]
    at.run()
    after = [c for c in fake.calls if c[0] in ("POST", "PATCH") and "resumes" in c[1]]
    assert len(before) == len(after)

    # log out wipes the session
    next(b for b in at.button if b.label == "Log out").click()
    at.run()
    assert "resume_data" not in at.session_state
    assert "Log in to ApplyMate" in texts(at)

    # a second user sees nothing of the first
    at = sign_up(at, "b@example.com", "another-pass1")
    assert at.session_state["resume_data"].personal_info.full_name == ""
    assert len(fake.tables["resumes"]) == 1

    # first user logs back in: résumé restored
    next(b for b in at.button if b.label == "Log out").click()
    at.run()
    at.text_input(key="login_email").set_value("a@example.com")
    at.text_input(key="login_password").set_value("correct-horse")
    next(b for b in at.button if b.label == "Log in").click()
    at.run()
    assert at.session_state["resume_data"].personal_info.full_name == ""      # starts empty after login
    next(b for b in at.button if b.label == "Load primary").click()
    at.run()
    assert at.session_state["resume_data"].personal_info.full_name == "Zaphod Beeblebrox"
    # logs carry no personal data
    assert "Zaphod" not in str(fake.logs) and "example.com" not in str(fake.logs)
    assert {l["p_event"] for l in fake.logs if "p_event" in l} >= {"signup_verified", "login_success", "logout"}


def page_as_user(fake, page, email="a@example.com"):
    """Run one page standalone, already signed in as `email` (created in the fake)."""
    import time, uuid
    uid = str(uuid.uuid4())
    fake.users[email] = {"id": uid, "password": "x"}
    sess = fake._session(uid, email)
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["auth"] = {"access_token": sess["access_token"], "refresh_token": sess["refresh_token"],
                                "expires_at": time.time() + 3000, "user_id": uid, "email": email}
    return at, uid


def test_account_page_and_delete(fake):
    from utils import crypto, db
    from models.resume_data import ResumeData
    at, uid = page_as_user(fake, "pages/5_👤_My_Account.py")
    at.run()
    assert not at.exception, at.exception
    # seed data through the real repository (session needs the auth for db calls)
    r = ResumeData(); r.personal_info.full_name = "Ada Lovelace"
    db.save_resume(None, "Ada CV", r) if False else None
    at.run()
    # seed through a page run: the repo functions need this session's state, so do it from the page's own session
    from streamlit.testing.v1 import AppTest as _A
    seeder = _A.from_string("""
import streamlit as st
from utils import db
from models.resume_data import ResumeData
r = ResumeData(); r.personal_info.full_name = 'Ada Lovelace'
rid = db.save_resume(None, 'Ada CV', r)
db.save_cover_letter(None, title='', company='Acme', role='Engineer', text='Dear Acme, hire me.', resume_id=rid)
db.save_job({'title':'Dev','company':'Acme','location':'London','url':'https://acme.example/j1','source':'Adzuna'})
db.save_job({'title':'Dev','company':'Acme','location':'London','url':'https://acme.example/j1','source':'Adzuna'})
st.write('seeded')
""", default_timeout=60)
    seeder.session_state["auth"] = at.session_state["auth"]
    seeder.run()
    assert not seeder.exception, seeder.exception
    assert len(fake.tables["saved_jobs"]) == 1                      # duplicate save ignored
    assert "Ada" not in str(fake.tables) and "Acme" not in str(fake.tables) and "acme.example" not in str(fake.tables)

    at.session_state["_dek"] = seeder.session_state["_dek"]; at.session_state["_profile"] = seeder.session_state["_profile"]
    body = ""
    for sec in ("Résumés", "Cover letters", "Saved jobs"):
        at.session_state["acct_section__last"] = sec; at.run()
        assert not at.exception, at.exception
        body += texts(at) + " ".join(str(x.value) for x in at.text_area)
    assert "Ada CV" in body and "Dear Acme" in body and "Dev" in body
    at.session_state["acct_section__last"] = "Privacy & data"; at.run()
    del_btn = next(b for b in at.button if b.label == "Delete all my stored data")
    assert del_btn.disabled
    at.text_input(key="acct_confirm_data").set_value("DELETE").run()
    next(b for b in at.button if b.label == "Delete all my stored data").click()
    at.run()
    assert not at.exception, at.exception
    assert fake.tables["resumes"] == [] and fake.tables["cover_letters"] == [] and fake.tables["saved_jobs"] == []
    assert len(fake.users) == 1                      # account still exists
    at.text_input(key="acct_confirm_account").set_value("DELETE").run()
    next(b for b in at.button if b.label == "Delete my account and everything in it").click()
    at.run()
    assert not at.exception, at.exception
    assert fake.users == {} and fake.tables["profiles"] == []
    assert "auth" not in at.session_state


def test_limits_and_quota(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    at, uid = page_as_user(fake, "pages/5_👤_My_Account.py")
    script = AppTest.from_string("""
import streamlit as st
from utils import db, ai_assistant
from models.resume_data import ResumeData
msgs = []
try:
    for i in range(6):
        db.save_resume(None, f"r{i}", ResumeData())
except db.DataError as e:
    msgs.append("LIMIT:" + str(e))
try:
    for i in range(15):
        assert db.consume_ai_call()["allowed"]     # use up the allowance without calling the model
    ai_assistant._generate("hi")                   # must be refused before any network call
except ai_assistant.AIError as e:
    msgs.append("AI:" + str(e))
st.write("|".join(msgs))
""", default_timeout=60)
    script.session_state["auth"] = at.session_state["auth"]
    script.run()
    assert not script.exception, script.exception
    out = script.markdown[0].value
    assert "LIMIT:You've reached the limit for stored résumés" in out, out
    assert "AI:You've used all 15 of today's AI requests" in out, out
    assert len(fake.tables["resumes"]) == 5
