"""Core regression tests: encryption, ATS scoring quality, the ATS page's behaviour, and a smoke
render of every page with a realistic résumé."""
from dataclasses import dataclass

import pytest
from streamlit.testing.v1 import AppTest

from models.resume_data import EducationEntry, ExperienceEntry, ProjectEntry, ResumeData, SkillCategory
from test_app_flow import fake, page_as_user  # noqa: F401  (fixture)
from utils import agents, ai_assistant, ats_analyzer, crypto

PAGES = ["pages/1_\U0001F9ED_Dashboard.py", "pages/2_\U0001F3AF_ATS_Match.py", "pages/3_\U0001F4BC_Jobs.py",
         "pages/4_✉️_Cover_Letter.py", "pages/5_\U0001F464_My_Account.py", "pages/6_⚡_Daily_Picks.py"]


def software_resume() -> ResumeData:
    r = ResumeData()
    r.personal_info.full_name = "Sam Dev"
    r.personal_info.professional_summary = "Backend developer building REST APIs in Python and Django on AWS."
    r.experience.append(ExperienceEntry(job_title="Software Engineer", company="Acme", is_current=True, start_date="Jan 2022",
                                        bullet_points=["Built RESTful APIs with Python and Django serving 2M requests/day",
                                                       "Deployed services to AWS using Docker and Kubernetes with CI/CD pipelines",
                                                       "Wrote unit tests with pytest, improving coverage to 90%",
                                                       "Mentored 2 junior developers and led code reviews"]))
    r.education.append(EducationEntry(institution="UCL", degree="BSc", field_of_study="Computer Science",
                                      start_date="Sep 2017", end_date="Jun 2020"))
    r.skills.append(SkillCategory(category_name="Tech", skills=["Python", "Django", "PostgreSQL", "Docker", "Git", "JS"]))
    r.projects.append(ProjectEntry(name="Dashboard", description="Reporting dashboard", technologies=["Python", "SQL"]))
    return r


SOFTWARE_JD = """Software Engineer (Python)
What you'll do
- Design, build and maintain backend services and APIs in Python
- Work with PostgreSQL and optimise database queries
- Deploy to the cloud (AWS) using containers and CI/CD
- Write automated tests and participate in code reviews
What you'll need
- 3+ years of experience as a software engineer
- Strong Python skills, experience with Django or Flask
- Knowledge of Docker, Kubernetes and microservices
- Bachelor's degree in Computer Science or equivalent experience
- Excellent communication skills
"""

CIVIL_JD = """Graduate Civil Engineer
- Assist with site supervision and the execution of civil works, ensuring compliance with design specifications.
- Prepare drawings and reports using AutoCAD and Revit.
- Bachelor's degree in Civil Engineering or a related field.
- Strong communication and teamwork skills.
"""


# ------------------------------------------------------------------------------- crypto
def test_encryption_roundtrip_and_binding():
    master = crypto.parse_master_key(crypto.generate_master_key())
    dek = crypto.new_dek()
    assert crypto.unwrap_dek(master, "u1", crypto.wrap_dek(master, "u1", dek)) == dek
    with pytest.raises(crypto.CryptoError):
        crypto.unwrap_dek(master, "u2", crypto.wrap_dek(master, "u1", dek))     # another user's key blob
    token = crypto.encrypt_json(dek, "u1", "resume", "r1", {"name": "Jörg 😀"})
    assert "Jörg" not in token and crypto.decrypt_json(dek, "u1", "resume", "r1", token) == {"name": "Jörg 😀"}
    for args in [("u2", "resume", "r1"), ("u1", "cover_letter", "r1"), ("u1", "resume", "r2")]:
        with pytest.raises(crypto.CryptoError):
            crypto.decrypt_json(dek, *args, token)                              # moved to another user/kind/row
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_json(dek, "u1", "resume", "r1", token[:-4] + "AAAA")      # tampered


# ---------------------------------------------------------------------------- ATS scoring
def test_ats_scores_a_good_match_high_and_a_bad_match_low():
    good = ats_analyzer.analyze(software_resume(), SOFTWARE_JD)
    assert good.overall >= 65 and good.keyword_pct >= 55
    assert good.title.score == 100 and good.education.score == 100
    assert "Flask" in good.skills_missing                                         # honest gaps stay gaps
    assert ats_analyzer.analyze(software_resume(), CIVIL_JD).overall < 30         # wrong field -> low


def test_ats_matches_stems_and_variants_across_the_whole_resume():
    r = ResumeData()
    r.experience.append(ExperienceEntry(job_title="Engineer", bullet_points=["Supervised site works, using Auto CAD and JS"]))
    rep = ats_analyzer.analyze(r, "We need site supervision, AutoCAD and JavaScript experience.")
    found = {m.jd_skill.lower() for m in rep.skills_matched}
    assert {"site supervision", "autocad", "javascript"} <= found


def test_a_resume_copied_from_the_job_scores_near_100():
    jd = SOFTWARE_JD
    r = ResumeData()
    r.experience.append(ExperienceEntry(job_title="Software Engineer",
                                        bullet_points=[l.strip("- ") for l in jd.splitlines() if l.startswith("-")]))
    r.education.append(EducationEntry(degree="Bachelor of Science", field_of_study="Computer Science"))
    r.skills.append(SkillCategory(category_name="s", skills=["Python", "Django", "Flask", "PostgreSQL", "Docker",
                                                            "Kubernetes", "microservices", "AWS", "CI/CD", "communication"]))
    assert ats_analyzer.analyze(r, jd).overall >= 95


