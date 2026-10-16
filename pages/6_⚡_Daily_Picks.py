"""Daily Picks: ~10 UK jobs a day at visa-sponsoring employers, each with a ready-to-apply pack.

You save the job titles you want. Once a day (the first time you open this page) ApplyMate
searches them and picks the ~10 best fits -- UK locations, employers on the Home Office sponsor
register only. For each pick, one click prepares your pack (a résumé tailored to the job, a cover
letter, and your form details ready to copy); a second click opens the employer's own apply page.

ApplyMate never submits anything for you: employers' application systems don't accept
third-party submissions, and automated applying breaks LinkedIn's and Indeed's rules.
"""
import hashlib
from html import escape

import streamlit as st

from utils import ai_assistant as ai
from utils import apply_pack, auth, cover_letter_pdf, daily_picks, db, job_search
from utils.session_manager import get_resume_data, init_session_state
from utils.theme import inject_theme, render_download_footer, render_header, render_hero

inject_theme()
render_header("daily")
init_session_state()

if not auth.is_logged_in():
    st.info("Log in to use Daily Picks.")
    st.stop()

st.markdown(
    """
<style>
.rb-dp-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;}
.rb-dp-title{font-family:var(--display);font-weight:700;font-size:1.08rem;letter-spacing:-.02em;color:var(--text);line-height:1.2;}
.rb-dp-co{color:var(--muted);font-weight:500;font-size:.9rem;margin-top:2px;}
.rb-dp-tags{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none;}
.rb-dp-badge{font-family:var(--mono);font-size:.6rem;font-weight:500;letter-spacing:.12em;text-transform:uppercase;
  padding:3px 8px;border-radius:6px;border:1px solid var(--line);color:var(--muted);white-space:nowrap;}
.rb-dp-badge.sponsor{color:#1D4ED8;border-color:#BFDBFE;background:#EFF6FF;}
.rb-dp-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.4rem .9rem;margin:.7rem 0 .2rem;}
.rb-dp-fact-l{font-family:var(--mono);font-size:.58rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:0;}
.rb-dp-fact-v{font-size:.88rem;color:var(--text);margin:.05rem 0 0;font-weight:500;}
.rb-dp-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:.6rem;align-items:center;}
.rb-dp-chip{font-family:var(--mono);font-size:.7rem;padding:2px 8px;border-radius:7px;background:var(--wash);
  border:1px solid var(--chip-border);color:var(--accent-ink);}
</style>
""",
    unsafe_allow_html=True,
)

render_hero("Daily picks", "Today's best-fit jobs",
            "Up to 10 UK jobs a day at employers that can sponsor a visa, picked from the job titles you "
            "save. Prepare a pack, open the employer's page, and press Submit yourself.")

STATE_KEY = "_daily_state"


def _load_state() -> dict:
    if STATE_KEY not in st.session_state:
        try:
            st.session_state[STATE_KEY] = daily_picks.normalise_state(db.load_daily_picks())
        except db.DataError as exc:
            st.error(str(exc))
            st.session_state[STATE_KEY] = daily_picks.new_state()
    return st.session_state[STATE_KEY]


def _persist(state: dict) -> None:
    st.session_state[STATE_KEY] = state
    try:
        db.save_daily_picks(state)
    except db.DataError as exc:
        st.warning(f"Couldn't save your picks: {exc}")


def _resume_for_picks():
    """The résumé being edited if it has content, else the primary résumé (loaded once)."""
    current = get_resume_data()
    if current.searchable_text().strip():
        return current, "the résumé in your builder"
    cached = st.session_state.get("_dp_resume")
    if cached is None:
        try:
            primary = db.primary_resume()
        except db.DataError:
            primary = None
        cached = (primary.resume, f"your primary résumé (“{primary.label}”)") if primary else (None, "")
        st.session_state["_dp_resume"] = cached
    return cached


def _refresh(state: dict, resume, *, reason: str = "") -> None:
    with st.spinner("Finding today's picks — searching your job titles at licensed UK sponsors..."):
        built = daily_picks.build_picks(state["titles"], state["location"], resume, state["seen"])
    daily_picks.apply_build(state, built)
    st.session_state["_dp_build"] = built
    _persist(state)


state = _load_state()
resume, resume_label = _resume_for_picks()

