import os, sys

import pytest
from streamlit.testing.v1 import AppTest

from models.resume_data import ExperienceEntry, ProjectEntry, ResumeData, SkillCategory
from test_app_flow import fake, page_as_user  # noqa: F401
from utils import apply_pack, cover_letter as cl, daily_picks, job_search, sponsors
from utils.job_search import JobPosting, SearchResult

PAGE = "pages/6_\u26A1_Daily_Picks.py"
REGISTER = frozenset({"acme", "beta", "gamma", "bigco"})


def J(title, company, location, url, source="Greenhouse", salary="", desc="python sql reporting", posted="2026-10-10"):
    return JobPosting(title=title, company=company, location=location, url=url, source=source,
                      salary=salary, description=desc, posted=posted)


JOBS = [
    J("Data Analyst", "Acme Ltd", "London, UK", "https://acme.example/1", salary="£45,000 - £55,000"),
    J("Data Analyst", "Nope Ltd", "London, UK", "https://nope.example/2"),                          # not a sponsor
    J("Data Analyst", "Beta PLC", "Berlin, Germany", "https://beta.example/3"),                      # not UK
    J("Data Analyst", "BigCo Limited", "London", "https://bigco.example/4",
      desc="Great role. We cannot offer visa sponsorship."),                                        # says no
    J("Business Analyst", "Beta plc", "Remote - UK", "https://beta.example/5", desc="sql excel reporting"),
    J("Data Analyst", "Gamma Limited", "Slough, Berkshire", "https://gamma.example/6", source="Adzuna"),
]


@pytest.fixture()
def world(monkeypatch):
    calls = []
    monkeypatch.setattr(sponsors, "_register", lambda: REGISTER)

    def fake_search(title, location, country, work_type, industry="Any"):
        calls.append(title)
        return SearchResult(jobs=[JobPosting(**j.__dict__) for j in JOBS], notices=[])
    monkeypatch.setattr(job_search, "search_jobs", fake_search)
    monkeypatch.setattr(job_search, "fetch_full_description", lambda job: job.description + " Python SQL.")
    monkeypatch.setattr(cl, "generate", lambda resume, jd, company="", role="", api_key=None:
                        cl.CoverLetter(text=f"Dear {company} team, I'd love to be your {role}.",
                                       company=company, role=role, word_count=12))
    return calls


def _resume():
    r = ResumeData()
    r.personal_info.full_name = "Ada Lovelace"; r.personal_info.email = "ada@example.com"
    r.personal_info.phone = "+441234567890"; r.personal_info.location = "London"
    r.experience.append(ExperienceEntry(job_title="Data Analyst", company="Old Co", bullet_points=[
        "Managed office rota", "Built SQL reports in Python for finance"]))
    r.skills.append(SkillCategory(category_name="Tools", skills=["Photoshop", "SQL", "Python"]))
    r.projects = [ProjectEntry(name="Garden app", description="Mobile gardening app"),
                  ProjectEntry(name="Sales dashboard", description="SQL and Python reporting dashboard")]
    return r


# ---------------------------------------------------------------------- unit: sponsors
def test_sponsor_helpers():
    assert sponsors.normalize_company("The Acme Group (UK) Ltd.") == "acme group"
    assert sponsors.normalize_company("Marks & Spencer PLC") == "marks and spencer"
    assert sponsors.is_licensed("Acme Limited", REGISTER) is True
    assert sponsors.is_licensed("Acmee Limited", REGISTER) is False          # exact match only
    assert sponsors.is_licensed("", REGISTER) is None
    assert sponsors.is_licensed("Acme", None.__class__ and frozenset()) is False
    assert sponsors.sponsorship_in_text("We offer visa sponsorship") == "yes"
    assert sponsors.sponsorship_in_text("We are unable to sponsor visas") == "no"
    assert sponsors.sponsorship_in_text("Right to work required. No visa sponsorship available.") == "no"
    assert sponsors.sponsorship_in_text("Nice team") == ""
    assert sponsors.is_uk_location("London, UK") and not sponsors.is_uk_location("Worldwide")
    assert sponsors.is_uk_location("Slough, Berkshire", trusted_uk_source=True)
    # real-world false positives that slipped through once: US places sharing UK names
    for not_uk in ("Austin; New York City; United States", "Cambridge, MA", "Manchester, NH",
                   "New South Wales, Australia", "New England, USA", "Dublin, Ireland", "Berlin, Germany"):
        assert not sponsors.is_uk_location(not_uk), not_uk
    for uk in ("London; New York City", "Manchester, UK", "York, England", "Belfast, Northern Ireland", "Leeds"):
        assert sponsors.is_uk_location(uk), uk


