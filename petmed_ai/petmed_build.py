
import logging
import os
import streamlit as st

from dotenv import load_dotenv
from pathlib import Path
from langchain.docstore.document import Document
from langchain_community.document_loaders import TextLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
from langchain_core.output_parsers import StrOutputParser
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
from langchain_openai import OpenAIEmbeddings
#from langchain_community.vectorstores import Chroma # <- deprecated
from langchain_chroma import Chroma
from time import sleep
from typing import List
#from langchain.memory import ChatMessageHistory # <-- deprecated
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langchain_community.llms import HuggingFaceHub
from langchain_community.chat_models.huggingface import ChatHuggingFace
from langchain import hub
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.messages.base import BaseMessage
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import StreamlitChatMessageHistory
from langchain_community.embeddings import OpenAIEmbeddings

# ==================================
# Load .env variables
# ==================================
load_dotenv()

EMBED_DELAY = 0.02

# ==================================
# Other AI models
# ==================================
MISTRAL_ID = "mistralai/Mistral-7B-Instruct-v0.1"
ZEPHYR_ID = "HuggingFaceH4/zephyr-7b-beta"

# ==================================
# Split documents into chunks
# ==================================
def split_documents(docs):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=0,
        length_function=len,
        is_separator_regex=False
    )
    contents = docs
    if docs and isinstance(docs[0], Document):
        contents = [doc.page_content for doc in docs]
    texts = text_splitter.create_documents(contents)
    n_chunks = len(texts)
    print(f"Split into {n_chunks} chunks")
    return texts

# ==================================
# Vector store and embeddings
# ==================================
class EmbeddingProxy:
        def __init__(self, embedding):
            self.embedding = embedding
        def embed_documents(self, texts: List[str]) -> List[List[float]]:
            sleep(EMBED_DELAY)
            return self.embedding.embed_documents(texts)
        def embed_query(self, text: str) -> List[float]:
            sleep(EMBED_DELAY)
            return self.embedding.embed_query(text)


def create_vector_db(texts, embeddings=None, collection_name="chroma"):
    if not texts:
        logging.warning("Empty texts passed in to create vector database")
    if not embeddings:
        openai_api_key=os.environ["OPENAI_API_KEY"]
        embeddings = OpenAIEmbeddings(openai_api_key=os.environ["OPENAI_API_KEY"], model="text-embedding-3-small")
    proxy_embeddings = EmbeddingProxy(embeddings)
    db = Chroma(collection_name=collection_name,
                embedding_function=proxy_embeddings,
                persist_directory=os.path.join("store/", collection_name))
    db.add_documents(texts)
    return db

# ==================================
# Ensemble retriever
# ==================================
def ensemble_retriever_from_docs(docs, embeddings=None):
    texts = split_documents(docs)
    vs = create_vector_db(texts, embeddings)
    vs_retriever = vs.as_retriever()
    bm25_retriever = BM25Retriever.from_texts([t.page_content for t in texts])
    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, vs_retriever],
        weights=[0.5,0.5]
    )
    return ensemble_retriever

# ==================================
# Load files
# ==================================
def list_txt_files(data_dir="petmed_ai\data"):
    paths = Path(data_dir).glob('**/*.csv')
    for path in paths:
        yield str(path)

def load_txt_files(data_dir="petmed_ai\data"):
    docs = []
    paths = list_txt_files(data_dir)
    for path in paths:
        print(f"Loading {path}")
        loader = CSVLoader(path) # changed to csvloader
        docs.extend(loader.load())
    return docs

# ==================================
# Build model
# ==================================
def get_model(repo_id=ZEPHYR_ID, **kwargs):
    if repo_id == "ChatGPT":
        chat_model = ChatOpenAI(model="gpt-4o", temperature=0, **kwargs) # added model
    else:
        huggingfacehub_api_token = kwargs.get("token", None)
        if not huggingfacehub_api_token:
            huggingfacehub_api_token = os.environ.get("token", None)
        os.environ["HF_TOKEN"] = huggingfacehub_api_token

        llm = HuggingFaceHub(
            repo_id=repo_id,
            task="text-generation",
            model_kwargs={
                "max_new_tokens": 512,
                "top_k": 30,
                "temperature": 0.1,
                "repetition_penalty": 1.03,
                "huggingfacehub_api_token": huggingfacehub_api_token,
            }
        )
        chat_model = ChatHuggingFace(llm=llm)
    return chat_model

