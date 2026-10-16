import os, sys

from streamlit.testing.v1 import AppTest
from models.resume_data import ResumeData
from test_app_flow import fake, new_app, sign_up, page_as_user, texts  # noqa: F401


def _name(at):
    return at.session_state["resume_data"].personal_info.full_name


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _primaries(fake):
    return [r["is_primary"] for r in fake.tables["resumes"]]


def _start_second_resume(at, name):
    """What 'New blank résumé' does, then type a name: autosave stores it as a second résumé."""
    for k in ("_active_resume_id", "_active_resume_label", "_active_resume_primary"):
        try:
            del at.session_state[k]
        except KeyError:
            pass
    r = ResumeData(); r.personal_info.full_name = name
    at.session_state["resume_data"] = r
    at.run()


def _labels(at):
    return {b.label for b in at.button if b.label in ("Set as primary", "Load primary", "New CV")}


def _login_again(at):
    _btn(at, "Log out").click(); at.run()
    at.session_state["login_mode__last"] = "Log in"; at.run()
    at.text_input(key="login_email").set_value("a@example.com")
    at.text_input(key="login_password").set_value("correct-horse")
    _btn(at, "Log in").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_button_visibility_follows_state(fake):
    at = sign_up(new_app())
    # brand new account, empty builder: nothing to set / load / start over from
    assert _labels(at) == set()

    at.session_state["resume_data"].personal_info.full_name = "Primary Person"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _primaries(fake) == [True]                       # the first résumé is primary automatically
    # the primary is in the builder: no Set as primary, no Load primary -- only New CV
    assert _labels(at) == {"New CV"}
    assert any("primary" in m.value and "Editing" in m.value for m in at.markdown)

    # New CV: the builder empties, and the buttons change with it
    _btn(at, "New CV").click(); at.run(); at.run()   # 2nd run = after st.rerun()
    assert not at.exception, [e.value for e in at.exception]
    assert _name(at) == ""
    assert _labels(at) == {"Load primary"}                  # blank + a primary exists
    assert len(fake.tables["resumes"]) == 1                 # the first CV is still stored

    # start typing the new CV: a second, non-primary résumé; now all three apply
    at.session_state["resume_data"].personal_info.full_name = "Second Person"
    at.run()
    assert sorted(_primaries(fake)) == [False, True]
    assert _labels(at) == {"Set as primary", "Load primary", "New CV"}

    # Load primary: switches to it, and its extra buttons disappear
    _btn(at, "Load primary").click(); at.run(); at.run()   # 2nd run = after st.rerun()
    assert not at.exception, [e.value for e in at.exception]
    assert _name(at) == "Primary Person"
    assert _labels(at) == {"New CV"}

    # edits to the loaded primary are saved to that same résumé
    at.session_state["resume_data"].personal_info.full_name = "Primary Person Edited"
    at.run()
    assert len(fake.tables["resumes"]) == 2


def test_login_starts_empty_then_load_primary(fake):
    at = sign_up(new_app())
    at.session_state["resume_data"].personal_info.full_name = "Primary Person"
    at.run()
    _start_second_resume(at, "Newer Person")
    assert sorted(_primaries(fake)) == [False, True]
    _login_again(at)
    assert _name(at) == ""                                  # empty after login, whatever is stored
    assert _labels(at) == {"Load primary"}
    _btn(at, "Load primary").click(); at.run(); at.run()   # 2nd run = after st.rerun()
    assert _name(at) == "Primary Person"                    # the primary, not the newer one
    assert _labels(at) == {"New CV"}


def test_set_as_primary_switches_the_flag(fake):
    at = sign_up(new_app())
    at.session_state["resume_data"].personal_info.full_name = "Primary Person"
    at.run()
    _start_second_resume(at, "Second Person")
    _btn(at, "Set as primary").click(); at.run(); at.run()   # 2nd run = after st.rerun()
    assert not at.exception, [e.value for e in at.exception]
    assert _primaries(fake).count(True) == 1                # exactly one primary
    assert _labels(at) == {"New CV"}                        # the one being edited is primary now
    _login_again(at)
    _btn(at, "Load primary").click(); at.run(); at.run()   # 2nd run = after st.rerun()
    assert _name(at) == "Second Person"


def test_my_account_make_primary_and_delete_promotes(fake):
    from streamlit.testing.v1 import AppTest as _A
    at, uid = page_as_user(fake, "pages/5_\U0001F464_My_Account.py")
    seeder = _A.from_string("""
import streamlit as st
from utils import db
from models.resume_data import ResumeData
a = ResumeData(); a.personal_info.full_name = 'A'
b = ResumeData(); b.personal_info.full_name = 'B'
ida = db.save_resume(None, 'Resume A', a, primary=True)
idb = db.save_resume(None, 'Resume B', b)
st.session_state['ids'] = (ida, idb)
""", default_timeout=60)
    seeder.session_state["auth"] = at.session_state["auth"]
    seeder.run(); assert not seeder.exception, seeder.exception
    ida, idb = seeder.session_state["ids"]
    at.session_state["_dek"] = seeder.session_state["_dek"]; at.session_state["_profile"] = seeder.session_state["_profile"]
    at.run(); assert not at.exception, [e.value for e in at.exception]
    body = " ".join(m.value for m in at.markdown)
    assert "★ primary" in body
    # primary is listed first
    titles = [m.value for m in at.markdown if "<div class=\"rb-acct-title\">" in m.value]
    assert "Resume A" in titles[0] and "primary" in titles[0]
    # make B primary
    _btn(at, "Make primary").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    flags = {r["id"]: r["is_primary"] for r in fake.tables["resumes"]}
    assert flags == {ida: False, idb: True}
    # delete the primary (B): A is promoted
    pops = [b for b in at.button if b.label == "Yes, delete it"]
    # the first card listed is now B (primary); its delete confirm button comes first
    pops[0].click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert {r["id"]: r["is_primary"] for r in fake.tables["resumes"]} == {ida: True}


def test_database_without_primary_column_still_works(fake):
    fake.has_primary = False                                # an un-migrated database
    at = sign_up(new_app())
    at.session_state["resume_data"].personal_info.full_name = "Legacy Person"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(fake.tables["resumes"]) == 1                 # saving still works
    assert not [b for b in at.button if b.label in ("Set as primary", "Load primary")]
    assert "schema" in texts(at)                            # tells the user to run the SQL