# --------------------------------------------------------------------- unit: build picks
def test_build_picks_applies_the_users_rules(world):
    built = daily_picks.build_picks(["Data Analyst", "Business Analyst"], "United Kingdom", _resume(), {})
    names = [(j.company, j.title) for j in built.jobs]
    assert ("Nope Ltd", "Data Analyst") not in names                      # not a licensed sponsor
    assert ("Beta PLC", "Data Analyst") not in names                      # not UK
    assert ("BigCo Limited", "Data Analyst") not in names                 # listing says no sponsorship
    assert {c for c, _ in names} == {"Acme Ltd", "Beta plc", "Gamma Limited"}   # deduped across both titles
    assert len(built.jobs) == len(set(j.url for j in built.jobs))
    assert all(j.sponsor_licensed for j in built.jobs)
    assert len(world) == 2                                                # one search per title
    # already-seen jobs never come back
    again = daily_picks.build_picks(["Data Analyst"], "United Kingdom", _resume(),
                                    {"https://acme.example/1": "2026-10-10"})
    assert "https://acme.example/1" not in [j.url for j in again.jobs]


def test_never_more_than_ten(world, monkeypatch):
    many = [J("Data Analyst", "Acme Ltd", "London, UK", f"https://acme.example/{i}") for i in range(30)]
    monkeypatch.setattr(job_search, "search_jobs", lambda *a, **k: SearchResult(jobs=many))
    assert len(daily_picks.build_picks(["Data Analyst"], "UK", _resume(), {}).jobs) == daily_picks.MAX_PER_COMPANY
    # ...so with 30 jobs spread over 8 employers the full ten come through, two per employer at most
    spread = [J("Data Analyst", f"Acme {i % 8} Ltd", "London, UK", f"https://acme.example/{i}") for i in range(30)]
    monkeypatch.setattr(sponsors, "_register", lambda: frozenset(f"acme {i}" for i in range(8)))
    monkeypatch.setattr(job_search, "search_jobs", lambda *a, **k: SearchResult(jobs=spread))
    picks = daily_picks.build_picks(["Data Analyst"], "UK", _resume(), {}).jobs
    assert len(picks) == 10
    from collections import Counter
    assert max(Counter(sponsors.normalize_company(j.company) for j in picks).values()) <= 2


def test_register_unavailable_makes_no_picks(world, monkeypatch):
    monkeypatch.setattr(sponsors, "_register", lambda: None)
    built = daily_picks.build_picks(["Data Analyst"], "UK", _resume(), {})
    assert built.jobs == [] and not built.register_ok and any("register" in n for n in built.notices)


def test_state_helpers():
    assert daily_picks.parse_titles("Data Analyst, Data analyst\nBI Developer;  ;x" + "y" * 80)[:2] == ["Data Analyst", "BI Developer"]
    assert len(daily_picks.parse_titles("\n".join(f"t{i}" for i in range(9)))) == daily_picks.MAX_TITLES
    st_ = daily_picks.new_state(); st_["titles"] = ["a"]
    assert daily_picks.needs_refresh(st_, "2026-10-16")
    st_["day"] = "2026-10-16"
    assert not daily_picks.needs_refresh(st_, "2026-10-16") and daily_picks.needs_refresh(st_, "2026-10-17")


# ------------------------------------------------------------------- unit: apply pack
def test_tailoring_only_reorders_never_rewrites():
    r = _resume()
    tailored, note = apply_pack.tailor_resume(r, "We need SQL and Python reporting for finance dashboards.")
    assert tailored.skills[0].skills[:2] == ["SQL", "Python"]               # relevant skills first
    assert tailored.projects[0].name == "Sales dashboard"                    # relevant project first
    assert tailored.experience[0].bullet_points[0].startswith("Built SQL reports")
    # nothing added, removed or reworded
    assert sorted(tailored.skills[0].skills) == sorted(r.skills[0].skills)
    assert sorted(tailored.experience[0].bullet_points) == sorted(r.experience[0].bullet_points)
    assert {p.name for p in tailored.projects} == {p.name for p in r.projects}
    assert r.skills[0].skills == ["Photoshop", "SQL", "Python"]               # the original is untouched
    assert "first" in note


def test_autofill_fields():
    rows = dict(apply_pack.autofill_fields(_resume()))
    assert rows["Full name"] == "Ada Lovelace" and rows["First name"] == "Ada" and rows["Last name"] == "Lovelace"
    assert rows["Email"] == "ada@example.com" and rows["Current / latest employer"] == "Old Co"


# -------------------------------------------------------------------------- the page
def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _labels(at):
    return [b.label for b in at.button]


def _downloads(at):
    return [getattr(d.proto, "label", "") for d in at.get("download_button")]


def _links(at):
    return [getattr(d.proto, "label", "") for d in at.get("link_button")]


