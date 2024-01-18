from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import CharacterTextSplitter

import os
import requests
import chromadb
from chromadb.config import Settings
from tqdm import tqdm

import pandas as pd
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
import numpy as np
import matplotlib.pyplot as plt

class DiscourseInterface:
    def __init__(self, base_url):
        self.base_url = "https://root-forum.cern.ch/"
        self.headers = None #eventually, we can load the api key here

    def get_all_categories(self):
        """Retrieve all category names."""
        url = f"{self.base_url}/categories.json"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            categories = response.json().get('category_list', {}).get('categories', [])
            return {category['id']:category['name'] for category in categories}
        else:
            return f"Error: {response.status_code}"

    def get_topics_by_category(self, category_id, limit=None):
        """Retrieve topics from a specific category, with optional limit."""
        topics = []
        page = 0

        while True:
            url = f"{self.base_url}/c/{category_id}.json?page={page}"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                page_topics = response.json().get('topic_list', {}).get('topics', [])
                topics.extend(page_topics)

                if (limit is not None and len(topics) >= limit) or not page_topics:
                    # Break if we've reached the limit or there are no more topics
                    break

                page += 1
            else:
                print("ERR : failed to load page number ", page)
                page += 1

        # If a limit is specified, return up to that number of topics
        return {topic['id']:topic['title'] for topic in topics[:limit]}

    def get_full_posts_by_topic(self, topic_id):
        """Retrieve all posts from a specific topic."""
        url = f"{self.base_url}/t/{topic_id}.json"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            post_stream = response.json().get('post_stream', {})
            posts = post_stream.get('posts', [])
            metadata = {
                "closed": response.json()["closed"],
                "views": response.json()["views"],
                "topic_id": topic_id,
                "topic_title": response.json()["title"]
            }
            return posts, metadata
        else:
            return f"Error: {response.status_code}"

    def get_posts_by_topic(self, topic_id):
        posts, metadata = self.get_full_posts_by_topic(topic_id)

        filtered_posts = [{
            "id":post["id"],
            "username":post["username"],
            "text":post["cooked"],
        } for post in posts]

        return {"posts": filtered_posts, "metadata": metadata}

class SimpleVectorStore():

    def __init__(self, storage_path="./data/vstore", collection_name = "discourse_collection"):

        os.makedirs(storage_path, exist_ok=True)

        self.client = chromadb.PersistentClient(
                path=storage_path,
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
        )

        self.collection = self.client.get_or_create_collection(collection_name)

        self.text_splitter = CharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
        )

        self.embedding_model = HuggingFaceEmbeddings()

    def get_num_entries():

        return self.collection.count()


    def add_topics(self, topics):
        """
        topics is list of posts dictionaries 
        
        posts dictionaries are as outputed by `get_posts_by_topic` (above) which has keys "posts" and "metadata"

        this method adds the posts to the vstore
        """

        i = 0
        for topic in tqdm(topics):
            print(i)
            i+=1

            topic_text = ""
            for post in topic["posts"]:
                topic_text += "Author: " + post["username"] + "\n Text: " + post["text"] + "\n\n" 

            chunks = self.text_splitter.split_text(topic_text)

            # embed each chunk
            embeddings = self.embedding_model.embed_documents(chunks)

            topic_metadata = topic["metadata"]
            metadatas = [topic_metadata for _ in chunks]

            # making unique ids, ugh
            ids = [topic_metadata["topic_title"] + " (" + str(i) + ")" for i, _ in enumerate(chunks)]

            self.collection.add(embeddings=embeddings, ids=ids, documents=chunks, metadatas=metadatas)


    def visualize_vectorstore(self, n_groups = 5):

        df = pd.DataFrame(self.collection.get(include=['embeddings'])["embeddings"])

        print(df.shape)

        pca_50 = PCA(n_components=50)
        pca_result_50 = pca_50.fit_transform(df)

        tsne = TSNE(n_components=3, verbose=0, perplexity=40, n_iter=300)
        tsne_pca_results = tsne.fit_transform(pca_result_50)

        print(pca_result_50.shape)

        tsne = TSNE(n_components=2, verbose=0, perplexity=40, n_iter=300)
        tsne_pca_results = tsne.fit_transform(pca_result_50)

        print(tsne_pca_results.shape)

        pca_grouping = PCA(n_components=n_groups)
        pca_grouping_result = pca_grouping.fit_transform(df)
        groups = np.argmax(pca_grouping_result, axis=1)

        print(groups)

        plt.figure(figsize=(10, 6))
        scatter = plt.scatter(tsne_pca_results[:, 0], tsne_pca_results[:, 1], c=groups, cmap='viridis')
        plt.savefig("pca_tsne.png")




discourse = DiscourseInterface("https://root-forum.cern.ch/")
vector_store = SimpleVectorStore()

topic_names_ids = discourse.get_topics_by_category(category_id=6, limit=1000)
topics = []
for topic_id in tqdm(topic_names_ids.keys()):
    topics += [discourse.get_posts_by_topic(topic_id)]
#topics = [discourse.get_posts_by_topic(topic_id) for topic_id in topic_names_ids.keys()]
vector_store.add_topics(topics)
vector_store.visualize_vectorstore()