# ==================================
# Create chain
# ==================================
def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

def get_question(input):
    if not input:
        return None
    elif isinstance(input,str):
        return input
    elif isinstance(input,dict) and 'question' in input:
        return input['question']
    elif isinstance(input,BaseMessage):
        return input.content
    else:
        raise Exception("String or dict with 'question' key expected as RAG chain input.")

def make_rag_chain(model, retriever, rag_prompt = None):
    if not rag_prompt:
        rag_prompt = hub.pull("vetincharge/vet")
    rag_chain = (
        {
            "context": RunnableLambda(get_question) | retriever | format_docs,
            "question": RunnablePassthrough()
        }
        | rag_prompt
        | model
    )
    return rag_chain

# ==================================
# Create memory chain
# ==================================
def create_memory_chain(llm, base_chain, chat_memory):
    contextualize_q_system_prompt = """Given a chat history and the latest user question \
        which might reference context in the chat history, formulate a standalone question \
            which can be understood without the chat history. Do NOT answer the question, \
                just reformulate it if needed and otherwise return it as is"""
    contextualize_q_promt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{question}"),
        ]
    )
    runnable = contextualize_q_promt | llm | base_chain
    def get_session_history(session_id: str) -> BaseChatMessageHistory:
        return chat_memory
    with_message_history = RunnableWithMessageHistory(
        runnable,
        get_session_history,
        input_messages_key="question",
        history_messages_key="chat_history",
    )
    return with_message_history

# ==================================
# Create full RAG chain
# ==================================
def create_full_chain(retriever, openai_api_key=None, chat_memory=ChatMessageHistory()):
    model = get_model("ChatGPT", openai_api_key=os.environ["OPENAI_API_KEY"])
    system_prompt = """You are a veterinarian for question-answering tasks. Use the following pieces of retrieved context to answer the question. If you don't know the answer, just say that you don't know. Use four sentences maximum and keep the answer concise.
    Context: {context}
    
    Question: """

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human"), "{question}"
        ]
    )
    rag_chain = make_rag_chain(model, retriever, rag_prompt=prompt)
    chain = create_memory_chain(model, rag_chain, chat_memory)
    return chain

def ask_question(chain, query):
    response = chain.invoke(
        {"question": query},
        config={"configurable": {"session_id": "foo"}}
    )
    return response

# ==================================
# Build streamlit app
# ==================================
st.set_page_config(page_title="PetMed AI Pet Diagnosis")
st.title("PetMed AI Pet Diagnosis")

def show_ui(qa, prompt_to_user="How may I help you?"):
    if "messages" not in st.session_state.keys():
        st.session_state.messages = [{"role": "assistant", "content": prompt_to_user}]
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    if prompt := st.chat_input():
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)
    if st.session_state.messages[-1]["role"] != "assistant":
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = ask_question(qa, prompt)
                st.markdown(response.content)
        message = {"role": "assistant", "content": response.content}
        st.session_state.messages.append(message)

@st.cache_resource
def get_retriever(openai_api_key=None):
    docs = load_txt_files()
    embeddings = OpenAIEmbeddings(openai_api_key=os.environ["OPENAI_API_KEY"], model="text-embedding-3-small")
    return ensemble_retriever_from_docs(docs, embeddings=embeddings)

def get_chain(openai_api_key=None):
    ensemble_retriever = get_retriever(openai_api_key=os.environ["OPENAI_API_KEY"])
    chain = create_full_chain(ensemble_retriever,
                              openai_api_key=os.environ["OPENAI_API_KEY"],
                              chat_memory=StreamlitChatMessageHistory(key="langchain_messages"))
    return chain

# ==================================
# Run streamlit app
# ==================================
def run():
    chain = get_chain(openai_api_key=os.environ["OPENAI_API_KEY"])
    st.subheader("How can I help you with your pet?")
    show_ui(chain, "Please describe your pet's symptoms.")

run()

# ==================================
# To execute:
# ==================================
# streamlit run C:\_DEV\VSCode\Workspaces\lanchain-llm\petmed_ai\petmed_build.py