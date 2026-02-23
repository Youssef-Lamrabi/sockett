# Metagenomics NLP Extraction Pipeline

This package includes:
- Semantic classifier using NLP (Sentence Transformers)
- MAG count & assembly count extractor
- Tool extractor (60+ metagenomics tools)
- Integration with PubMed, Scopus, Dimensions
- Combined pipeline to produce Excel output

## Requirements
```
pip install crossrefapi pandas numpy scikit-learn sentence-transformers biopython requests beautifulsoup4
pip install pybliometrics   # if using Scopus
pip install openpyxl (#new)
```

## Usage
Run:
```
python run_pipeline.py
```

Edit `run_pipeline.py` to select PubMed, Scopus, or Dimensions.

Output is saved as:
```
deep_metagenomics_analysis.xlsx
```