def test_daily_picks_page_end_to_end(fake, world, monkeypatch):
    at, uid = page_as_user(fake, PAGE)
    at.session_state["resume_data"] = _resume()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Save titles & find picks" in _labels(at) and _labels(at).count("Prepare application") == 0

    at.text_area[0].set_value("Data Analyst\nBusiness Analyst")
    _btn(at, "Save titles & find picks").click(); at.run(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _labels(at).count("Prepare application") == 3
    page_html = " ".join(m.value for m in at.markdown)
    for expected in ("Acme Ltd", "£45,000 - £55,000", "London, UK", "Visa sponsor", "Salary", "Sponsorship", "Location"):
        assert expected in page_html, expected
    assert "Nope Ltd" not in page_html and "Berlin" not in page_html
    # stored encrypted, one row
    assert len(fake.tables["daily_picks"]) == 1
    assert "Acme" not in str(fake.tables["daily_picks"]) and "Data Analyst" not in str(fake.tables["daily_picks"])

    # the first card's pack: tailored résumé + cover letter + a link to the employer's page
    _btn(at, "Prepare application").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Résumé PDF" in _downloads(at) and "Cover letter PDF" in _downloads(at) and "I've applied" in _labels(at)
    assert any("Apply now" in l for l in _links(at))
    assert any("Dear" in t.proto.default for t in at.text_area)

    # "I've applied" -> saved as applied, removed from today's list
    _btn(at, "I've applied").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(fake.tables["saved_jobs"]) == 1 and fake.tables["saved_jobs"][0]["status"] == "applied"
    # (the rebuilt page -- two cards left -- is checked in the fresh session below; AppTest keeps the
    #  pre-rerun tree after st.rerun(), so reading `at` again here would show a stale card)

    # a brand-new session the same day: remembers titles + list + what's done, doesn't search again
    searches = len(world)
    at2, _ = page_as_user(fake, PAGE, email="a@example.com") if False else (None, None)
    at3 = AppTest.from_file(PAGE, default_timeout=60)
    for key in ("auth", "_dek", "_profile"):
        at3.session_state[key] = at.session_state[key]
    at3.session_state["resume_data"] = _resume()
    at3.run()
    assert not at3.exception, [e.value for e in at3.exception]
    assert len(world) == searches
    assert [b.label for b in at3.button].count("Prepare application") == 2

    # the next day: a rebuilt list that skips everything already shown
    monkeypatch.setattr(daily_picks, "today", lambda: "2099-01-01")
    at3.run(); at3.run()
    assert len(world) > searches
    assert [b.label for b in at3.button].count("Prepare application") == 0     # all three were already shown
    assert "No matching UK jobs" in " ".join(e.value for e in at3.info)


def test_cover_letter_failure_still_gives_resume_and_details(fake, world, monkeypatch):
    from utils import ai_assistant
    def boom(*a, **k):
        raise ai_assistant.AIError("daily limit reached")
    monkeypatch.setattr(cl, "generate", boom)
    at, uid = page_as_user(fake, PAGE)
    at.session_state["resume_data"] = _resume(); at.run()
    at.text_area[0].set_value("Data Analyst")
    _btn(at, "Save titles & find picks").click(); at.run(); at.run()
    _btn(at, "Prepare application").click(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Résumé PDF" in _downloads(at) and "Cover letter PDF" not in _downloads(at)
    assert any("daily limit reached" in w.value for w in at.warning)
    # ...and once the AI is back, one click writes the letter into the same pack
    monkeypatch.setattr(cl, "generate", lambda resume, jd, company="", role="", api_key=None:
                        cl.CoverLetter(text="Dear team, hello.", company=company, role=role, word_count=3))
    _btn(at, "Try writing the cover letter again").click(); at.run(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Cover letter PDF" in _downloads(at) and "Try writing the cover letter again" not in _labels(at)


def test_page_needs_a_resume(fake, world):
    at, uid = page_as_user(fake, PAGE)
    at.run()
    at.text_area[0].set_value("Data Analyst")
    _btn(at, "Save titles & find picks").click(); at.run(); at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("résumé" in w.value.lower() for w in at.warning)
    assert world == []                                                       # nothing searched without one


# ---------------------------------------------------------------------- Jobs-page filter
def test_jobs_page_sponsorship_filter(fake, world):
    jobs = [J("Licensed Role", "Acme Ltd", "London", "https://acme.example/a"),
            J("Other Role", "Nope Ltd", "London", "https://nope.example/b"),
            J("Unknown Role", "", "London", "https://x.example/c", desc="We offer visa sponsorship for this role")]
    sponsors.annotate(jobs)
    assert [j.sponsor_licensed for j in jobs] == [True, False, None] and jobs[2].sponsorship_text == "yes"
    at, uid = page_as_user(fake, "pages/3_\U0001F4BC_Jobs.py")
    at.session_state["resume_data"] = _resume()
    at.session_state["job_results"] = SearchResult(jobs=jobs)
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    def titles():
        return [t for t in ("Licensed Role", "Other Role", "Unknown Role")
                if any(f'rb-job-title">{t}<' in m.value for m in at.markdown)]

    assert titles() == ["Licensed Role", "Other Role", "Unknown Role"]
    page = " ".join(m.value for m in at.markdown)
    assert "Visa sponsor" in page and "Sponsorship offered" in page        # the badges
    at.selectbox(key="job_sponsor").set_value("Visa sponsor (licensed)"); at.run()
    assert titles() == ["Licensed Role"]
    at.selectbox(key="job_sponsor").set_value("Not a licensed sponsor"); at.run()
    assert titles() == ["Other Role"]
    at.selectbox(key="job_sponsor").set_value("Any sponsorship"); at.run()
    assert len(titles()) == 3
