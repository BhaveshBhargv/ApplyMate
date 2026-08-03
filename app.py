"""AI Résumé Studio -- entry point.

Two pages only: the Dashboard (edit + live preview + download) and ATS Match.
Registered with st.navigation(position="hidden"); each page draws its own
custom top bar via utils.theme.render_header. set_page_config lives here so it
runs exactly once for the whole app.
"""
import streamlit as st

st.set_page_config(page_title="Résumé Studio", page_icon="📄", layout="wide", initial_sidebar_state="collapsed")

pages = [
    st.Page("pages/1_🧭_Dashboard.py", title="Dashboard", icon="🧭", url_path="dashboard", default=True),
    st.Page("pages/2_🎯_ATS_Match.py", title="ATS Match", icon="🎯", url_path="ats"),
    st.Page("pages/3_💼_Jobs.py", title="Jobs", icon="💼", url_path="jobs"),
]
st.navigation(pages, position="hidden").run()
