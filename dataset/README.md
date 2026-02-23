# MetaGeneAssistant — Dataset Specification

We want to build MetaGeneAssistant, a domain-specialized LLM designed to assist users in metagenomics workflows, including conceptual & factual Q/A, 
workflow design, data interpretation, troubleshooting, and pipeline automation. This README describes the dataset types required to train 
MetaGeneAssistant and were we are so far.


MetaGeneAssistant requires a **multi-component instruction and Q/A dataset** to achieve expert-level capabilities in metagenomics. This dataset must combine:

- **Factual knowledge**
- **Conceptual reasoning**
- **Workflow design**
- **Data interpretation**
- **Technical troubleshooting**
- **Practical scripts and procedures**

Unlike a simple Q/A dataset, MetaGeneAssistant must act as a domain expert assistant, therefore the training data must include Q/A, instructions real-world tasks, and expert workflow examples.

## 2. Dataset

| Dataset Type | Purpose | Format | Sources | Do we have it? | What do do now |
|--------------|---------|--------|------------------|------------------|------------------|
| **Factual Q/A** | Teach biological & metagenomics facts, definitions, tool capabilities | `{"question": "...", "answer": "..."}` | Research papers, tool documentation, textbooks | <span style="color: green">YES</span> | Select source & generate Q.A & human curation |
| **Conceptual Q/A** | Teach deeper understanding of processes & principles | Same as factual Q/A | Papers, reviews, metagenomics courses | <span style="color: green">YES</span> | Select source & generate Q.A & human curation |
| **Technical Troubleshooting Data** | Teach diagnostic reasoning for common failures | `{"instruction": "...", "input": "...", "output": "..."}` | Bioinformatics Q/A forums(we identified  7) | <span style="color: green">YES</span> | EXtend collection & Curate existing db(human) |
| **Expert Workflow Instructions** | Enable full pipeline generation & step-by-step planning | Instruction format | Expert forum posts, GitHub workflows, or Manua example and augemntedwith AI | <span style="color: blue">NO</span> | --- |
| **Data Interpretation Tasks** | Explain QC/assembly/binning/functional profiling outputs | `{"instruction": "...", "input": "QC/assembly report", "output": "interpretation..."}` | User-generated examples | <span style="color: red">NO</span> | --- |
| **Practical Tool Usage Examples** | Show CLI commands, scripts, config files | Instruction format with code outputs | Tool documentation, tutorials, GitHub repos | <span style="color: red">~</span> | --- |
| **Scenario-Based Reasoning** | Case-based reasoning for realistic computational scenarios | Multi-step instruction | Expert-written synthetic examples | <span style="color: red">~</span> | --- |
<!-- | **General Instruction Data** | Baseline assistant behavior (politeness, reasoning) | Standard instruction-tuning format | Public instruction datasets (Alpaca, Dolly, OASST) | <span style="color: red">NO</span> | -->

**Note**: Once we collect all papers and or metagenimics review we can actually use the script we tested with Mohamed and 
Fatima to generte the Q.A dataset. - Using the Q.A collected from Biostarts(and other forum) we can generate instrcution dataset  for Technical Troubleshooting. - Then next we should try to have evenif it is small a workflow intruction dataset(the 3rd one).

## 3. Dataset Structure

All dataset samples follow a JSONL structure.

**3.1 Q/A Format (Factual & Conceptual)**
```json
{
  "question": "What is the purpose of co-assembly in metagenomics?",
  "answer": "Co-assembly merges reads from multiple samples to improve genome bin recovery in low-abundance organisms..."
}
```

**3.2 Instruction Format (Troubleshooting, Workflows, Interpretation)**
```json
{
  "instruction": "Design a shotgun metagenomics workflow for soil samples.",
  "input": "",
  "output": "1. Perform quality control using FastQC...\n2. Trim adapters with Trimmomatic...\n3. Assemble using MEGAHIT...\n4. Bin using MetaBAT2..."
}
```
**3.3 Interpretation Example**
```json
{
  "instruction": "Interpret this MetaQUAST result.",
  "input": "N50: 1200, #Contigs: 250k, Largest: 10kb",
  "output": "The low N50 and high contig count indicate severe fragmentation likely due to uneven coverage or high diversity..."
}
```

This dataset aims to develop a model capable of: (1) answering expert-level biological and computational questions, 
(2) designing complete workflows for different sample types, 
(3) interpreting real analysis outputs, 
(4) diagnosing pipeline failures, 
(5) writing scripts & tool commands, 
(6) understanding general metagenomic logic: QC -> assembly -> binning -> refinement -> annotation.

Final dataset shoul be like:

```log
data/
 ├── qa_factual.jsonl
 ├── qa_conceptual.jsonl
 ├── instructions_troubleshooting.jsonl
 ├── instructions_workflows.jsonl
 ├── interpretations.jsonl
 ├── tool_usage_examples.jsonl
 └── scenarios_reasoning.jsonl
```

## 4. Training Strategy

1. Stage 1 — Q/A fine-tuning
2. Stage 2 — Instruction tuning

We finetune a whole selected model or use plug-train technic we saw before.

