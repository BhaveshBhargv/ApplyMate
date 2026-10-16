import os, sys
from models.resume_data import ResumeData, ExperienceEntry, EducationEntry
from test_app_flow import page_as_user, fake  # noqa: F401


def test_dashboard_shows_entries_newest_first(fake):
    at, uid = page_as_user(fake, "pages/1_\U0001F9ED_Dashboard.py")
    r = ResumeData()
    r.experience = [ExperienceEntry(job_title="Old Job", start_date="Jan 2015", end_date="Dec 2016"),
                    ExperienceEntry(job_title="Current Job", start_date="Mar 2022", is_current=True),
                    ExperienceEntry(job_title="Middle Job", start_date="Feb 2017", end_date="Jun 2020")]
    r.education = [EducationEntry(degree="BSc", start_date="Sep 2010", end_date="Jun 2013"),
                   EducationEntry(degree="MSc", start_date="Sep 2013", end_date="Jun 2015")]
    at.session_state["resume_data"] = r
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert [e.job_title for e in at.session_state["resume_data"].experience] == ["Current Job", "Middle Job", "Old Job"]
    assert [e.degree for e in at.session_state["resume_data"].education] == ["MSc", "BSc"]
    # the preview sheet lists them in that order too
    sheet = " ".join(m.value for m in at.markdown)
    assert sheet.index("Current Job") < sheet.index("Middle Job") < sheet.index("Old Job")
    assert sheet.index("MSc") < sheet.index("BSc")
