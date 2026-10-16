import os, sys, time
from test_app_flow import fake, new_app, texts, sign_up  # noqa: F401

EMAIL, PW = "otp.user@example.com", "correct-horse"


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _start_signup(at, email=EMAIL, pw=PW):
    at.session_state["login_mode__last"] = "Create account"; at.run()
    at.text_input(key="signup_email").set_value(email)
    at.text_input(key="signup_password").set_value(pw)
    at.text_input(key="signup_password2").set_value(pw)
    at.checkbox(key="signup_agree").check()
    _btn(at, "Create account").click(); at.run(); at.run()
    return at


def _enter(at, code):
    at.text_input(key="verify_code").set_value(code)
    _btn(at, "Verify and continue").click(); at.run()


def test_account_is_only_created_after_the_right_code(fake):
    at = _start_signup(new_app())
    assert not at.exception, [e.value for e in at.exception]
    assert fake.users == {}                                  # nothing exists in Supabase yet
    assert "auth" not in at.session_state
    assert fake.outbox[-1][:2] == (EMAIL, "signup")
    code = fake.last_code(EMAIL, "signup")
    assert len(code) == 6 and code.isdigit()

    _enter(at, "000000" if code != "000000" else "111111")
    assert fake.users == {} and "wrong" in texts(at).lower() or "isn't right" in texts(at)

    _enter(at, code); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert fake.users[EMAIL]["confirmed"]                    # created already verified
    assert at.session_state["auth"]["email"] == EMAIL        # and signed in
    assert "_verify_password" not in at.session_state        # the held password is gone


def test_code_locks_after_five_wrong_guesses_and_is_single_use(fake):
    at = _start_signup(new_app())
    good = fake.last_code(EMAIL, "signup")
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        _enter(at, bad)
    assert "Too many wrong attempts" in texts(at)
    _enter(at, good)                                         # even the right code is refused now
    assert fake.users == {} and "auth" not in at.session_state
    # a new code works, and can't be replayed
    at.session_state["_otp_signup"]["issued"] -= 120         # skip the resend cooldown
    _btn(at, "Send a new code").click(); at.run()
    fresh = fake.last_code(EMAIL, "signup")
    _enter(at, fresh); at.run()
    assert "auth" in at.session_state


def test_expired_code_is_refused(fake):
    at = _start_signup(new_app())
    at.session_state["_otp_signup"]["expires"] = time.time() - 1
    _enter(at, fake.last_code(EMAIL, "signup"))
    assert "expired" in texts(at).lower()
    assert fake.users == {}


def test_resend_cooldown(fake):
    at = _start_signup(new_app())
    _btn(at, "Send a new code").click(); at.run()
    assert "Please wait" in texts(at)
    assert len(fake.outbox) == 1


def test_signup_with_existing_email_sends_a_notice_instead_of_a_code(fake):
    at = sign_up(new_app())                                  # a@example.com registered
    _btn(at, "Log out").click(); at.run()
    before = len(fake.outbox)
    at2 = _start_signup(new_app(), email="a@example.com", pw="attacker-pass")
    assert not at2.exception, [e.value for e in at2.exception]
    # the owner is told they already have an account -- and NO sign-up code is emailed
    assert [o[:2] for o in fake.outbox[before:]] == [("a@example.com", "exists")]
    # the screen looks exactly like it does for a new address
    assert "Check your email" in texts(at2) and "a@example.com" in texts(at2)
    assert "already exists" not in texts(at2).lower() and "already have an account." not in texts(at2).lower().replace("already have an account? go back", "")
    # no code can succeed: wrong guesses behave like for a new address, then lock; password untouched
    for _ in range(5):
        _enter(at2, "123456")
    assert "isn't right" in texts(at2) or "Too many wrong attempts" in texts(at2)
    assert "Too many wrong attempts" in texts(at2)
    assert fake.users["a@example.com"]["password"] == "correct-horse"
    assert "auth" not in at2.session_state


def test_resend_for_existing_email_sends_another_notice_not_a_code(fake):
    at = sign_up(new_app())
    _btn(at, "Log out").click(); at.run()
    at2 = _start_signup(new_app(), email="a@example.com", pw="x-password-1")
    at2.session_state["_otp_signup"]["issued"] -= 120        # skip the cooldown
    _btn(at2, "Send a new code").click(); at2.run()
    kinds = [o[1] for o in fake.outbox if o[0] == "a@example.com"]
    assert kinds.count("signup") == 1 and kinds.count("exists") == 2   # only the original sign-up had a code


