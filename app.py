"""ApplyMate -- entry point.

Everything sits behind a login. Signed-out visitors only ever see the login /
sign-up screen; signed-in users get five pages: the Dashboard (edit + live
preview + download), ATS Match, Jobs (search live openings), Cover Letter (a
grounded letter for one job) and My Account (saved résumés, letters, jobs and
privacy controls). Registered with st.navigation(position="hidden"); each page
draws its own custom top bar via utils.theme.render_header. set_page_config
lives here so it runs exactly once for the whole app.

The résumé being edited is mirrored (encrypted) to the user's account at the end
of every run -- see utils.account.autosave.
"""
import streamlit as st

st.set_page_config(page_title="ApplyMate", page_icon="📄", layout="wide", initial_sidebar_state="collapsed")

from utils import account, applog, auth, db, walkthrough  # noqa: E402  (after set_page_config)
from utils import supabase_client as sb  # noqa: E402
from utils.theme import inject_theme  # noqa: E402

pages = [
    st.Page("pages/1_🧭_Dashboard.py", title="Dashboard", icon="🧭", url_path="dashboard", default=True),
    st.Page("pages/2_🎯_ATS_Match.py", title="ATS Match", icon="🎯", url_path="ats"),
    st.Page("pages/3_💼_Jobs.py", title="Jobs", icon="💼", url_path="jobs"),
    st.Page("pages/4_✉️_Cover_Letter.py", title="Cover Letter", icon="✉️", url_path="cover-letter"),
    st.Page("pages/5_👤_My_Account.py", title="My Account", icon="👤", url_path="account"),
]

# --- Gate: configuration, then login -------------------------------------------
if not sb.is_configured() or not db.master_key_configured():
    inject_theme()
    st.error("ApplyMate isn't fully configured yet. Set SUPABASE_URL, SUPABASE_KEY and "
             "ENCRYPTION_MASTER_KEY in the app secrets.")
    st.stop()

auth.restore_from_cookie()
if auth.is_logged_in() and auth.access_token() is None:
    st.rerun()   # the session couldn't be refreshed: fall through to the login screen

if not auth.is_logged_in():
    inject_theme()
    auth.render_login_page()
    auth.flush_cookie()
    st.stop()

problem = account.bootstrap()
if problem:
    inject_theme()
    st.error(problem)
    if st.button("Log out"):
        auth.log_out()
        st.rerun()
    st.stop()

# --- Signed in -----------------------------------------------------------------
try:
    walkthrough.maybe_show()
    auth.flush_cookie()
    st.navigation(pages, position="hidden").run()
except Exception as exc:  # noqa: BLE001 -- log the failure type, then let Streamlit show it
    applog.log("unhandled_exception", "error", "error", type=type(exc).__name__)
    raise
finally:
    # Runs after every page, including ones that end in st.stop()/st.switch_page().
    account.autosave()
