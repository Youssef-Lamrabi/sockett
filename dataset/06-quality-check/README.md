## LLM-as-a-Judge

This folder contains scripts and utilities for **quality assessment and filtering** of datasets used for fine-tuning (SFT).

## Data Types
We evaluate multiple dataset formats:
- **QA data** (forum-derived question–answer pairs)
- **Instruction data** (instruction / input / output triples)
- **Workflow data** (pipeline-oriented or procedural content)

All data originates from:
- Bioinformatics forums (raw Q/A then cleaned with LLM)
- Scientific papers (paragraph + evidence then instruction/input/output)


## Objective
Ensure high-quality training data by evaluating:

1. **Answer Correctness**: Is the answer supported by the source?
2. **Answer Completeness**: Does the answer fully address the question?
3. **Question Clarity**: Is the question well-formed and understandable?
4. **Faithfulness to Source**: Does the output avoid hallucinations and stay grounded?


## Approach
We use a hosted Ollama LLM as a *judge model* to automatically score each sample.

Each example is evaluated using:
- Input (question/instruction)
- Output (answer)
- Source (forum text or paper evidence)

The LLM returns structured scores for each metric.


## Pipeline Overview
1. Load dataset(s) (JSON / JSONL)
2. Normalize fields (question, answer, source)
3. Send evaluation prompt to LLM
4. Parse returned scores
5. Store results in output directory

## Output Format
Each sample is augmented with:

```json
"quality_scores": {
  "answer_correctness": 0-2,
  "answer_completeness": 0-2,
  "question_clarity": 0-2,
  "faithfulness": 0-2
}