# --- Job titles -------------------------------------------------------------------
with st.container(border=True):
    st.markdown("**Your job titles**")
    with st.form("dp_titles_form"):
        titles_text = st.text_area(
            "Job titles (one per line, up to 5)", value="\n".join(state["titles"]), height=110,
            placeholder="Data Analyst\nBusiness Analyst\nBI Developer",
            help="ApplyMate searches each title and keeps the best matches overall.")
        location = st.text_input("Location", value=state["location"],
                                 help="Only UK locations are included, at employers on the Home Office sponsor register.")
        saved = st.form_submit_button("Save titles & find picks", type="primary")
    if saved:
        titles = daily_picks.parse_titles(titles_text)
        if not titles:
            st.warning("Enter at least one job title.")
        else:
            state["titles"], state["location"] = titles, location.strip() or daily_picks.DEFAULT_LOCATION
            if resume is None:
                _persist(state)
            else:
                _refresh(state, resume)
                st.rerun()

if not state["titles"]:
    st.info("Add the job titles you're after and press **Save titles & find picks**.")
    render_download_footer(get_resume_data())
    st.stop()

if resume is None:
    st.warning("Add your résumé on the Dashboard (or set a primary résumé) so ApplyMate can rank jobs by "
               "how well they fit you and build your application packs.")
    render_download_footer(get_resume_data())
    st.stop()

# The first visit of each day builds the day's list.
if daily_picks.needs_refresh(state):
    _refresh(state, resume)

st.caption(f"Using {resume_label}. List for **{state['day']}**.")

built = st.session_state.get("_dp_build")
picks = daily_picks.pending_jobs(state)

head_l, head_r = st.columns([3, 1.3])
head_l.markdown(f'<p class="rb-eyebrow" style="margin-top:.4rem">{len(picks)} to review today</p>',
                unsafe_allow_html=True)
if head_r.button("Find different picks", key="dp_refresh", width="stretch",
                 help="Search again, skipping jobs you've already been shown."):
    _refresh(state, resume)
    st.rerun()

if built is not None:
    for note in built.notices:
        st.caption(f"Note — {note}")
    if built.register_ok and built.considered:
        st.caption(f"Looked at {built.considered} listings: {built.uk} in the UK; "
                   f"{len(built.jobs)} at licensed sponsors chosen.")

if not picks:
    done = len(state["jobs"]) - len(picks)
    if state["jobs"] and done:
        st.success("You've been through all of today's picks. Come back tomorrow, or press **Find different picks**.")
    else:
        st.info("No matching UK jobs at licensed sponsors right now. Try other job titles or a broader location. "
                + ("" if job_search.adzuna_configured() else
                   "Adding free Adzuna keys (see the Jobs page) widens the UK search a lot."))
    render_download_footer(resume)
    st.stop()


def _key(job) -> str:
    return hashlib.sha1((job.url or f"{job.title}|{job.company}").encode("utf-8")).hexdigest()[:12]


def _fact(label: str, value: str) -> str:
    return (f'<div><p class="rb-dp-fact-l">{escape(label)}</p>'
            f'<p class="rb-dp-fact-v">{escape(value) or "—"}</p></div>')


def _prepare(job, key: str) -> None:
    with st.spinner("Preparing your pack — loading the job, tailoring your résumé, writing the cover letter..."):
        jd = job_search.fetch_full_description(job)
        try:
            pack = apply_pack.build_pack(resume, job, jd)
        except ai.AIError as exc:
            st.session_state[f"_dp_note_{key}"] = (
                f"The cover letter couldn't be written ({exc}). Your tailored résumé and form details are ready; "
                "use the button below to try the letter again.")
            pack = apply_pack.build_pack(resume, job, jd, write_letter=False)
    st.session_state[f"_dp_pack_{key}"] = pack


