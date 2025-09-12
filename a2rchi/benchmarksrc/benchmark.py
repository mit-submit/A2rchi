from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger
from a2rchi.chains.chain import Chain
from a2rchi.utils.data_manager import DataManager
from a2rchi.utils.env import read_secret
from a2rchi.chains.utils.history_utils import stringify_history

from ragas import evaluate 
from datasets import Dataset
from pathlib import Path
from datetime import datetime
from pprint import pprint

import pandas as pd
import shutil
import os
import sys
import yaml
import json
import time



CONFIG_PATH = "/root/A2rchi/config.yaml"
OUTPUT_PATH = "/root/A2rchi/benchmarks"
METADATA_PATH = "/root/A2rchi/METADATA.json"
OUTPUT_DIR = Path(OUTPUT_PATH)

logger = get_logger(__name__)

os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")

class ResultHandler:
    results = {} 

    @staticmethod
    def process_link_results(benchmark_name: str, config_path: Path, results: dict, accuracy: float):
        config_name = config_path.name
        config = yaml.load(str(config_path), Loader=yaml.FullLoader)

        current_results = { f"{benchmark_name} - {config_name}" : { 
                       "results": results, 
                       "accuracy": accuracy,
                       "configuration used": config, 
                       }
                }

        ResultHandler.results.update(current_results)

    @staticmethod
    def add_metadata():
        with open(METADATA_PATH, "r") as f: 
            additional_info = json.load(f)

        meta_data = {
            "time": str(datetime.now()),
            "additional info": additional_info , 
            }

        ResultHandler.results.update(meta_data)


    @staticmethod 
    def dump(benchmark_name: Path):
        filename = f"{benchmark_name}-{datetime.now()}.json"
        
        file_path = OUTPUT_DIR / filename
        with open(file_path, "w") as f:
            json.dump(ResultHandler.results, f, indent=4)



class Benchmarker: 
    def __init__(self, configs: Path, q_to_a: dict[str, str]):

        self.queries_to_answers = q_to_a 
        self.benchmark_name = os.environ['container_name']
        # self.all_config_files = self.get_all_configs(configs)
        self.previous_input_list = None
        self.chain = None 
        self.config = None 
        # self.current_config = self.all_config_files[0]

        # self.load_new_configuration()

        self.config = load_config()
        self.data_path = self.config["global"]["DATA_PATH"]
        self.temp_init_function()


    def get_all_configs(self, configs_dir):
        all_paths = []
        for root, _, filenames in os.walk(configs_dir):
            for file in filenames: 
                full_path = os.path.join(root, file)
                all_paths.append(full_path)
        return all_paths

    def temp_init_function(self):
        try: 
            self.data_manager = DataManager()
            self.data_manager.update_vectorstore()
            self.chain = Chain()
        except Exception as E: 
            print(f"exception in initializing: {E}")

#     def load_new_configuration(self):
#         with open(self.current_config, "r") as f:
#             current_input_list = yaml.load(f, Loader=yaml.FullLoader)['input_list']
# 
#         if current_input_list != self.previous_input_list:
#             self.data_manager = DataManager()
#             self.data_manager.update_vectorstore()
# 
#         del self.config
#         del self.chain 
# 
#         self.current_config = self.all_config_files.pop(0)
#         shutil.copyfile(self.current_config, CONFIG_PATH)
# 
# 
#         self.config = load_config(map = True)
#         self.chain = Chain() 

    def run_with_links(self):
        question_id = 0
        all_results = {}
        
        for question, (link, _) in self.queries_to_answers.items(): 
            question_id +=1

            dict_to_add = {}

            formatted_question = [("User", question)]
            start = time.perf_counter()
            result = self.chain(formatted_question)
            end = time.perf_counter()

            sources =  result['source_documents']

            chat_history = result.get('chat_history', "None")
            if chat_history != "None": chat_history = stringify_history(chat_history)

            with open(os.path.join(self.data_path, 'sources.yml'), 'r') as file:
                sources_to_links = yaml.load(file, Loader=yaml.FullLoader)
            
            num_sources = len(sources)

            match = False 
            links_generated = []
            for k in range(num_sources): 
                document = sources[k]
                document_source_hash = document.metadata['source']
                if '/' in document_source_hash and '.' in document_source_hash:
                    document_source_hash = document_source_hash.split('/')[-1].split('.')[0]
                link_k = sources_to_links.get(document_source_hash, "NO LINK FOUND")

                pass_flag = False
                if link_k not in links_generated: 
                    links_generated += [link_k]
                else: 
                    pass_flag = True
                if link == link_k: 
                    match = True
                    if not pass_flag: accuracy += 1

            result_dict = {True: "LINK FOUND",
                           False: "NOT FOUND"
                           }
                
            dict_to_add["question"] = question
            dict_to_add["correct_answer"] = link
            dict_to_add["sources"] = [str(source) for source in sources] 
            dict_to_add["chat_history"] = chat_history
            dict_to_add["chat_answer"] = result['answer']
            # dict_to_add['document_scores'] = result['document_scores']
            dict_to_add["time_elapsed"] =  end - start

            dict_to_add['links_returned'] = links_generated
            dict_to_add['result'] = result_dict[match]
            all_results[f"question_{question_id}"] = dict_to_add


        accuracy = accuracy / question_id 

        print("Results found: ")
        pprint(all_results, indent = 4)
        ResultHandler.process_link_results(self.benchmark_name, Path(CONFIG_PATH), all_results, accuracy)
        ResultHandler.dump(self.benchmark_name)
    
    def run_with_ragas(self):
        all_results = []
        
        for question, (_, reference_answer) in self.queries_to_answers.items(): 
            formatted_question = [("User", question)]
            result = self.chain(formatted_question)

            sources =  result['source_documents']
            contexts = [source.page_content for source in sources]

            result = {
                    "question": question,
                    "contexts": contexts,
                    "answer": result['answer'],
                    "ground_truth": reference_answer,
                    }
            all_results.append(result)

        data = Dataset.from_list(all_results)
        evaluation_results = evaluate(data)
        res = evaluation_results.to_pandas()

        print("Results found: ")
        print(res)
        res.to_csv("ragas_res.csv", index=False)
        # ResultHandler.process_ragas_results(self.benchmark_name, Path(CONFIG_PATH), all_results, accuracy)
        # ResultHandler.dump(self.benchmark_name)

if __name__ == "__main__":
    assert(len(sys.argv) > 1) 
    # configs_folder = sys.argv[1]
    query_file = sys.argv[1]

    queries = []
    sep = " _SEP_ "
    question_to_answer = {}

    with open(query_file, "r") as f:
        for line in f:
            l = line.strip()
            working_list = l.split(sep)

            assert(len(working_list) == 3)

            # map the question to the proposed      link          &  answer 
            question_to_answer[working_list[0]] = (working_list[1],  working_list[2])
    benchmarker = Benchmarker(None, question_to_answer)
    benchmarker.run_with_ragas() 
