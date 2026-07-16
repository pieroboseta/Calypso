from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings


# Load the AI search database
embeddings = HuggingFaceEmbeddings(
    model_name="all-MiniLM-L6-v2"
)

db = FAISS.load_local(
    "../data/faiss",
    embeddings,
    allow_dangerous_deserialization=True
)

print("Apocalypso is ready.")
print("Type 'exit' to quit.\n")


while True:
    question = input("You: ")

    if question.lower() == "exit":
        break

    results = db.similarity_search(question, k=3)

    print("\n--- Relevant knowledge found ---\n")

    for i, result in enumerate(results):
        print(f"Source {i+1}:")
        print(result.page_content[:1000])
        print("\n-----------------------------\n")