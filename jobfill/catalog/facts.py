"""Fact schema: every single-valued thing jobfill can store about the candidate.

Tiers
-----
1  static     Rarely changes: identity, contact, work authorization, EEO,
              legal/background, standing consents. Collected once at setup.
2  evolving   Changes over months: compensation, availability, preferences,
              documents, and the narrative base material the AI tailors.
              ``review_days`` makes ``jobfill status`` flag stale values.
3  generated  Never stored as a fact. Per-application answers written by the
              agent (see ``questions.py``); they live in the answer log.

Repeating data (education, experience, projects, skills, languages, stories...)
lives in records, see ``records.py``.

``setup`` controls what ``jobfill missing`` asks for:
required     setup is not complete without it
recommended  asked during setup, can be skipped
optional     asked only when a form needs it

``sensitive`` facts are never inferred from documents or names. The agent must
ask the candidate directly, and "decline" is always a valid answer.
"""


def F(key, tier, type_, label, prompt, *, setup="optional", choices=None, multi=False,
      sensitive=False, review_days=None, notes=None):
    category = key.split(".", 1)[0]
    if choices is None and type_ == "bool":
        choices = "bool"
    return {
        "key": key, "tier": tier, "category": category, "type": type_, "label": label,
        "prompt": prompt, "setup": setup, "choices": choices, "multi": multi,
        "sensitive": sensitive, "review_days": review_days, "notes": notes,
    }