def test_empty_job_description_is_zero_not_a_crash():
    assert ats_analyzer.analyze(software_resume(), "").overall == 0


# ------------------------------------------------------------------------- the ATS page
def test_analyze_makes_no_ai_call_and_the_button_makes_one(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    calls = []
    monkeypatch.setattr(agents, "generate_suggestions", lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(ai_assistant, "_generate", lambda *a, **k: calls.append("llm") or "")
    at, _ = page_as_user(fake, PAGES[1])
    at.session_state["resume_data"] = software_resume(); at.run()
    at.text_area[0].set_value(SOFTWARE_JD)
    next(b for b in at.button if b.label == "Analyze match").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["ats_report"].overall > 0 and calls == []
    next(b for b in at.button if b.label == "Generate suggestions").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls == [1]


@dataclass
class _OldReport:       # the shape stored by an older version of the app (no keyword_pct / title / ...)
    overall: int = 50


def test_stale_report_from_an_older_version_does_not_crash(fake):
    at, _ = page_as_user(fake, PAGES[1])
    at.session_state["resume_data"] = software_resume()
    at.session_state["ats_report"] = _OldReport()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "ats_report" not in at.session_state


# ------------------------------------------------------------------------- every page renders
@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_a_realistic_resume(fake, page):
    at, _ = page_as_user(fake, page)
    at.session_state["resume_data"] = software_resume()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("section", ["Personal", "Education", "Experience", "Projects", "Skills", "Import"])
@pytest.mark.parametrize("prefix", ["Add ", "Remove"])
def test_dashboard_sections_and_their_add_remove_buttons(fake, section, prefix):
    # A fresh page per click: after a button that calls st.rerun(), AppTest keeps the pre-rerun
    # element tree, so reusing one instance would try to read widgets that no longer exist.
    at, _ = page_as_user(fake, PAGES[0])
    at.session_state["resume_data"] = software_resume()
    at.session_state["dash_section__last"] = section
    at.run()
    assert not at.exception, (section, [e.value for e in at.exception])
    for b in [b for b in at.button if b.label.startswith(prefix)][:1]:
        b.click(); at.run()
        assert not at.exception, (section, b.label, [e.value for e in at.exception])


# ------------------------------------------------------------- Jobs page: role suggestions
def _chips(at):
    return [b.label for b in at.button if b.key and b.key.startswith("role_sugg_")]


def test_role_chips_appear_instantly_and_the_ai_upgrade_swaps_them_in(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    calls = []

    def ai_roles(facts, max_roles=3, quick=False):
        calls.append(quick)
        return ["AI Role One", "AI Role Two"]

    monkeypatch.setattr(ai_assistant, "suggest_job_roles", ai_roles)
    at, _ = page_as_user(fake, PAGES[2])
    at.session_state["resume_data"] = software_resume()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls == [True]                                      # one AI call, with the short time budget
    assert at.session_state["job_role_suggestions"][2] == "ai"
    at.run()                                                    # the redrawn page shows the AI's roles
    assert _chips(at) == ["AI Role One", "AI Role Two"] and calls == [True]   # ...without asking again


def test_a_slow_or_failing_ai_never_blocks_the_page_or_repeats(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    calls = []

    def broken(facts, max_roles=3, quick=False):
        calls.append(1)
        raise ai_assistant.AIError("busy")

    monkeypatch.setattr(ai_assistant, "suggest_job_roles", broken)
    at, _ = page_as_user(fake, PAGES[2])
    at.session_state["resume_data"] = software_resume()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _chips(at)                                           # keyword-based chips are there regardless
    at.run(); at.run()
    assert calls == [1]                                         # tried once, never retried on reruns
    assert at.session_state["job_role_suggestions"][2] == "tried"


def test_role_suggestions_state_from_an_older_version_does_not_crash(fake):
    at, _ = page_as_user(fake, PAGES[2])
    at.session_state["resume_data"] = software_resume()
    at.session_state["job_role_suggestions"] = ("old-fingerprint", ["Old Role"])   # the old 2-tuple shape
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_quick_ai_calls_give_up_fast(monkeypatch):
    """The page-load AI call must be bounded: one short try on at most two models (it used to be
    able to run for minutes: 2 models x 3 tries x 120 s)."""
    import openai
    seen = {"timeouts": [], "models": []}

    class FakeClient:
        def __init__(self, **kw):
            seen["timeouts"].append(kw["timeout"])
            self.chat = type("C", (), {"completions": type("X", (), {"create": staticmethod(self._create)})})()

        @staticmethod
        def _create(model, **kw):
            seen["models"].append(model)
            raise RuntimeError("503 service unavailable")      # a transient failure

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(ai_assistant, "_get_secret", lambda name: __import__("os").environ.get(name))
    monkeypatch.setattr(ai_assistant, "_enforce_daily_limit", lambda: None)
    monkeypatch.setattr(ai_assistant.time, "sleep", lambda s: seen.setdefault("slept", []).append(s))
    with pytest.raises(ai_assistant.AIError):
        ai_assistant._generate("hi", quick=True)
    assert seen["timeouts"] == [ai_assistant._QUICK_TIMEOUT_SECONDS] and len(seen["models"]) <= 2
    assert not seen.get("slept")                                # no backoff sleeping either