def _reset_request(at, email):
    at.session_state["login_mode__last"] = "Forgot password"; at.run()
    at.text_input(key="reset_email").set_value(email)
    _btn(at, "Send reset code").click(); at.run(); at.run()


def test_password_reset_changes_password_and_revokes_sessions(fake):
    at = sign_up(new_app())
    uid = fake.users["a@example.com"]["id"]
    assert fake.tokens                                       # the session from signing up
    _btn(at, "Log out").click(); at.run()

    at = new_app()
    _reset_request(at, "a@example.com")
    assert not at.exception, [e.value for e in at.exception]
    code = fake.last_code("a@example.com", "recovery")

    for c, p1, p2, msg in [("000000" if code != "000000" else "111111", "new-password-1", "new-password-1", "isn't right"),
                           (code, "new-password-1", "different", "don't match"),
                           (code, "short", "short", "at least 8")]:
        at.text_input(key="reset_code").set_value(c)
        at.text_input(key="reset_pw1").set_value(p1)
        at.text_input(key="reset_pw2").set_value(p2)
        _btn(at, "Set new password").click(); at.run()
        assert msg in texts(at), (msg, texts(at))
        assert fake.users["a@example.com"]["password"] == "correct-horse"

    # a still-signed-in device (stolen session) must be cut off by the reset
    other_device = fake._session(uid, "a@example.com")["access_token"]
    assert other_device in fake.tokens
    at.text_input(key="reset_code").set_value(code)
    at.text_input(key="reset_pw1").set_value("new-password-1")
    at.text_input(key="reset_pw2").set_value("new-password-1")
    _btn(at, "Set new password").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert fake.users["a@example.com"]["password"] == "new-password-1"
    assert other_device not in fake.tokens and uid in fake.revoked

    at.text_input(key="login_email").set_value("a@example.com")
    at.text_input(key="login_password").set_value("correct-horse")
    _btn(at, "Log in").click(); at.run()
    assert "Incorrect email or password" in texts(at)
    at.text_input(key="login_password").set_value("new-password-1")
    _btn(at, "Log in").click(); at.run()
    assert "auth" in at.session_state


def test_reset_for_unknown_email_looks_identical_but_sends_nothing(fake):
    at = new_app()
    _reset_request(at, "nobody@example.com")
    assert "reset code is on its way" in texts(at)
    assert fake.outbox == []
    # and a code can't be "guessed" into existence for them
    at.text_input(key="reset_code").set_value("123456")
    at.text_input(key="reset_pw1").set_value("new-password-1")
    at.text_input(key="reset_pw2").set_value("new-password-1")
    _btn(at, "Set new password").click(); at.run()
    assert "nobody@example.com" not in fake.users


def test_reset_keeps_encrypted_data_readable(fake):
    at = sign_up(new_app())
    at.session_state["resume_data"].personal_info.full_name = "Reset Person"; at.run()
    _btn(at, "Log out").click(); at.run()
    at = new_app(); _reset_request(at, "a@example.com")
    at.text_input(key="reset_code").set_value(fake.last_code("a@example.com", "recovery"))
    at.text_input(key="reset_pw1").set_value("brand-new-pass")
    at.text_input(key="reset_pw2").set_value("brand-new-pass")
    _btn(at, "Set new password").click(); at.run()
    at.text_input(key="login_email").set_value("a@example.com")
    at.text_input(key="login_password").set_value("brand-new-pass")
    _btn(at, "Log in").click(); at.run()
    _btn(at, "Load primary").click(); at.run(); at.run()
    assert at.session_state["resume_data"].personal_info.full_name == "Reset Person"


def test_not_configured_is_reported_not_crashed(fake, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "applymate.info@gmail.com")
    monkeypatch.delenv("SMTP_PASSWORD")   # e.g. the Gmail app password was never filled in
    at = new_app()
    at.session_state["login_mode__last"] = "Create account"; at.run()
    at.text_input(key="signup_email").set_value(EMAIL)
    at.text_input(key="signup_password").set_value(PW)
    at.text_input(key="signup_password2").set_value(PW)
    at.checkbox(key="signup_agree").check()
    _btn(at, "Create account").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("isn't set up" in e.value for e in at.error) and fake.outbox == []


def test_service_key_is_never_used_for_user_calls(fake):
    sign_up(new_app())
    admin_paths = [(m, p) for m, p in fake.calls if "/admin" in p]
    assert admin_paths                                       # admin API used for account creation...
    # ...and the fake asserts on every non-admin call that the publishable key (not the service key) was sent.