FACTS = [
    # ---------------------------------------------------------------- identity (tier 1)
    F("identity.first_name", 1, "string", "Legal first name", "What is your legal first name?",
      setup="required"),
    F("identity.middle_name", 1, "string", "Middle name", "Do you have a middle name? (leave empty if none)"),
    F("identity.last_name", 1, "string", "Legal last name", "What is your legal last name (surname)?",
      setup="required"),
    F("identity.preferred_name", 1, "string", "Preferred first name",
      "Do you go by a different first name than your legal one? (e.g. 'Alex' for Alexander)",
      setup="recommended"),
    F("identity.pronouns", 1, "enum", "Pronouns",
      "Optional: which pronouns should forms show? (he/him, she/her, they/them, he/they, she/they, xe/xem, "
      "ze/hir, any pronouns, use my name, self-describe, or decline)",
      choices="pronouns", multi=True, sensitive=True, setup="recommended"),
    F("identity.date_of_birth", 1, "date", "Date of birth",
      "Date of birth (any format). Only some non-US forms ask; skip if you prefer.", sensitive=True),

    # ---------------------------------------------------------------- contact (tier 1)
    F("contact.email", 1, "email", "Email", "Which email address should employers use?", setup="required"),
    F("contact.phone", 1, "phone", "Phone", "Your phone number, digits only or as you normally write it",
      setup="required"),
    F("contact.phone_country_code", 1, "string", "Phone country code", "Phone country code (e.g. +1)",
      setup="recommended"),
    F("contact.phone_type", 1, "enum", "Phone type", "Is that a mobile, home or work phone?",
      choices="phone_type"),
    F("contact.linkedin", 1, "url", "LinkedIn URL", "Your LinkedIn profile URL", setup="recommended"),
    F("contact.github", 1, "url", "GitHub URL", "Your GitHub profile URL (if you have one)"),
    F("contact.website", 1, "url", "Personal website / portfolio URL", "Personal website or portfolio URL"),
    F("contact.twitter", 1, "url", "X / Twitter URL", "X (Twitter) profile URL, if you want it on applications"),

    F("address.line1", 1, "string", "Street address", "Street address (line 1)", setup="recommended"),
    F("address.line2", 1, "string", "Address line 2", "Apartment / suite / unit (leave empty if none)"),
    F("address.city", 1, "string", "City", "City you live in", setup="required"),
    F("address.state", 1, "string", "State / province", "State or province (e.g. WI or Wisconsin)",
      setup="required"),
    F("address.postal_code", 1, "string", "Postal code", "ZIP / postal code", setup="required"),
    F("address.country", 1, "string", "Country of residence", "Country you live in", setup="required"),

    # ---------------------------------------------------------------- work authorization (tier 1)
    F("work_auth.primary_country", 1, "string", "Primary job-search country",
      "Which country are you mainly applying for jobs in?", setup="required"),
    F("work_auth.authorized_countries", 1, "list", "Countries authorized to work in",
      "In which countries are you currently legally allowed to work (without needing a new visa)?",
      setup="required"),
    F("work_auth.citizenship", 1, "list", "Citizenship(s)", "Which country or countries are you a citizen of?",
      setup="recommended"),
    F("work_auth.us_status", 1, "enum", "US immigration status",
      "If you are applying in the US: what is your current status? (citizen, permanent resident, "
      "F-1 OPT, F-1 STEM OPT, F-1 CPT, F-1 student, H-1B, H-4 EAD, L-1, L-2, J-1, TN, O-1, E-3, "
      "asylee/refugee, other EAD, not authorized)", choices="us_status", setup="recommended"),
    F("work_auth.visa_detail", 1, "text", "Work authorization details",
      "In one or two sentences, how would you explain your work authorization to an employer? "
      "(e.g. 'F-1 OPT through 2028-06, eligible for the 24-month STEM extension')"),
    F("work_auth.requires_sponsorship_now", 1, "bool", "Needs sponsorship now",
      "To START a job in your primary country, do you need the employer to sponsor a visa?",
      setup="required"),
    F("work_auth.requires_sponsorship_future", 1, "bool", "Needs sponsorship in future",
      "Will you need visa sponsorship at ANY point in the future (e.g. H-1B after OPT ends)?",
      setup="required"),
    F("work_auth.permit_expiry", 2, "date", "Work permit expiry",
      "When does your current work authorization end (e.g. June 2028)? Skip if it does not expire.",
      review_days=365),
    F("work_auth.security_clearance", 1, "enum", "Security clearance",
      "Do you hold an active security clearance? (none, confidential, secret, top secret, TS/SCI)",
      choices="clearance"),
    F("work_auth.clearance_eligible", 1, "bool", "Can obtain clearance",
      "Would you be able and willing to obtain a government security clearance?"),

    # ---------------------------------------------------------------- EEO self-identification (tier 1)
    F("eeo.gender", 1, "enum", "Gender",
      "Voluntary EEO question: gender (male, female, non-binary, or decline)?",
      choices="gender", sensitive=True, setup="required"),
    F("eeo.race", 1, "enum", "Race / ethnicity",
      "Voluntary EEO question: race/ethnicity (American Indian/Alaska Native, Asian (or East, South or "
      "Southeast Asian, for forms that ask), Black/African American, Hispanic/Latino, Native Hawaiian/Pacific "
      "Islander, White, Middle Eastern/North African, two or more, or decline)? You may pick several.",
      choices="race", multi=True, sensitive=True, setup="required"),
    F("eeo.hispanic_latino", 1, "enum", "Hispanic or Latino",
      "Voluntary EEO question: do you identify as Hispanic or Latino (yes, no, decline)?",
      choices="hispanic_latino", sensitive=True, setup="required"),
    F("eeo.veteran_status", 1, "enum", "Veteran status",
      "Voluntary EEO question: veteran status (not a veteran, protected veteran, veteran but not "
      "protected, or decline)?", choices="veteran", sensitive=True, setup="required"),
    F("eeo.disability_status", 1, "enum", "Disability status",
      "Voluntary EEO question: do you have a disability (yes, no, decline)?",
      choices="disability", sensitive=True, setup="required"),
    F("eeo.sexual_orientation", 1, "enum", "Sexual orientation",
      "Optional: sexual orientation, or decline?", choices="sexual_orientation", sensitive=True),
    F("eeo.transgender", 1, "enum", "Transgender", "Optional: do you identify as transgender (yes, no, decline)?",
      choices="yes_no_decline", sensitive=True),
    F("eeo.lgbtq", 1, "enum", "LGBTQ+", "Optional: do you identify as LGBTQ+ (yes, no, decline)?",
      choices="yes_no_decline", sensitive=True),
    F("eeo.first_generation", 1, "bool", "First-generation college student",
      "Are you a first-generation college student (first in your family to attend university)?"),

    # ---------------------------------------------------------------- legal / background (tier 1)
    F("legal.is_18_plus", 1, "bool", "18 or older", "Are you 18 years of age or older?", setup="required"),
    F("legal.criminal_conviction", 1, "bool", "Criminal conviction",
      "Have you ever been convicted of a crime that a form would ask about? Answer honestly; jobfill "
      "never guesses this one.", sensitive=True, setup="recommended"),
    F("legal.background_check_ok", 1, "bool", "Consents to background check",
      "Are you willing to undergo a standard background check?", setup="recommended"),
    F("legal.drug_test_ok", 1, "bool", "Consents to drug test", "Are you willing to take a pre-employment drug test?"),
    F("legal.drivers_license", 1, "bool", "Valid driver's license", "Do you hold a valid driver's license?"),
    F("legal.non_compete", 1, "bool", "Bound by non-compete",
      "Are you bound by a non-compete, non-solicit or similar agreement that could restrict a new job?",
      setup="recommended"),
    F("legal.relatives_at_companies", 1, "list", "Companies where relatives work",
      "List any companies where a relative currently works (empty list if none). Used for "
      "'Do you have relatives at X?' questions.", setup="recommended"),
    F("legal.essential_functions_ok", 1, "bool", "Can perform essential functions",
      "Can you perform the essential functions of the jobs you apply for, with or without reasonable "
      "accommodation?"),
    F("legal.government_official", 1, "bool", "Current/former government official",
      "Are you a current or former government official or government employee?"),

    # ---------------------------------------------------------------- consents (tier 1)
    F("consent.certify_accuracy", 1, "bool", "Auto-confirm accuracy attestations",
      "When a form asks you to certify that your application is true and complete, may jobfill tick "
      "that box for you? (You still review before submitting.)", setup="recommended"),
    F("consent.privacy_ack", 1, "bool", "Auto-acknowledge privacy notices",
      "May jobfill tick 'I have read the privacy notice' boxes for you?", setup="recommended"),
    F("consent.data_retention_ok", 1, "bool", "Keep data for future roles",
      "Are you happy for employers to keep your application on file for future openings?"),
    F("consent.sms_ok", 1, "bool", "Text message consent", "Do you agree to receive recruiting text messages?"),
    F("consent.account_terms", 1, "bool", "Accept portal account terms",
      "When jobfill creates a job-portal account for you (Workday, company career sites), may it tick the "
      "box accepting that site's account terms / privacy policy?"),

    # ---------------------------------------------------------------- job-portal accounts (tier 1)
    F("account.login_email", 1, "email", "Login email for job portals",
      "Which email should jobfill use to create and sign in to job-portal accounts? (Default: your contact "
      "email.) It must be an inbox you can read: some sites email a verification code.", setup="recommended"),

    # ---------------------------------------------------------------- compensation (tier 2)
    F("comp.currency", 2, "string", "Salary currency", "Currency for salary answers (e.g. USD)",
      setup="recommended", review_days=365),
    F("comp.salary_expectation_text", 2, "text", "Salary expectation (free text)",
      "How do you want to answer open 'salary expectations' boxes? (e.g. '$120k-140k base, flexible "
      "for the right role')", setup="recommended", review_days=90),
    F("comp.salary_min_annual", 2, "number", "Minimum annual salary",
      "Lowest annual base salary you would accept (number only)", setup="recommended", review_days=90),
    F("comp.salary_target_annual", 2, "number", "Target annual salary",
      "Annual base salary you will ask for when a form wants one number", review_days=90),
    F("comp.hourly_min", 2, "number", "Minimum hourly rate",
      "Minimum hourly rate for internships / contract roles (number only)", review_days=90),
    F("comp.current_salary", 2, "number", "Current salary",
      "Current annual salary (many US states ban this question; leave empty to always ask)",
      sensitive=True, review_days=180),

    # ---------------------------------------------------------------- availability (tier 2)
    F("availability.earliest_start", 2, "date", "Earliest start date",
      "Earliest date you could start a new job (e.g. June 1, 2027 or 6/2027)", setup="recommended", review_days=60),
    F("availability.notice_period", 2, "string", "Notice period",
      "How much notice do you need to give? (e.g. 'Immediately', '2 weeks')", setup="recommended",
      review_days=90),
    F("availability.internship_terms", 2, "list", "Internship terms available",
      "Which internship / co-op terms are you available for? (e.g. Summer 2027)", review_days=120),
    F("availability.hours_per_week", 2, "number", "Hours per week available",
      "How many hours per week can you work (for part-time / internship forms)?", review_days=120),

    # ---------------------------------------------------------------- preferences (tier 2)
    F("prefs.willing_to_relocate", 2, "bool", "Willing to relocate", "Are you willing to relocate for a job?",
      setup="recommended", review_days=180),
    F("prefs.preferred_locations", 2, "list", "Preferred locations (ordered)",
      "Which cities/regions do you prefer, best first? Used to pick office-location dropdowns.",
      setup="recommended", review_days=180),
    F("prefs.work_arrangements", 2, "enum", "Acceptable work arrangements",
      "Which work arrangements are you OK with: onsite, hybrid, remote? (pick all that apply)",
      choices="work_arrangement", multi=True, setup="recommended", review_days=180),
    F("prefs.willing_to_travel", 2, "bool", "Willing to travel", "Are you willing to travel for work?",
      review_days=180),
    F("prefs.max_travel_percent", 2, "number", "Max travel %", "Maximum share of time you would travel (0-100)",
      review_days=180),
    F("prefs.employment_types", 2, "enum", "Employment types sought",
      "Which employment types are you looking for: full-time, part-time, internship, contract, temporary?",
      choices="employment_type", multi=True, setup="recommended", review_days=180),
    F("prefs.desired_titles", 2, "list", "Desired job titles", "Which job titles are you targeting?",
      review_days=180),

    # ---------------------------------------------------------------- application defaults (tier 2)
    F("source.how_heard_default", 2, "string", "Default 'how did you hear about us'",
      "Default answer for 'How did you hear about us?' when an application does not say otherwise "
      "(e.g. LinkedIn, Company website)", setup="recommended", review_days=365),

    # ---------------------------------------------------------------- documents (tier 2)
    F("documents.resume_path", 2, "path", "Resume file", "Path to the resume PDF to upload by default",
      setup="recommended", review_days=90),
    F("documents.transcript_path", 2, "path", "Transcript file", "Path to your (unofficial) transcript PDF"),
    F("documents.portfolio_path", 2, "path", "Portfolio file", "Path to a portfolio / work-sample PDF"),

    # ---------------------------------------------------------------- narrative base material (tier 2)
    # Never pasted verbatim into a form: the agent reads these to write tier-3 answers.
    F("profile.headline", 2, "string", "Headline", "One-line professional headline (LinkedIn style)",
      setup="recommended", review_days=180),
    F("profile.summary", 2, "text", "Base self-introduction",
      "A factual 80-150 word 'about me' the AI tailors per application", setup="recommended",
      review_days=180),
    F("profile.motivations", 2, "text", "What motivates you",
      "What excites you about work and what you want in your next role? Feeds 'why this company' answers.",
      setup="recommended", review_days=180),
    F("profile.career_goals", 2, "text", "Career goals", "Where do you want your career to go in the next 3-5 years?",
      setup="recommended", review_days=180),
    F("profile.strengths", 2, "text", "Strengths", "Your top 2-3 strengths, each with a concrete example",
      review_days=365),
    F("profile.weaknesses", 2, "text", "Growth areas",
      "One honest growth area and what you are doing about it", review_days=365),
    F("profile.work_style", 2, "text", "Work style",
      "How do you like to work: team setup, pace, management style you thrive under?", review_days=365),
    F("profile.interests", 2, "text", "Interests outside work", "Hobbies or fun facts you are happy to share",
      review_days=365),
    F("profile.writing_style", 2, "text", "Writing style rules",
      "Any rules for how answers should sound? (e.g. 'plain, direct, no buzzwords, no em dashes')",
      review_days=365),
    F("profile.years_experience_override", 2, "number", "Years of experience (override)",
      "Total years of professional experience, if you want to override the value computed from records",
      review_days=365),
]
