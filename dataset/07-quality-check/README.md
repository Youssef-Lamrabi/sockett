## LLM-as-a-Judge

This folder contains scripts and utilities for **quality assessment and filtering** of datasets used for fine-tuning (SFT).

## Script version
In this benchmark max_worker=4
```bash
time python run_judge_v1.py 
Processing benchmark.jsonl: 100%|███████████████| 4/4 [01:29<00:00, 22.25s/it]
real    1m29.228s
user    0m0.224s
sys     0m0.024s
```
```bash
time python run_judge_v2_parallele.py 
Processing benchmark.jsonl: 100%|███████████████| 4/4 [01:07<00:00, 16.99s/it]
real    1m8.184s
user    0m0.229s
sys     0m0.038s
```
```bash
time python run_judge_v3_parallele.py 
Processing benchmark.jsonl: 100%|███████████████| 4/4 [01:07<00:00, 16.79s/it]
real    1m7.393s
user    0m0.228s
sys     0m0.025s
```

We go for parallele v3 then.

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
  "question_clarity": 0-2,
  "question_faithfulness": 0-2,
  "answer_completeness": 0-2,
  "answer_faithfulness": 0-2,
}
```

## Filtering

After that we will filter to have different quality dataset:
- db_v2
- db_v2_allminscore_1
- db_v2_allminscore_2


