"""Choice sets: canonical answer values and the phrases forms use for them.

A fact stores ONE canonical value (e.g. ``"protected_veteran"``). When a form
offers options, ``jobfill.text.choose_option`` classifies every option against
the synonym lists below and picks the option that maps to the stored value.
Longer phrase matches win, so "I am not a protected veteran" maps to
``not_veteran`` even though it also contains "protected veteran".

Synonyms are normalized with ``jobfill.text.normalize`` at load time, so write
them in plain lowercase English; punctuation and hyphens do not matter.
"""

DECLINE = [
    "decline", "decline to self identify", "decline to answer", "decline to state",
    "decline to disclose", "prefer not", "prefer not to say", "prefer not to answer",
    "prefer not to disclose", "i dont wish to answer", "dont wish to answer",
    "do not wish", "dont wish", "do not want to answer", "dont want to answer",
    "choose not", "i choose not to disclose", "rather not", "not to disclose",
    "not declared", "not specified", "wish not to", "i do not wish to self identify",
    "i dont wish to self identify", "not to answer",
    # Longer than "protected veteran" in "I choose not to self-identify my protected veteran status" (Workable, OFCCP)
    "choose not to self identify", "i choose not to self identify", "not to self identify",
]

CHOICE_SETS: dict[str, dict] = {
    "bool": {
        "values": {
            "true": ["yes", "y", "true", "i am", "i do", "i will", "i have", "i can",
                     "i agree", "agree", "i accept", "accept", "i consent", "consent",
                     "correct", "i acknowledge", "acknowledge"],
            "false": ["no", "n", "false", "i am not", "i do not", "i dont", "i will not",
                      "i wont", "i have not", "i havent", "i cannot", "i cant",
                      "i do not agree", "i do not consent", "disagree"],
        },
    },
    "yes_no_decline": {
        "values": {
            "yes": ["yes", "i am", "i do", "i identify as"],
            "no": ["no", "i am not", "i do not", "i dont"],
            "decline": DECLINE,
        },
    },
    "gender": {
        "values": {
            "male": ["male", "man", "cisgender male", "cis male", "cisgender man"],
            "female": ["female", "woman", "cisgender female", "cis female", "cisgender woman"],
            "non_binary": ["non binary", "nonbinary", "genderqueer", "gender non conforming",
                           "non conforming", "gender nonconforming", "genderfluid"],
            "decline": DECLINE,
        },
    },
    "race": {
        # US EEO-1 categories (plus MENA, added by OMB SPD-15 in 2024).
        "values": {
            "american_indian_alaska_native": ["american indian", "alaska native",
                                              "alaskan native", "native american", "indigenous"],
            "asian": ["asian", "east asian", "south asian", "southeast asian"],
            "black": ["black", "african american"],
            "hispanic_latino": ["hispanic", "latino", "latina", "latinx", "latine"],
            "native_hawaiian_pacific_islander": ["native hawaiian", "pacific islander"],
            "white": ["white", "caucasian"],
            "middle_eastern_north_african": ["middle eastern", "north african", "mena"],
            "two_or_more": ["two or more", "two or more races", "multiracial", "multi racial",
                            "mixed race", "biracial"],
            "decline": DECLINE,
        },
        # "Asian (Not Hispanic or Latino)" must not count as a Hispanic match.
        "ignore": [r"\bnot hispanic or latino\b", r"\bnon hispanic\b"],
    },
    "hispanic_latino": {
        "values": {
            "yes": ["yes", "hispanic or latino", "hispanic", "latino", "latina", "latinx",
                    "i am hispanic or latino"],
            "no": ["no", "not hispanic or latino", "not hispanic", "non hispanic",
                   "i am not hispanic or latino"],
            "decline": DECLINE,
        },
    },
    "veteran": {
        "values": {
            "not_veteran": ["no", "not a veteran", "i am not a veteran", "not a protected veteran",
                            "i am not a protected veteran", "not protected veteran",
                            "i have not served"],
            "protected_veteran": ["yes", "protected veteran", "i am a protected veteran",
                                  "i identify as one or more",
                                  "identify as one or more of the classifications of protected veteran",
                                  "i identify as a protected veteran"],
            "veteran": ["i am a veteran", "veteran but not a protected veteran",
                        "i am a veteran but not a protected veteran",
                        "veteran not protected"],
            "decline": DECLINE,
        },
    },
    "disability": {
        "values": {
            "yes": ["yes", "i have a disability", "yes i have a disability",
                    "have a disability or have had one", "i have or have had a disability"],
            "no": ["no", "i do not have a disability", "i dont have a disability",
                   "no i do not have a disability", "no i dont have a disability"],
            "decline": DECLINE,
        },
    },
    "pronouns": {
        "values": {
            "he_him": ["he him", "he him his", "he"],
            "she_her": ["she her", "she her hers", "she"],
            "they_them": ["they them", "they them theirs", "they"],
            "he_they": ["he they", "he they them"],
            "she_they": ["she they", "she they them"],
            "xe_xem": ["xe xem", "xe xem xyr", "xe"],
            "ze_hir": ["ze hir", "ze zir", "ze hir hirs", "ze zir zirs", "ze"],
            "any": ["any pronouns", "all pronouns", "any all"],
            "use_name": ["use my name", "use my name only", "name only", "no pronouns", "just my name"],
            "self_describe": ["self describe", "prefer to self describe", "i prefer to self describe", "other",
                              "not listed", "another"],
            "decline": DECLINE,
        },
        # How the value reads in a free-text box.
        "labels": {"he_him": "he/him", "she_her": "she/her", "they_them": "they/them", "he_they": "he/they",
                   "she_they": "she/they", "xe_xem": "xe/xem", "ze_hir": "ze/hir", "any": "any pronouns",
                   "use_name": "please use my name", "self_describe": "self-describe",
                   "decline": "prefer not to say"},
    },
    "sexual_orientation": {
        "values": {
            "heterosexual": ["heterosexual", "straight"],
            "gay": ["gay"],
            "lesbian": ["lesbian"],
            "bisexual": ["bisexual"],
            "asexual": ["asexual"],
            "pansexual": ["pansexual"],
            "queer": ["queer"],
            "other": ["other", "prefer to self describe", "self describe"],
            "decline": DECLINE,
        },
    },
    "us_status": {
        "values": {
            "citizen": ["us citizen", "citizen", "united states citizen", "american citizen"],
            "permanent_resident": ["permanent resident", "green card", "green card holder",
                                   "lawful permanent resident", "lpr"],
            "f1_stem_opt": ["stem opt", "f 1 stem opt", "f1 stem opt", "stem opt extension"],
            "f1_opt": ["opt", "f 1 opt", "f1 opt", "optional practical training"],
            "f1_cpt": ["cpt", "f 1 cpt", "f1 cpt", "curricular practical training"],
            "f1_student": ["f 1", "f1", "student visa", "f 1 student"],
            "h1b": ["h 1b", "h1b", "h 1 b"],
            "h4_ead": ["h 4 ead", "h4 ead", "h 4", "h4"],
            "l1": ["l 1", "l1"],
            "l2_ead": ["l 2", "l2", "l 2 ead"],
            "j1": ["j 1", "j1"],
            "tn": ["tn", "tn visa"],
            "o1": ["o 1", "o1"],
            "e3": ["e 3", "e3"],
            "asylee_refugee": ["asylee", "refugee", "asylum"],
            "other_ead": ["ead", "employment authorization document"],
            "other": ["other"],
            "not_authorized": ["not authorized", "no work authorization", "none"],
        },
    },
    "clearance": {
        "values": {
            "none": ["none", "no clearance", "no", "not applicable", "n a"],
            "confidential": ["confidential"],
            "secret": ["secret"],
            "top_secret": ["top secret", "ts"],
            "ts_sci": ["ts sci", "top secret sci", "sci"],
            "other": ["other", "public trust"],
        },
    },
    "degree_level": {
        "values": {
            "high_school": ["high school", "secondary school", "ged", "high school diploma"],
            "some_college": ["some college", "some university", "some college no degree"],
            "associate": ["associate", "associates", "associate degree", "associates degree"],
            "bachelor": ["bachelor", "bachelors", "bachelors degree", "ba", "bs", "bsc", "beng",
                         "undergraduate", "undergraduate degree", "4 year degree",
                         "four year degree"],
            "master": ["master", "masters", "masters degree", "ms", "msc", "ma", "meng",
                       "graduate degree"],
            "mba": ["mba", "master of business administration"],
            "doctorate": ["phd", "ph d", "doctorate", "doctoral", "doctor of philosophy",
                          "doctoral degree"],
            "professional": ["jd", "md", "professional degree", "juris doctor",
                             "doctor of medicine", "pharmd", "dds"],
            "other": ["other"],
        },
    },
    "language_proficiency": {
        "values": {
            "native": ["native", "native speaker", "bilingual", "mother tongue",
                       "native or bilingual", "native bilingual"],
            "fluent": ["fluent", "full professional", "full professional proficiency",
                       "advanced", "c1", "c2", "expert"],
            "professional": ["professional", "professional working", "professional working proficiency",
                             "proficient", "business", "business fluent", "b2", "upper intermediate"],
            "conversational": ["conversational", "intermediate", "limited working",
                               "limited working proficiency", "b1"],
            "basic": ["basic", "elementary", "elementary proficiency", "beginner", "a1", "a2",
                      "novice"],
        },
    },
    "skill_level": {
        "values": {
            "expert": ["expert", "mastery", "5"],
            "advanced": ["advanced", "proficient", "strong", "4"],
            "intermediate": ["intermediate", "competent", "working knowledge", "3"],
            "beginner": ["beginner", "basic", "novice", "familiar", "entry level", "1", "2"],
            "none": ["none", "no experience", "not familiar", "0"],
        },
    },
    "employment_type": {
        "values": {
            "full_time": ["full time", "permanent", "fte", "full time employee"],
            "part_time": ["part time"],
            "internship": ["intern", "internship", "co op", "coop", "co operative"],
            "contract": ["contract", "contractor", "freelance", "consultant", "contract to hire"],
            "temporary": ["temporary", "temp", "seasonal"],
        },
    },
    "work_arrangement": {
        "values": {
            "remote": ["remote", "fully remote", "work from home", "wfh", "telecommute"],
            "hybrid": ["hybrid", "flexible"],
            "onsite": ["onsite", "on site", "in office", "in person", "office", "on premises"],
        },
    },
    "phone_type": {
        "values": {
            "mobile": ["mobile", "cell", "cellular", "cell phone", "mobile phone"],
            "home": ["home", "landline", "home phone"],
            "work": ["work", "office", "business", "work phone"],
        },
    },
}

# Degree levels ordered for "do you have a bachelor's degree or higher?" checks.
DEGREE_RANK = {
    "high_school": 1, "some_college": 1.5, "associate": 2, "bachelor": 3, "master": 4,
    "mba": 4, "professional": 5, "doctorate": 5, "other": 0,
}
