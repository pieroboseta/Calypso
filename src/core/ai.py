from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
import subprocess


# Load database

embeddings = HuggingFaceEmbeddings(
    model_name="all-MiniLM-L6-v2"
)


db = FAISS.load_local(
    "../data/faiss",
    embeddings,
    allow_dangerous_deserialization=True
)



# Memory

conversation = []


def ask_ai(question):

    global conversation


    # Search documents

    results = db.similarity_search(
        question,
        k=5
    )


    context = ""

    sources = []


    for result in results:

        context += result.page_content + "\n\n"


        sources.append(
            {
                "book": result.metadata.get(
                    "book",
                    "Unknown"
                ),

                "page": result.metadata.get(
                    "page",
                    "?"
                ),

                "pdf": "../books/" + result.metadata.get(
                    "book",
                    ""
                )
            }
        )



    history = ""

    for item in conversation[-6:]:

        history += (
            "User: "
            + item["user"]
            + "\nAI: "
            + item["ai"]
            + "\n\n"
        )



    prompt = f"""

You are Apocalypso.

You are an offline survival intelligence.

Your job is to help a person survive disasters,
collapse scenarios, and emergencies.

Rules:

- Use the provided survival library as your main source.
- Do NOT copy text directly from the books.
- Explain information naturally in your own words.
- Add useful reasoning and practical advice when appropriate.
- If information is missing, say you don't know.
- Continue conversations naturally.
- Remember previous messages.

Previous conversation:

{history}


Knowledge from survival library:

{context}


Current question:

{question}


Answer:

"""



    response = subprocess.run(
        [
            "ollama",
            "run",
            "qwen2.5:3b",
            prompt
        ],

        capture_output=True,

        text=True,

        encoding="utf-8",

        errors="replace"
    )


    answer = response.stdout.strip()



    conversation.append(
        {
            "user": question,
            "ai": answer
        }
    )



    return answer, sources