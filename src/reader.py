from pypdf import PdfReader

pdf = "../books/survival.pdf"

reader = PdfReader(pdf)

text = ""

for page in reader.pages[:3]:
    text += page.extract_text()

print(text[:2000])