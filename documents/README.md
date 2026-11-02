# documents/

Drop your career documents here, then run `/setup` and pick **Path A**. Everything in this folder
except this README is gitignored and stays on your machine.

```
documents/
├── resume/        Your most complete resume/CV (PDF, DOCX, TXT or LaTeX). Not a tailored variant.
├── linkedin/      LinkedIn "Save to PDF" export (Profile → More → Save to PDF).
├── transcripts/   Transcripts and diplomas: official degree names, dates, GPA.
├── projects/      Project write-ups, READMEs, portfolio pages: tech stack and real outcomes.
└── other/         Certificates, reference letters, anything else useful.
```

What setup extracts: contact details, education, experience, projects, skills, languages,
certifications and awards. It never infers voluntary EEO answers, visa or sponsorship status,
criminal history or salary from documents; it asks you directly.

Re-running setup after adding files is safe: unchanged information is skipped and anything that
conflicts with what is already stored is listed for you to keep or replace, together with every
other open question in one numbered message.

Point `documents.resume_path` at the PDF you want uploaded by default (setup asks).

Tailored CVs go in a subfolder such as `resume/tailored cv/`, one per posting, named after the company
and the role (`main_acme_labs_research_engineer_pre_training.pdf`). Setup does not read them;
`/apply` uploads the one whose name matches the posting, and the default resume otherwise.
