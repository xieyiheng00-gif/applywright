"""What the setup portal asks, page by page, worded the way application portals word it.

Every built-in fact sits on exactly one page (``tests/test_portal.py`` checks it, so a fact added to
``catalog/facts.py`` must be placed here too). Facts learned later (``fact define``) are added to the
page named by ``EXTRA_PAGE``, with a widget chosen from their type.

Required marks are not set here: a fact is required when the catalog says ``setup="required"`` and a
record block when ``store.RECORD_SETUP`` does, so a submitted portal is a complete setup. ``required``
on a record field applies to every entry the candidate adds.
"""

from __future__ import annotations

from ..catalog import STORY_THEMES


def Q(key, label, help=None, *, widget=None, placeholder=None, wide=False, none_label=None, default_from=None):
    """A fact. ``none_label``: a list fact whose empty list is an answer ("no relatives anywhere").
    ``default_from``: on submit, a blank answer takes this fact's value (login email = contact email)."""
    return {"type": "fact", "key": key, "label": label, "help": help, "widget": widget,
            "placeholder": placeholder, "wide": wide, "none_label": none_label, "default_from": default_from}


def F(name, label, widget="text", *, required=False, options=None, help=None, placeholder=None, wide=False):
    """A field of a record block. ``options``: a choice set name or ``[(value, label), ...]``."""
    return {"name": name, "label": label, "widget": widget, "required": required, "options": options,
            "help": help, "placeholder": placeholder, "wide": wide}


def R(kind, title, entry, fields, *, help=None, summary=None):
    """A repeating block (education, work experience...): one card per record.
    ``summary``: the fields that name an entry on the review page."""
    return {"type": "records", "record": kind, "title": title, "entry": entry, "fields": fields, "help": help,
            "summary": summary or [fields[0]["name"]]}


MONTH_YEAR = "MM/YYYY"

