from pypdf import PdfReader
from langchain_core.documents import Document
import pickle
import os

BOOK_FOLDER = "../books"

documents = []

for filename in os.listdir(BOOK_FOLDER):

    if not filename.endswith(".pdf"):
        continue

    print(f"Reading {filename}")

    path = os.path.join(BOOK_FOLDER, filename)

    reader = PdfReader(path)

    for page_number, page in enumerate(reader.pages):

        text = page.extract_text()

        if text:

            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "book": filename,
                        "page": page_number + 1
                    }
                )
            )

print(f"\nLoaded {len(documents)} pages.")

with open("../data/documents.pkl", "wb") as f:
    pickle.dump(documents, f)

print("Saved documents.pkl")