for job in picks:
    key = _key(job)
    pack = st.session_state.get(f"_dp_pack_{key}")
    with st.container(border=True):
        badges = ""
        if job.sponsor_licensed:
            badges += '<span class="rb-dp-badge sponsor" title="Employer is on the UK Home Office sponsor register">Visa sponsor</span>'
        if job.sponsorship_text == "yes":
            badges += '<span class="rb-dp-badge sponsor">Sponsorship offered</span>'
        st.markdown(
            '<div class="rb-dp-top"><div>'
            f'<div class="rb-dp-title">{escape(job.title)}</div>'
            f'<div class="rb-dp-co">{escape(job.company) or "—"}</div></div>'
            f'<div class="rb-dp-tags"><span class="rb-dp-badge">{escape(job.source)}</span>{badges}</div></div>'
            '<div class="rb-dp-facts">'
            + _fact("Company", job.company) + _fact("Job title", job.title)
            + _fact("Sponsorship", "Licensed sponsor" + (" · offered in listing" if job.sponsorship_text == "yes" else ""))
            + _fact("Salary", job.salary or "Not stated") + _fact("Location", job.location)
            + "</div>"
            + ('<div class="rb-dp-chips">' + "".join(f'<span class="rb-dp-chip">{escape(s)}</span>'
                                                   for s in job.matched_skills[:8]) + "</div>"
               if job.matched_skills else ""),
            unsafe_allow_html=True,
        )

        if pack is None:
            actions = st.empty()
            with actions.container():
                a, b, _sp = st.columns([1.6, 1, 2])
                prepare_clicked = a.button(
                    "Prepare application", key=f"dp_prep_{key}", type="primary", width="stretch",
                    help="Tailors your résumé, writes a cover letter, and gets your form details ready "
                         "(uses 1–2 of your daily AI requests).")
                skip_clicked = b.button("Skip", key=f"dp_skip_{key}", width="stretch")
            if skip_clicked:
                daily_picks.mark(state, job.url, "skipped")
                _persist(state)
                st.rerun()
            if not prepare_clicked:
                continue
            _prepare(job, key)             # show the pack right here -- no page reload in between
            actions.empty()
            pack = st.session_state.get(f"_dp_pack_{key}")

        # ---- the ready-to-apply pack ----------------------------------------------
        st.markdown("###### Your application pack")
        note = st.session_state.get(f"_dp_note_{key}")
        if note:
            st.warning(note)
        st.caption(pack.tailoring_note)

        cover_text = ""
        if not pack.cover_text and st.button("Try writing the cover letter again", key=f"dp_retry_{key}"):
            st.session_state.pop(f"_dp_pack_{key}", None)
            st.session_state.pop(f"_dp_note_{key}", None)
            _prepare(job, key)
            st.rerun()
        if pack.cover_text:
            cover_text = st.text_area("Cover letter (edit freely)", value=pack.cover_text, height=260,
                                      key=f"dp_cover_{key}")
            if pack.cover_flags:
                st.warning("Check before sending — these appear in the letter but aren't on your résumé: "
                           + ", ".join(escape(f.text) for f in pack.cover_flags))

        d1, d2, d3, d4 = st.columns([1.2, 1.2, 1.5, 1.2])
        d1.download_button("Résumé PDF", pack.resume_pdf, file_name=pack.resume_file, mime="application/pdf",
                           key=f"dp_dlr_{key}", width="stretch")
        if cover_text.strip():
            d2.download_button(
                "Cover letter PDF",
                cover_letter_pdf.build_cover_letter_pdf(resume, cover_text, company=job.company, role=job.title),
                file_name=pack.cover_file, mime="application/pdf", key=f"dp_dlc_{key}", width="stretch")
        link = job.url if (job.url or "").startswith(("http://", "https://")) else ""
        if link:
            d3.link_button("Apply now on employer's site ↗", link, type="primary", width="stretch")
        if d4.button("I've applied", key=f"dp_done_{key}", width="stretch",
                     help="Saves this job to My Account as Applied and removes it from today's list."):
            try:
                db.save_job(job, status="applied")
            except db.DataError as exc:
                st.error(str(exc))
            daily_picks.mark(state, job.url, "applied")
            _persist(state)
            st.session_state.pop("_saved_job_hashes", None)
            st.rerun()

        with st.expander("Details to copy into the application form"):
            st.caption("Click the copy icon on any field. Upload the PDFs above where the form asks for a "
                       "résumé / cover letter.")
            for label, value in pack.fields:
                c1, c2 = st.columns([1, 2.4])
                c1.markdown(f"<p class='rb-dp-fact-l' style='margin-top:.55rem'>{escape(label)}</p>",
                            unsafe_allow_html=True)
                c2.code(value, language=None)
        if st.button("Skip this job", key=f"dp_skip2_{key}"):
            daily_picks.mark(state, job.url, "skipped")
            _persist(state)
            st.rerun()

render_download_footer(resume)