PAGES = [
    {"id": "info", "title": "My Information", "sections": [
        {"title": "Legal Name", "items": [
            Q("identity.first_name", "First Name"),
            Q("identity.last_name", "Last Name"),
            Q("identity.middle_name", "Middle Name"),
            Q("identity.preferred_name", "Preferred First Name",
              "Only if you go by a different first name, e.g. Alex for Alexander."),
            Q("identity.pronouns", "Pronouns", "Optional. Filled in only where a form asks."),
            Q("identity.date_of_birth", "Date of Birth",
              "Only some forms outside the US ask. Leave empty if you prefer.", placeholder="MM/DD/YYYY"),
        ]},
        {"title": "Contact Information", "items": [
            Q("contact.email", "Email Address", "The address employers should use."),
            Q("contact.phone", "Phone Number"),
            Q("contact.phone_country_code", "Country Phone Code", placeholder="+1"),
            Q("contact.phone_type", "Phone Device Type"),
        ]},
        {"title": "Address", "items": [
            Q("address.line1", "Address Line 1"),
            Q("address.line2", "Address Line 2", placeholder="Apartment, suite, unit"),
            Q("address.city", "City"),
            Q("address.state", "State / Province", placeholder="e.g. WI or Wisconsin"),
            Q("address.postal_code", "Postal Code"),
            Q("address.country", "Country"),
        ]},
        {"title": "Websites", "items": [
            Q("contact.linkedin", "LinkedIn Profile", placeholder="https://www.linkedin.com/in/..."),
            Q("contact.github", "GitHub", placeholder="https://github.com/..."),
            Q("contact.website", "Portfolio / Personal Website"),
            Q("contact.twitter", "X (Twitter)"),
        ]},
        {"title": "Job Portal Account", "items": [
            Q("account.login_email", "Email for Job-Portal Accounts",
              "Workday and some company sites need an account; jobfill signs up with this address. Leave empty "
              "to use your contact email. Some sites email a verification code, so use an inbox you can read.",
              default_from="contact.email", wide=True),
        ]},
    ]},

    {"id": "auth", "title": "Work Authorization", "sections": [
        {"title": "Work Authorization", "intro":
            "These answers decide every visa and sponsorship question on every form, so they must be exact.",
         "items": [
            Q("work_auth.primary_country", "Which country are you mainly applying for jobs in?", wide=True),
            Q("work_auth.authorized_countries", "In which countries are you legally authorized to work?",
              "Separate with commas. Only countries where you need no new visa.", wide=True),
            Q("work_auth.citizenship", "Citizenship(s)", "Separate with commas."),
            Q("work_auth.us_status", "Current U.S. Work Authorization Status", "Only if you apply in the United States."),
            Q("work_auth.requires_sponsorship_now",
              "Will you require visa sponsorship to start working in your primary country?",
              "Yes means you cannot start unless the employer files a visa for you."),
            Q("work_auth.requires_sponsorship_future",
              "Will you now or in the future require sponsorship for employment visa status (e.g. H-1B)?",
              "Yes if you will need sponsorship at any point, for example after OPT ends."),
            Q("work_auth.permit_expiry", "When does your current work authorization end?",
              "Leave empty if it does not expire.", placeholder=MONTH_YEAR),
            Q("work_auth.visa_detail", "Describe your work authorization in one or two sentences",
              placeholder="e.g. F-1 OPT through 06/2028, eligible for the 24-month STEM extension"),
        ]},
        {"title": "Security Clearance", "items": [
            Q("work_auth.security_clearance", "Do you hold an active security clearance?"),
            Q("work_auth.clearance_eligible", "Would you be able and willing to obtain a security clearance?"),
        ]},
    ]},

    {"id": "experience", "title": "Experience", "sections": [
        {"title": "Work Experience", "items": [
            R("experience", "Work Experience", "Work Experience", [
                F("title", "Job Title", required=True),
                F("company", "Company", required=True),
                F("employment_type", "Employment Type", "select", options="employment_type"),
                F("location", "Location", placeholder="City, State/Country"),
                F("start_date", "From", "date", required=True, placeholder=MONTH_YEAR),
                F("end_date", "To", "date", placeholder=MONTH_YEAR, help="Leave empty if you work here now."),
                F("current", "I currently work here", "checkbox"),
                F("summary", "Role Description", "textarea", help="One factual sentence about the role."),
                F("highlights", "Achievements", "lines",
                  help="One per line. Use numbers only where they are real."),
                F("skills", "Skills Used", "list", placeholder="Python, SQL, Airflow"),
                F("supervisor", "Manager's Name"),
                F("may_contact", "May we contact this employer?", "yesno"),
                F("reason_for_leaving", "Reason for Leaving"),
            ], help="Internships count. Most recent first is fine; jobfill orders them by date.",
              summary=["title", "company", "start_date", "end_date"]),
        ]},
        {"title": "Projects", "items": [
            R("project", "Projects", "Project", [
                F("name", "Project Name", required=True, placeholder="A descriptive name, not the employer's"),
                F("role", "Your Role", help="As it really was."),
                F("ownership", "Ownership", "select",
                  options=[("sole", "Sole developer"), ("lead", "Lead"), ("contributor", "Contributor")]),
                F("is_coursework", "This was a class project", "checkbox"),
                F("start_date", "From", "date", placeholder=MONTH_YEAR),
                F("end_date", "To", "date", placeholder=MONTH_YEAR),
                F("summary", "One-Sentence Summary", wide=True),
                F("description", "Description", "textarea",
                  help="100-150 words: what it does, the hardest problem, the outcome."),
                F("highlights", "Highlights", "lines", help="One per line."),
                F("tech", "Technologies", "list", placeholder="PyTorch, Kubernetes"),
                F("metrics", "Measured Outcomes", help="Only results you measured, never estimates."),
                F("url", "Link", "url", placeholder="Repository or demo"),
            ], summary=["name", "role"]),
        ]},
        {"title": "References", "items": [
            R("reference", "References", "Reference", [
                F("name", "Full Name", required=True),
                F("title", "Job Title"),
                F("company", "Company"),
                F("relationship", "Relationship", placeholder="e.g. Former manager"),
                F("email", "Email", "email"),
                F("phone", "Phone", "tel"),
                F("years_known", "Years Known", "number"),
            ], help="Only some forms ask for references.", summary=["name", "relationship"]),
        ]},
    ]},

    {"id": "education", "title": "Education", "sections": [
        {"title": "Education", "items": [
            R("education", "Education", "Education", [
                F("school", "School or University", required=True, help="The official name, as on your diploma."),
                F("degree", "Degree", required=True, placeholder="e.g. Master of Science"),
                F("degree_level", "Degree Level", "select", required=True, options="degree_level"),
                F("field_of_study", "Field of Study", required=True),
                F("minor", "Minor"),
                F("location", "Location", placeholder="City, State/Country"),
                F("start_date", "From", "date", placeholder=MONTH_YEAR),
                F("end_date", "To (actual or expected)", "date", required=True, placeholder=MONTH_YEAR),
                F("expected", "I am still studying here", "checkbox"),
                F("gpa", "GPA", placeholder="e.g. 3.8 or 9.1"),
                F("gpa_scale", "GPA Scale", placeholder="4.0"),
                F("gpa_4", "GPA on a 4.0 Scale",
                  help="Only if your school uses another scale: the 4.0 GPA forms should show. "
                       "jobfill never converts scales itself."),
                F("honors", "Honors"),
                F("coursework", "Relevant Coursework", "list", placeholder="Distributed Systems, Machine Learning"),
                F("thesis", "Thesis Title"),
            ], summary=["degree", "field_of_study", "school", "end_date"]),
        ]},
        {"title": "Certifications", "items": [
            R("certification", "Certifications", "Certification", [
                F("name", "Certification", required=True),
                F("issuer", "Issuing Organization"),
                F("date", "Issue Date", "date", placeholder=MONTH_YEAR),
                F("expires", "Expiration Date", "date", placeholder=MONTH_YEAR),
                F("credential_id", "Credential ID"),
                F("url", "Credential URL", "url"),
            ], summary=["name", "issuer"]),
        ]},
        {"title": "Awards", "items": [
            R("award", "Awards", "Award", [
                F("name", "Award", required=True),
                F("issuer", "Issuer"),
                F("date", "Date", "date", placeholder=MONTH_YEAR),
                F("description", "Description", wide=True),
            ], summary=["name", "issuer"]),
        ]},
        {"title": "Publications", "items": [
            R("publication", "Publications", "Publication", [
                F("title", "Title", required=True, wide=True),
                F("venue", "Journal / Conference"),
                F("date", "Date", "date", placeholder=MONTH_YEAR),
                F("authors", "Authors", wide=True),
                F("url", "Link", "url"),
            ], summary=["title", "venue"]),
        ]},
    ]},

    {"id": "skills", "title": "Skills & Documents", "sections": [
        {"title": "Skills", "items": [
            R("skill", "Skills", "Skill", [
                F("name", "Skill", required=True),
                F("years", "Years of Experience", "number"),
                F("level", "Proficiency", "select", options="skill_level"),
                F("category", "Category", "select", options=[
                    ("language", "Programming language"), ("framework", "Framework / library"),
                    ("tool", "Tool / platform"), ("domain", "Domain knowledge"), ("soft", "Soft skill")]),
                F("aliases", "Other Names", "list", placeholder="e.g. py, k8s"),
                F("last_used", "Last Used", placeholder="e.g. 2025 or current"),
            ], help="Also add common skills from your target postings that you do NOT have, with proficiency "
                    "None, so forms get an honest No.", summary=["name", "years", "level"]),
        ]},
        {"title": "Languages", "items": [
            R("language", "Languages", "Language", [
                F("name", "Language", required=True),
                F("proficiency", "Proficiency", "select", required=True, options="language_proficiency"),
            ], help="Every language you speak, English included. Forms answer No for a language not listed.",
              summary=["name", "proficiency"]),
        ]},
        {"title": "Resume & Documents", "items": [
            Q("documents.resume_path", "Resume/CV", "Uploaded by default. It is copied to documents/resume/ on "
              "this computer. Tailored CVs for single postings go in documents/resume/tailored cv/."),
            Q("documents.transcript_path", "Transcript", "An unofficial transcript is fine."),
            Q("documents.portfolio_path", "Portfolio / Work Sample"),
        ]},
    ]},

    {"id": "questions", "title": "Application Questions", "sections": [
        {"title": "Availability", "items": [
            Q("availability.earliest_start", "When is the earliest you can start?", placeholder="MM/DD/YYYY"),
            Q("availability.notice_period", "How much notice do you need to give?",
              placeholder="e.g. 2 weeks, Immediately"),
            Q("availability.internship_terms", "Internship / co-op terms you are available for",
              placeholder="e.g. Summer 2027, Fall 2027"),
            Q("availability.hours_per_week", "Hours per week you can work",
              "For part-time and internship forms."),
        ]},
        {"title": "Compensation", "items": [
            Q("comp.currency", "Salary Currency", placeholder="USD"),
            Q("comp.salary_min_annual", "Minimum acceptable annual base salary", placeholder="e.g. 115000"),
            Q("comp.salary_target_annual", "Desired annual base salary",
              "Used when a form wants one number.", placeholder="e.g. 130000"),
            Q("comp.hourly_min", "Minimum hourly rate", "For internships and contract roles."),
            Q("comp.salary_expectation_text", "What are your salary expectations?",
              "Used for free-text salary boxes.", placeholder="e.g. $120k-140k base, flexible for the right role"),
            Q("comp.current_salary", "Current annual salary",
              "Many US states ban this question. Leave empty to be asked each time."),
        ]},
        {"title": "Job Preferences", "items": [
            Q("prefs.desired_titles", "Desired job titles", "One per line.", widget="lines",
              placeholder="Software Engineer\nMachine Learning Engineer"),
            Q("prefs.employment_types", "Which employment types are you looking for?"),
            Q("prefs.work_arrangements", "Which work arrangements are you open to?"),
            Q("prefs.preferred_locations", "Preferred locations, best first",
              "One per line. Used to pick office-location dropdowns.", widget="lines",
              placeholder="San Francisco, CA\nSeattle, WA"),
            Q("prefs.willing_to_relocate", "Are you willing to relocate?"),
            Q("prefs.willing_to_travel", "Are you willing to travel for work?"),
            Q("prefs.max_travel_percent", "Maximum travel (% of time)", placeholder="0-100"),
            Q("source.how_heard_default", "How did you hear about us?",
              "Your default answer when an application does not say otherwise.", placeholder="e.g. LinkedIn"),
        ]},
        {"title": "Legal & Background", "items": [
            Q("legal.is_18_plus", "Are you at least 18 years of age?"),
            Q("legal.criminal_conviction", "Have you ever been convicted of a crime?",
              "Answer as a form would ask it. jobfill never guesses this one."),
            Q("legal.background_check_ok", "Are you willing to undergo a background check?"),
            Q("legal.drug_test_ok", "Are you willing to take a pre-employment drug test?"),
            Q("legal.drivers_license", "Do you hold a valid driver's license?"),
            Q("legal.non_compete", "Are you bound by a non-compete, non-solicit or similar agreement?"),
            Q("legal.essential_functions_ok",
              "Can you perform the essential functions of the job, with or without reasonable accommodation?"),
            Q("legal.government_official", "Are you a current or former government official or employee?"),
            Q("legal.relatives_at_companies", "Companies where a relative currently works",
              "Answers \"Do you have relatives at ...?\" questions.", placeholder="Separate with commas",
              none_label="None", wide=True),
        ]},
    ]},

    {"id": "about", "title": "About You", "intro":
        "Forms ask \"Tell us about yourself\", \"Why this company?\" and \"Tell us about a time...\". jobfill "
        "never pastes these answers: your agent writes each one fresh for every application from what you put "
        "here. Plain facts in your own words work better than polished prose.",
     "sections": [
        {"title": "Your Own Words", "items": [
            Q("profile.headline", "Professional Headline",
              placeholder="e.g. M.S. Computer Science student | ML systems and data infrastructure", wide=True),
            Q("profile.summary", "Tell us about yourself", "80-150 factual words."),
            Q("profile.motivations", "What motivates you, and what do you want in your next role?"),
            Q("profile.career_goals", "Where do you want your career to be in 3-5 years?"),
            Q("profile.strengths", "What are your top 2-3 strengths? Give an example for each."),
            Q("profile.weaknesses", "What is one area you are working to improve, and how?"),
            Q("profile.work_style", "Describe the way you like to work",
              "Team setup, pace, the kind of management you thrive under."),
            Q("profile.interests", "Interests outside work", "Hobbies or fun facts you are happy to share."),
            Q("profile.writing_style", "How should your written answers sound?",
              placeholder="e.g. Plain and direct. No buzzwords, no em dashes."),
            Q("profile.years_experience_override", "Total years of professional experience",
              "Leave empty to count it from your work history. Students: how many years internships and "
              "research should count for."),
        ]},
        {"title": "Tell Us About a Time...", "items": [
            R("story", "Stories", "Story", [
                F("title", "Short Title", required=True, placeholder="e.g. Learning Airflow in a week", wide=True),
                F("themes", "Themes", "checkboxes", options=[(t, t.capitalize()) for t in STORY_THEMES],
                  help="Behavioral questions pick stories by theme."),
                F("situation", "Situation", "textarea"),
                F("task", "Task", "textarea", help="What you had to do."),
                F("action", "Action", "textarea", help="What YOU did."),
                F("result", "Result", "textarea", help="The outcome, with real numbers where you have them."),
                F("source", "Where it happened", placeholder="e.g. Example Analytics internship"),
            ], help="Four to six true stories that together cover many themes: failure, conflict, leadership, "
                    "teamwork, pressure, learning... A sentence or two per part is enough.",
              summary=["title"]),
        ]},
    ]},

    {"id": "voluntary", "title": "Voluntary Disclosures", "sections": [
        {"title": "Voluntary Self-Identification", "intro":
            "Answering is voluntary. jobfill uses these answers only to fill the voluntary self-identification "
            "sections of applications and never to write anything. \"I don't wish to answer\" is a complete "
            "answer.",
         "items": [
            Q("eeo.gender", "Gender"),
            Q("eeo.hispanic_latino", "Are you Hispanic or Latino?"),
            Q("eeo.race", "Race / Ethnicity", "Select all that apply."),
            Q("eeo.veteran_status", "Veteran Status"),
            Q("eeo.disability_status", "Disability Status"),
            Q("eeo.sexual_orientation", "Sexual Orientation"),
            Q("eeo.transgender", "Do you identify as transgender?"),
            Q("eeo.lgbtq", "Do you identify as LGBTQ+?"),
            Q("eeo.first_generation", "Are you a first-generation college student?"),
        ]},
        {"title": "Consents", "intro": "You still review every application before it is submitted.", "items": [
            Q("consent.certify_accuracy",
              "May jobfill tick \"I certify that my answers are true and complete\" boxes for you?"),
            Q("consent.privacy_ack", "May jobfill tick \"I have read the privacy notice\" boxes for you?"),
            Q("consent.data_retention_ok", "May employers keep your application on file for future openings?"),
            Q("consent.sms_ok", "Do you agree to receive recruiting text messages?"),
            Q("consent.account_terms",
              "When jobfill creates a job-portal account for you, may it accept that site's account terms?"),
        ]},
    ]},
]

