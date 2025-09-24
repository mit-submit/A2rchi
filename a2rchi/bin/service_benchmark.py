from a2rchi.chains.a2rchi import A2rchi
from a2rchi.utils.config_loader import load_config
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
from typing import Dict, List, Any

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
logger.setLevel(0)

os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")

class ResultHandler:
    results = {} 

    @staticmethod
    def map_prompts(config: Dict[str, Any]):
        pipe = config.get('a2rchi', {}).get('pipelines')[0]

        prompts = config.get('a2rchi',{}).get('pipeline_map').get(pipe).get('prompts')

        for _, prompts in prompts.items():
            for prompt_name, file_path in prompts.items():
                path  = Path(file_path)
                with open(path, "r") as f:
                    prompt_str = f.read()
                prompts[prompt_name] = prompt_str


    @staticmethod
    def handle_results(benchmark_name: str, config_path: Path, results: Dict, total_results: Dict):
        config_name = config_path.name
        with open(config_path, "r") as f: 
            config = yaml.load(f, Loader=yaml.FullLoader)

        ResultHandler.map_prompts(config)

        current_results = { f"{benchmark_name} - {config_name}" : { 
                       "single question results": results, 
                       "total_results": total_results, 
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
        self.previous_input_list = []
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

        # for now we just reset the datamanager entirely every time,
        # in the future we should add support for hot config swapping so 
        # documents dont need to be reinputted unnecessarily
        if self.data_manager:
            del self.data_manager
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()
        self.previous_input_list = current_input_list

        del self.chain
        self.config = config 
        self.benchmarking_configs = config['services']['benchmarking']

        # for now it only uses one pipeline (the first one) but maybe later we make this work for mulitple
        print(f"loaded new configuration: {self.current_config}")
        pipeline = config.get('a2rchi').get('pipelines')[0]

        self.chain = A2rchi(pipeline) 

    def get_link_results(self, result: Dict, link):
        res = {}
        sources = result['documents']


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

            if link_k not in links_generated: 
                links_generated += [link_k]

            if link == link_k: 
                match = True

        result_dict = {True: "LINK FOUND",
                       False: "NOT FOUND"
                       }
            
        res["correct_link"] = link
        res['links_returned'] = links_generated
        res['link_result'] = result_dict[match]

        return res, match
         
    def get_ragas_results(self, data, to_add):
        """WARNING: this method modifies the to_add dictionary to add the relevant scores to the relevant questions"""
        
        all_metrics_dict = {
                'answer_relevancy': answer_relevancy, 
                'faithfulness': faithfulness, 
                'context_precision': context_precision, 
                'context_recall': context_recall
                }

        enabled_metrics = self.benchmarking_configs['mode_settings']['ragas_settings']['enabled_metrics']

        metrics_dict = {k: v for k, v in all_metrics_dict.items() if k in enabled_metrics}
                       
        res = pd.DataFrame()

        # going one metric at a time prevents errors 
        for metric_name, metric in metrics_dict.items():
            evaluation_results = evaluate(data, metrics=[metric])
            metric_results = evaluation_results.to_pandas()
            res[metric_name] = metric_results[metric_name]

        for question_idx, question in enumerate(to_add.values()):
            for metric in metrics_dict.keys():
                question[metric] = res.at[question_idx, metric]

        return res

    def run(self):
        modes_being_run = set(self.benchmarking_configs['modes'])

        while self.all_config_files: 
            question_id = 0

            all_results = []

            link_accuracy = 0.0 

            # results for each questions
            question_wise_results = {}

            #results for all of the questions in this config
            total_results = {}

            for question, (link, reference_answer) in self.queries_to_answers.items(): 
                question_id +=1
                formatted_question = [("User", question)]
                start = time.perf_counter()
                result = self.chain(history=formatted_question)
                end = time.perf_counter()
                to_add = {}

                sources =  result['documents']
                contexts = [source.page_content for source in sources]


                if "RAGAS" in modes_being_run:
                    dataset_result = {
                            "question": question,
                            "contexts": contexts,
                            "answer": result['answer'],
                            "ground_truth": reference_answer,
                            }
                    all_results.append(dataset_result)

                to_add["question"] = question
                to_add["contexts"] = [str(source) for source in sources]
                to_add["chat_answer"] = result['answer']
                to_add["time_elapsed"] = end - start
                to_add['document_scores'] = result['documents_scores']
                to_add['ground_truth'] = reference_answer

                if "LINKS" in modes_being_run: 
                    link_info, match = self.get_link_results(result, link)
                    to_add.update(link_info)
                    if match: 
                        link_accuracy += 1.0

                question_wise_results[f"question_{question_id}"] = to_add
                print(f"Finished answering question: {question_id}")

            if "RAGAS" in modes_being_run:
                print(f"Starting to collect RAGAS results")
                data = Dataset.from_list(all_results)
                # were modifying final_addition here to add ragas results by question
                ragas_results = self.get_ragas_results(data, question_wise_results)

                answer_relevancy = ragas_results['answer_relevancy'].mean()
                faithfulness = ragas_results['faithfulness'].mean()
                context_precision = ragas_results['context_precision'].mean()
                context_recall = ragas_results['context_recall'].mean()

                
                total_results['aggregate_answer_relevancy'] = answer_relevancy
                total_results['aggregate_faithfulness'] = faithfulness
                total_results['aggregate_context_precision'] = context_precision
                total_results['aggregate_context_recall'] = context_recall

            if "LINKS" in modes_being_run:
                total_results['link_accuracy'] = link_accuracy / len(self.queries_to_answers)


            ResultHandler.handle_results(self.benchmark_name, Path(self.current_config), question_wise_results, total_results)
            self.load_new_configuration()

        ResultHandler.add_metadata()
        ResultHandler.dump(self.benchmark_name)
        return

if __name__ == "__main__":

    query_file = Path("QandA.txt") 
    configs_folder = Path('configs')

    question_to_answer = {}

    with open(Path(query_file), "r") as f:
        obj = json.load(f)

    for d in obj: 
        question_to_answer[d['question']] = (d['link'], d['answer']) 

    benchmarker = Benchmarker(configs_folder, question_to_answer)
    benchmarker.run()
    print("\n\nFINISHED RUNNING\n\n")
