import PyPDF2
import re

pdf_path = "./pdfs_batch1/10.1007_978-1-0716-5060-8_13.pdf"
reader = PyPDF2.PdfReader(open(pdf_path, "rb"))
text = []
for page in reader.pages:
    if page.extract_text():
        text.append(page.extract_text())
meta = reader.metadata or {}
print('META: \n', meta)
print('\nTITLE: \n', meta.get("/Title", "Unknown"))
print('\nPAGES: \n', len(reader.pages))
print('\nTEXT: \n', re.sub(r"\s+", " ", " ".join(text)))