# Facts learned after this layout was written go to this page.
EXTRA_PAGE = "questions"

# How each canonical choice reads on the page (falls back to the choice set's labels, then the value).
CHOICE_LABELS = {
    "gender": {"male": "Male", "female": "Female", "non_binary": "Non-binary", "decline": "I don't wish to answer"},
    "race": {
        "american_indian_alaska_native": "American Indian or Alaska Native", "asian": "Asian",
        "black": "Black or African American", "hispanic_latino": "Hispanic or Latino",
        "native_hawaiian_pacific_islander": "Native Hawaiian or Other Pacific Islander", "white": "White",
        "middle_eastern_north_african": "Middle Eastern or North African", "two_or_more": "Two or More Races",
        "decline": "I don't wish to answer"},
    "hispanic_latino": {"yes": "Yes", "no": "No", "decline": "I don't wish to answer"},
    "veteran": {
        "not_veteran": "I am not a protected veteran",
        "protected_veteran": "I identify as one or more of the classifications of protected veteran",
        "veteran": "I am a veteran, but not a protected veteran", "decline": "I don't wish to answer"},
    "disability": {"yes": "Yes, I have a disability (or previously had a disability)",
                   "no": "No, I do not have a disability", "decline": "I don't wish to answer"},
    "yes_no_decline": {"yes": "Yes", "no": "No", "decline": "I don't wish to answer"},
    "sexual_orientation": {"heterosexual": "Heterosexual / straight", "other": "Prefer to self-describe",
                           "decline": "I don't wish to answer"},
    "pronouns": {"any": "Any pronouns", "use_name": "Use my name", "self_describe": "Self-describe",
                 "decline": "Prefer not to say"},
    "us_status": {
        "citizen": "U.S. citizen", "permanent_resident": "Permanent resident (green card)",
        "f1_stem_opt": "F-1 STEM OPT", "f1_opt": "F-1 OPT", "f1_cpt": "F-1 CPT",
        "f1_student": "F-1 student (no OPT/CPT yet)", "h1b": "H-1B", "h4_ead": "H-4 EAD", "l1": "L-1",
        "l2_ead": "L-2 EAD", "j1": "J-1", "tn": "TN", "o1": "O-1", "e3": "E-3", "asylee_refugee": "Asylee / refugee",
        "other_ead": "Other EAD", "other": "Other", "not_authorized": "Not authorized to work in the US"},
    "clearance": {"none": "None", "top_secret": "Top Secret", "ts_sci": "TS/SCI", "other": "Other (e.g. public trust)"},
    "degree_level": {"high_school": "High school", "some_college": "Some college, no degree", "associate": "Associate",
                     "bachelor": "Bachelor's", "master": "Master's", "mba": "MBA", "doctorate": "Doctorate (PhD)",
                     "professional": "Professional (JD, MD, ...)", "other": "Other"},
    "language_proficiency": {"native": "Native / bilingual", "fluent": "Fluent",
                             "professional": "Professional working", "conversational": "Conversational",
                             "basic": "Basic"},
    "skill_level": {"expert": "Expert", "advanced": "Advanced", "intermediate": "Intermediate",
                    "beginner": "Beginner", "none": "None (I don't have this skill)"},
    "employment_type": {"full_time": "Full-time", "part_time": "Part-time", "internship": "Internship / co-op",
                        "contract": "Contract", "temporary": "Temporary"},
    "work_arrangement": {"onsite": "Onsite", "hybrid": "Hybrid", "remote": "Remote"},
    "phone_type": {"mobile": "Mobile", "home": "Home", "work": "Work"},
}
