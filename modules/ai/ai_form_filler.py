"""
AI-powered form field answerer using the Anthropic API.

answer_question() is called when the static config has no answer for a
required form field.  It sends the applicant's profile and the specific
job context to Claude and returns a ready-to-paste string.

Field type behaviour:
  select   — Claude picks exactly one option from the provided list (verbatim)
  number   — Claude returns only a number, no other text
  text / textarea — Claude keeps the answer to 3 sentences or fewer
"""

import anthropic

MODEL = "claude-sonnet-4-6"


def answer_question(
    question_label: str,
    field_type: str,
    options: list[str] | None,
    config: dict,
    job: dict,
) -> str:
    """
    Ask Claude to answer a single job-application form question.

    Returns the answer string, or "" if the API call fails or the key is missing.
    """
    api_key = (config.get("ai", {}).get("anthropic_api_key") or "").strip()
    if not api_key:
        return ""

    applicant = config.get("applicant", {})
    answers   = config.get("answers",   {})

    name       = applicant.get("name",  "the applicant")
    bio        = applicant.get("bio",   "")
    years_exp  = str(answers.get("years_experience", ""))
    job_title  = job.get("title",   "this role")
    company    = job.get("company", "this company")

    if field_type == "select" and options:
        options_block = "\n".join(f"  - {o}" for o in options)
        field_instruction = (
            f"Choose exactly one of the following options and return it verbatim "
            f"with no other text:\n{options_block}"
        )
    elif field_type == "number":
        field_instruction = "Return only a number with no other text or punctuation."
    else:
        field_instruction = "Answer in 3 sentences or fewer. Do not add a greeting or sign-off."

    prompt = f"""\
You are helping {name} fill out a job application for {job_title} at {company}.

Applicant profile:
  Name: {name}
  Years of experience: {years_exp}
  Bio: {bio}

Form question: {question_label}

{field_instruction}

Only answer the question. Do not explain, qualify, or add commentary."""

    try:
        client  = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception:
        return ""
