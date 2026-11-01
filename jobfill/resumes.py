"""Which resume an application uploads: a CV tailored to that posting, else the default one.

Tailored CVs live under ``documents/resume/`` (any subfolder, e.g. ``resume/tailored cv/``), one file
per posting, named after the company and the role: ``main_acme_labs_research_engineer_pre_training.pdf``.
A file is picked only when its name contains the company and shares more role words with the posting
than any other CV for that company. Anything less certain is listed as a candidate, never picked.
"""

from __future__ import annotations

from pathlib import Path

from .text import COMPANY_SUFFIXES, company_key, content_tokens, normalize

EXTENSIONS = {".pdf", ".docx", ".doc", ".rtf", ".odt", ".txt"}


def _tokens(text: str) -> list[str]:
    return normalize(text.replace("_", " ").replace("-", " ")).split()


def _company_span(company: str, words: list[str]) -> tuple[int, int] | None:
    """Where the company sits in a file name's words: 'Acme Labs' and 'AcmeLabs' both match
    acme_labs, and so does 'acme labs' split differently."""
    want = "".join(_tokens(company_key(company) or company))
    if not want:
        return None
    for i in range(len(words)):
        joined = ""
        for j in range(i, len(words)):
            joined += words[j]
            if joined == want:
                end = j + 1
                while end < len(words) and words[end] in COMPANY_SUFFIXES:   # acme_labs_... for "Acme Labs"
                    end += 1
                return i, end
            if len(joined) >= len(want):
                break
    return None


def tailored(folder: Path, company: str, role: str | None, *, exclude: Path | None = None) -> dict:
    """The CV in ``folder`` (searched recursively) made for this company and role.

    Returns ``{"file": Path | None, "candidates": [Path, ...]}``: ``file`` is set only for one clear match,
    ``candidates`` lists every CV that names the company (for the agent to offer when none was picked).
    """
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS) \
        if folder.is_dir() else []
    role_all = set(_tokens(role or ""))
    role_words = content_tokens(" ".join(role_all))
    scored = []
    for p in files:
        if exclude is not None and p.resolve() == Path(exclude).resolve():
            continue
        words = _tokens(p.stem)
        span = _company_span(company, words)
        # <company>_<role>: the next word starts the role, so "Delta" never takes delta_ai_... CVs.
        if span is None or (span[1] < len(words) and words[span[1]] not in role_all):
            continue
        rest = set(words[:span[0]] + words[span[1]:])
        scored.append((len(role_words & rest) / len(role_words) if role_words else 0.0, p))
    scored.sort(key=lambda s: -s[0])
    pick = None
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        pick = scored[0][1]
    return {"file": pick, "candidates": [p for _, p in scored]}


def shown(path: Path, root: Path) -> str:
    """How a path is stored: relative to the project folder when inside it (like documents.resume_path)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def choose(store, root: Path, company: str, role: str | None, explicit: str | None = None) -> dict:
    """The resume for a new application: ``explicit`` (--resume), else a tailored CV, else the default.

    ``file`` is what to store on the application (None = use ``documents.resume_path``).
    """
    root = Path(root)
    default = store.get_fact("documents.resume_path")
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else (root / p if (root / p).exists() else Path.cwd() / p)
        if not p.is_file():
            raise FileNotFoundError(f"resume file not found: {explicit}")
        return {"file": shown(p, root), "why": "given with --resume"}
    found = tailored(root / "documents" / "resume", company, role,
                     exclude=(root / default) if default else None)
    others = [shown(p, root) for p in found["candidates"]]
    if found["file"] is not None:
        return {"file": shown(found["file"], root), "why": "tailored CV whose name matches the company and role",
                **({"other_cvs_for_company": [o for o in others if o != shown(found["file"], root)]}
                   if len(others) > 1 else {})}
    out = {"file": None, "why": "default resume (documents.resume_path)", "default": default}
    if others:
        out["cvs_for_company"] = others
        out["note"] = ("CVs name this company but none clearly matches the role; set one with "
                       "`app update <ID> --resume <path>` if it fits.")
    return out
