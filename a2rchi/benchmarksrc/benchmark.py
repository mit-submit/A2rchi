from a2rchi.utils.config_loader import load_config
from a2rchi.chains.a2rchi import A2rchi
from a2rchi.utils.logging import get_logger
from a2rchi.utils.data_manager import DataManager
from a2rchi.utils.env import read_secret
from a2rchi.chains.utils.history_utils import stringify_history

from ragas import evaluate 
from ragas.metrics import answer_relevancy, faithfulness, context_precision, context_recall
from datasets import Dataset
from pathlib import Path
from datetime import datetime
from pprint import pprint

import pandas as pd
import os
import yaml
import json
import time


CONFIG_PATH = "/root/A2rchi/config.yaml"
OUTPUT_PATH = "/root/A2rchi/benchmarks"
EXTRA_METADATA_PATH = "/root/A2rchi/git_info.yaml"
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
        with open(config_path, "r") as f: 
            config = yaml.load(f, Loader=yaml.FullLoader)

        current_results = { f"{benchmark_name} - {config_name}" : { 
                       "results": results, 
                       "accuracy": accuracy,
                       "configuration used": config, 
                       }
                }

        ResultHandler.results.update(current_results)

    @staticmethod
    def process_ragas_results(benchmark_name: str, config_path: Path, results: dict, ragas_results: pd.DataFrame):
        config_name = config_path.name
        with open(config_path, "r") as f: 
            config = yaml.load(f, Loader=yaml.FullLoader)
        
        answer_relevancy = ragas_results['answer_relevancy'].mean()
        faithfulness = ragas_results['faithfulness'].mean()
        context_precision = ragas_results['context_precision'].mean()
        context_recall = ragas_results['context_recall'].mean()

        current_results = { f"{benchmark_name} - {config_name}" : { 
                       "results": results, 
                       "aggregate_answer_relevancy": answer_relevancy,
                       "aggregate_faithfulness": faithfulness,
                       "aggregate_context_precision": context_precision,
                       "aggregate_context_recall": context_recall,
                       "configuration used": config, 
                       }
                }

        ResultHandler.results.update(current_results)

    @staticmethod
    def add_metadata():
        with open(EXTRA_METADATA_PATH, "r") as f: 
            additional_info = yaml.safe_load(f)

        meta_data = {
            "time": str(datetime.now()),
            "git info": additional_info, 
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
        self.all_config_files = self.get_all_configs(configs)
        self.all_config_files.append('FINISHED')
        self.previous_input_list = None
        self.chain = None 
        self.config = None 
        self.data_manager = None 
        self.current_config = None 

        self.load_new_configuration()
        self.data_path = self.config["global"]["DATA_PATH"]

    def get_all_configs(self, configs_dir):
        all_paths = []
        for root, _, filenames in os.walk(configs_dir):
            for file in filenames: 
                full_path = os.path.join(root, file)
                all_paths.append(full_path)
        return all_paths

    def load_new_configuration(self):
        self.current_config = self.all_config_files.pop(0)
        if self.current_config == 'FINISHED': return
        with open(self.current_config, "r") as f:
            config = yaml.safe_load(f)
        current_input_list = config.get('data_manager', {}).get('input_lists', [])

        with open(CONFIG_PATH, 'w') as f: 
            yaml.dump(config, stream=f)

        if current_input_list != self.previous_input_list:
            del self.data_manager
            self.data_manager = DataManager()
            self.data_manager.update_vectorstore()
        self.previous_input_list = current_input_list

        del self.chain
        self.config = load_config()

        # for now it only uses one pipeline (the first one) but maybe later we make this work for mulitple
        print(f"loaded new configuration: {self.current_config}")
        pipeline = config.get('a2rchi').get('pipelines')[0]

        self.chain = A2rchi(pipeline) 

    def run_with_links(self):
        all_results = {}
        
        while self.all_config_files:
            accuracy = 0.0
            question_id = 0
            for question, (link, _) in self.queries_to_answers.items(): 
                question_id +=1

                dict_to_add = {}

                formatted_question = [("User", question)]
                start = time.perf_counter()
                result = self.chain(history=formatted_question)
                end = time.perf_counter()

                sources =  result['documents']

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
                dict_to_add["documents"] = [str(source) for source in sources] 
                dict_to_add["chat_history"] = chat_history
                dict_to_add["chat_answer"] = result['answer']
                dict_to_add['document_scores'] = result['documents_scores']
                dict_to_add["time_elapsed"] =  end - start

                dict_to_add['links_returned'] = links_generated
                dict_to_add['result'] = result_dict[match]
                all_results[f"question_{question_id}"] = dict_to_add


            accuracy = accuracy / question_id 

            #print("Results found: ")
            #pprint(all_results, indent = 4)
            print(f"current configs: {self.all_config_files}")
            ResultHandler.process_link_results(self.benchmark_name, Path(self.current_config), all_results, accuracy)
            
            self.load_new_configuration()
        ResultHandler.add_metadata()
        ResultHandler.dump(self.benchmark_name)



    def run_with_ragas(self):
        metrics_dict = {'answer_relevancy': answer_relevancy, 
                        'faithfulness': faithfulness, 
                        'context_precision': context_precision, 
                        'context_recall': context_recall
                        }
        while self.all_config_files:
            question_id = 0
            all_results = []
            to_add = {}

            for question, (_, reference_answer) in self.queries_to_answers.items(): 
                question_id +=1
                formatted_question = [("User", question)]
                start = time.perf_counter()
                result = self.chain(history=formatted_question)
                end = time.perf_counter()

                sources =  result['documents']
                contexts = [source.page_content for source in sources]


                result = {
                        "question": question,
                        "contexts": contexts,
                        "answer": result['answer'],
                        "ground_truth": reference_answer,
                        }
                all_results.append(result)
                result["time_elapsed"] = end - start
                to_add[f"question_{question_id}"] = result

            data = Dataset.from_list(all_results)

            res = pd.DataFrame()

            # going one metric at a time prevents errors 
            for metric_name, metric in metrics_dict.items():
                evaluation_results = evaluate(data, metrics=[metric])
                metric_results = evaluation_results.to_pandas()
                res[metric_name] = metric_results[metric_name]

            for question_idx, question in enumerate(to_add.values()):
                for metric in metrics_dict.keys():
                    question[metric] = res.at[question_idx, metric]

            ResultHandler.process_ragas_results(self.benchmark_name, Path(self.current_config), to_add, res)
            del data
            del res
            self.load_new_configuration()
        ResultHandler.dump(self.benchmark_name)

if __name__ == "__main__":
    query_file = Path("QandA.txt") 
    configs_folder = Path('configs')
    mode = os.environ['mode']

    queries = []
    sep = " : "
    question_to_answer = {}

    with open(query_file, "r") as f:
        for line in f:
            l = line.strip()
            working_list = l.split(sep)

            assert(len(working_list) == 3)

            # map the question to the proposed      link          &  relevant answer 
            question_to_answer[working_list[0]] = (working_list[1],  working_list[2])
    benchmarker = Benchmarker(configs_folder, question_to_answer)
    if mode == 'RAGAS':
        benchmarker.run_with_ragas() 
    else: 
        benchmarker.run_with_links() 
