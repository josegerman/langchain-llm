# =====================================
# About:
# =====================================
# This code focuses on reading a file and then chunking it and saving it into a local chroma vector database

# =====================================
# Required libraries:
# =====================================
#pip install langchain langchain_community langchain_chroma
#pip install langchain-openai

# =====================================
# Required imports
# =====================================
import os

from dotenv import load_dotenv
from langchain.text_splitter import CharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings

# ==================================
# Load .env variables
# ==================================
load_dotenv()

# =====================================
# Define the directory containing the text file and the persistent directory
# =====================================
current_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(current_dir, "books", "odyssey.txt") # <-- C:\_DEV\VSCode\Workspaces\lanchain-llm\rag\books\odyssey.txt
persistent_directory = os.path.join(current_dir, "db", "chroma_db")

# =====================================
# Check if the Chroma vector store already exists; If not create; If yes abort
# =====================================
if not os.path.exists(persistent_directory):
    print("Persistent directory does not exist. Initializing vector store...")

    # =====================================
    # Ensure the text file exists
    # =====================================
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"The file {file_path} does not exist. Please check the path."
        )

    # =====================================
    # Read the text content from the file
    # =====================================
    loader = TextLoader(file_path , encoding='utf8') # <-- Had to provide encoding to read text file
    documents = loader.load()

    # =====================================
    # Split the document into chunks
    # =====================================
    text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
    docs = text_splitter.split_documents(documents)

    # =====================================
    # Display information about the split documents
    # =====================================
    print("\n--- Document Chunks Information ---")
    print(f"Number of document chunks: {len(docs)}")
    print(f"Sample chunk:\n{docs[0].page_content}\n")

    # =====================================
    # Create embeddings
    # =====================================
    print("\n--- Creating embeddings ---")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")  # <-- Embeddings model; Update if needed
    print("\n--- Finished creating embeddings ---")

    # =====================================
    # Create the vector store and persist it automatically
    # =====================================
    print("\n--- Creating vector store ---")
    db = Chroma.from_documents(docs, embeddings, persist_directory=persistent_directory)
    print("\n--- Finished creating vector store ---")

else:
    print("Vector store already exists. No need to initialize.")

# =====================================
# Notes:
# =====================================
# Upgrade to Python 3.12.5
# When installing chroma I had to upgrade the MS C++ build tools