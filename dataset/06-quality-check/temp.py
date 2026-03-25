def build_judge_prompt(sample):
    return f"""
You are a STRICT evaluation model for dataset quality.

You MUST ONLY use the provided SOURCE to judge the QUESTION and ANSWER (or instruction/output).
DO NOT use external knowledge.

Evaluate the following dimensions:

1. answer_correctness (0-2)
2. answer_completeness (0-2)
3. question_clarity (0-2)
4. faithfulness_to_source (0-2)

Scoring rules:
- 0 = bad
- 1 = partial
- 2 = good

Return STRICT JSON ONLY:

{{
  "answer_correctness": int,
  "answer_completeness": int,
  "question_clarity": int,
  "faithfulness": int,
  "reasoning": {{
    "correctness": "...",
    "completeness": "...",
    "clarity": "...",
    "faithfulness": "..."
  }}
}}

DATA:

QUESTION / INSTRUCTION:
{sample.get("question") or sample.get("instruction")}

ANSWER / OUTPUT:
{sample.get("answer") or sample.get("output")}

SOURCE:
{sample.get("source") or sample.get("evidence")}

Return ONLY JSON.
"""