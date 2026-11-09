# The setup portal

Fill in your profile like a job application instead of answering a questionnaire in chat.
Back to the [main README](../../README.md).

```bash
python -m jobfill portal
```

opens a local web form laid out like an application portal: My Information, Work Authorization,
Experience, Education, Skills & Documents (resume upload), Application Questions, About You, Voluntary
Disclosures and a Review page. Required questions are marked `*` (exactly the ones `jobfill status`
needs for setup to be complete) and are checked before you can continue or submit; each page saves
as you go (Save for Later keeps a draft), and every value goes through the same checks as
`jobfill fact set`. It shows what is already stored, so it also works for reviewing a profile built
from your documents (`/setup --portal` reads them first) or for changing it later. It serves on
127.0.0.1 only with a key in the link, saves to the database it prints (and shows on the page), stops
when you click Submit (`--keep-open` keeps it running), and an attached resume is copied to
`documents/resume/`. If it is stopped while a tab is open, the tab says so and keeps your answers: start
it again and save from the same tab.
