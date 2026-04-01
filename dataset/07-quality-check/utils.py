# -----------------------------------------------
# PROMPT
# -----------------------------------------------
def base_rules():
    return """
You are a STRICT evaluator for dataset quality.

RULES:
- Use ONLY the provided SOURCE
- NO external knowledge
- If not supported treat as incorrect
- Be strict and critical
"""

# --- METRIC 1 ---
def question_faithfulness(sample, format_source):
    return f"""
{base_rules()}

TASK: Evaluate QUESTION FAITHFULNESS TO SOURCE

Goal:
Assess whether the rewritten QUESTION preserves the meaning of the original SOURCE question.
You are NOT evaluating clarity or quality.
You are ONLY checking whether the meaning is preserved.

Evaluation criteria:
- The QUESTION should retain all important details from the SOURCE
- It should not omit critical technical information
- It should not introduce new meaning or change intent

Scoring rules:
- 0 = meaning is incorrect, distorted, or major information is missing
- 1 = mostly consistent but some details are lost or simplified
- 2 = meaning fully preserved with no important loss

Return JSON:
{{"score": int, "reason": "..."}}

QUESTION (rewritten):
{sample['question']}

SOURCE QUESTION:
{format_source(sample['source'], include_keys=["initial_question", "pdf_evidence", "pdf_claim"])}
"""

# --- METRIC 2 ---
def question_clarity(sample, format_source):
    return f"""
{base_rules()}

TASK: Evaluate QUESTION CLARITY

Goal:
Assess whether the QUESTION is understandable, well-formed, and self-contained.

Evaluation criteria:
- The question should NOT depend on missing external context (example: prior conversation, unseen data, or implicit references).
- All necessary information should be present within the question itself.
- The intent and request should be clearly expressed.

Scoring rules:
- 0 = unclear, ambiguous, or missing critical context
- 1 = partially clear but incomplete or requires assumptions
- 2 = clear, specific, and fully self-contained

Return JSON:
{{"score": int, "reason": "..."}}

QUESTION:
{sample['question']}
"""

# --- METRIC 3 ---
def answer_faithfulness(sample, format_source):
    return f"""
{base_rules()}

TASK: Evaluate FAITHFULNESS TO SOURCE
Is the answer grounded in the SOURCE without introducing hallucination, unsupported or invented information.

Evaluation criteria:
- The ANSWER should not change the meaning of the SOURCE
- It should not introduce new concepts or distort intent
- It should not omit critical information

Scoring rules:
- 0 = hallucinated / unsupported claims / meaning is incorrect
- 1 = minor unsupported details or missing details
- 2 = fully grounded in SOURCE and the meaning is fully preserved and consistent with SOURCE

Return JSON:
{{"score": int, "reason": "..."}}

ANSWER:
{sample['answer']}

SOURCE:
{format_source(sample['source'], include_keys=["initial_answer", "pdf_evidence", "pdf_claim"])}
"""

# --- METRIC 4 ---
def answer_completeness(sample, format_source):
    return f"""
{base_rules()}

TASK: Evaluate ANSWER COMPLETENESS

Goal:
Assess whether the ANSWER fully addresses all parts of the QUESTION.

Evaluation criteria:
- You are NOT evaluating correctness.
- You are ONLY evaluating whether the answer is complete with respect to the question.
- The ANSWER should cover all key aspects of the QUESTION.
- The response should not ignore important sub-questions or constraints.

Scoring rules:
- 0 = does not answer question
- 1 = partially answers but misses important details or lacks explanation
- 2 = fully answers all aspects with sufficient detail or explanation

Return JSON:
{{"score": int, "reason": "..."}}

QUESTION:
{sample['question']}

ANSWER:
{sample['answer']}
"""
