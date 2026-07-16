import pickle

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Load the pages created by ingest.py
with open("../data/documents.pkl", "rb") as f:
    documents = pickle.load(f)

print(f"Loaded {len(documents)} pages.")

# Split pages into smaller chunks
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200
)

chunks = splitter.split_documents(documents)

print(f"Created {len(chunks)} chunks.")

# Create embeddings
embeddings = HuggingFaceEmbeddings(
    model_name="all-MiniLM-L6-v2"
)

print("Building FAISS database...")

db = FAISS.from_documents(chunks, embeddings)

db.save_local("../data/faiss")

print("Done!")