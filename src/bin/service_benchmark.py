import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml
from datasets import Dataset
from langchain_anthropic import ChatAnthropic
from langchain_community.chat_models import ChatOllama
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import RunConfig, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (answer_relevancy, context_precision, context_recall,
                           faithfulness)

from src.a2rchi.a2rchi import A2rchi
from src.a2rchi.models import HuggingFaceOpenLLM
from src.data_manager.collectors.utils.index_utils import CatalogService
from src.data_manager.data_manager import DataManager
from src.utils.env import read_secret
from src.utils.logging import get_logger, setup_logging

CONFIG_PATH = "/root/A2rchi/config.yaml"
OUTPUT_PATH = "/root/A2rchi/benchmarks"
EXTRA_METADATA_PATH = "/root/A2rchi/git_info.yaml"
OUTPUT_DIR = Path(OUTPUT_PATH)

setup_logging()
logger = get_logger(__name__)

os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")


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

        current_results = { 
            f"{benchmark_name} - {config_name}" : { 
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
        filename = f"{benchmark_name}-{datetime.now().strftime('%y%m%d_%H%M%S')}.json"
        file_path = OUTPUT_DIR / filename
        logger.info(f"Dumping results to {file_path}")
        logger.debug(f"Full results: {ResultHandler.results}")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(file_path, "w") as f:
            json.dump(ResultHandler.results, f, indent=4)


class Benchmarker: 

    def __init__(self, configs: Path, q_to_a: dict[str, str]):
        self.queries_to_answers = q_to_a 
        self.required_fields = ['question']
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
        current_input_list = config.get('data_manager', {}).get('sources', {}).get('links', {}).get('input_lists', [])

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
        if 'SOURCES' in self.benchmarking_configs:
            self.required_fields += ['sources']
        elif 'RAGAS' in self.benchmarking_configs:
            self.required_fields += ['answer']

        # for now it only uses one pipeline (the first one) but maybe later we make this work for mulitple
        logger.info(f"loaded new configuration: {self.current_config}")
        pipeline = config.get('a2rchi').get('pipelines')[0]

        self.chain = A2rchi(pipeline) 


    def get_ragas_llm_evaluator(self):
        ragas_configs = self.config['services']['benchmarking']['mode_settings']['ragas_settings']
        provider = ragas_configs['provider']
        provider_settings = ragas_configs['evaluation_model_settings']

        model_name = provider_settings['model_name']


        match provider.lower():
            case "openai":
                return ChatOpenAI(model=model_name)
            case "ollama":
                base_url = provider_settings['base_url']
                return ChatOllama(model=model_name, base_url=base_url)
            case "huggingface":
                return HuggingFaceOpenLLM(base_model=model_name)
            case "anthropic":
                return ChatAnthropic(model=model_name)
            case _:
                return ChatOpenAI(model=model_name)

    def get_ragas_embedding_model(self):
        ragas_configs = self.config['services']['benchmarking']['mode_settings']['ragas_settings']
        embedding_model = ragas_configs['embedding_model']

        match embedding_model.lower():
            case "openai":
                return OpenAIEmbeddings()
            case "huggingface":
                return HuggingFaceEmbeddings()
            case _:
                return OpenAIEmbeddings()

    def get_source_results(
            self,
            result: Dict,
            reference_sources: List[str] | str,
            match_field: List[str] | str = 'display_name',
        ):
        """
        For each reference source, check the specified metadata field in the retrieved documents.
        The reference sources and match fields are paired one-to-one; a single string field is
        expanded to cover all provided sources. Returns summary information and whether all
        reference sources were found.
        """
        sources = result.get('documents', [])

        # Clean and prepare reference sources
        raw_references: List[str] = []
        if isinstance(reference_sources, str):
            cleaned = reference_sources.strip()
            if cleaned and cleaned != 'N/A':
                raw_references = [reference_sources]
        elif isinstance(reference_sources, list):
            raw_references = [ref for ref in reference_sources if ref not in (None, '')]
        elif reference_sources is None:
            raw_references = []
        else:
            raw_references = [reference_sources]
        reference_sources_list: List[str] = []
        for ref in raw_references:
            ref_str = str(ref).strip()
            if ref_str and ref_str != 'N/A':
                reference_sources_list.append(ref_str)

        # Prepare match fields
        if isinstance(match_field, str):
            match_fields_list = [match_field] if match_field else []
        elif match_field is None:
            match_fields_list = []
        else:
            match_fields_list = [field for field in match_field if field]
        if reference_sources_list:
            if not match_fields_list:
                match_fields_list = ['display_name'] * len(reference_sources_list)
            elif len(match_fields_list) == 1 and len(reference_sources_list) > 1:
                match_fields_list = match_fields_list * len(reference_sources_list)
            elif len(match_fields_list) != len(reference_sources_list):
                logger.error(
                    "Number of match fields (%s) does not align with number of reference sources (%s); reusing the last field for the remaining references.",
                    len(match_fields_list),
                    len(reference_sources_list),
                )
                raise ValueError("Mismatch between number of match fields and reference sources.")
        else:
            match_fields_list = []
        fields_to_capture: List[str] = []
        seen_fields = set()
        for field in match_fields_list:
            if field not in seen_fields:
                seen_fields.add(field)
                fields_to_capture.append(field)
        logger.debug("Fields to capture from metadata: %s", fields_to_capture)

        sources_returned: List[Dict[str, Any]] = []
        for document in sources:
            metadata = getattr(document, 'metadata', {}) or {}
            if fields_to_capture:
                record = {field: metadata.get(field) for field in fields_to_capture}
            else:
                record = dict(metadata)
            sources_returned.append(record)
        logger.debug("Sources returned: %s", sources_returned)

        matched_sources: List[str] = []
        for reference, field in zip(reference_sources_list, match_fields_list):
            logger.debug("Checking for reference source '%s' in field '%s'", reference, field)
            for document in sources:
                metadata = getattr(document, 'metadata', {}) or {}
                value = metadata.get(field)
                logger.debug("Checking against document metadata field '%s': %s", field, value)
                if value is None:
                    continue
                if isinstance(value, list):
                    values = [str(v).strip() for v in value if v is not None]
                else:
                    values = [str(value).strip()]
                if reference in values:
                    matched_sources.append(reference)
                    break

        match = bool(reference_sources_list) and len(matched_sources) == len(reference_sources_list)
        logger.debug("Source matching result: %s", match)

        result_dict = {
            True: 'SOURCE FOUND',
            False: 'SOURCE NOT FOUND'
        }

        res = {
            'correct_source': reference_sources,
            'sources_returned': sources_returned,
            'matched_reference_sources': matched_sources,
            'match_fields_used': match_fields_list,
            'source_result': result_dict[match],
        }

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

        ragas_settings = self.config['services']['benchmarking']['mode_settings']['ragas_settings']
        log_tenacity = self.config['global']['verbosity'] >= 4
        timeout = ragas_settings['timeout']
        batch_settings = ragas_settings['batch_size']
        if not batch_settings: 
            batch_settings = None
        
        runconfig = RunConfig(timeout=timeout, log_tenacity=log_tenacity)
        # going one metric at a time prevents errors 
        for metric_name, metric in metrics_dict.items():
            evaluation_results = evaluate(data, 
                                          metrics=[metric],
                                          llm=LangchainLLMWrapper(self.get_ragas_llm_evaluator()),
                                          embeddings=LangchainEmbeddingsWrapper(self.get_ragas_embedding_model()),
                                          run_config=runconfig,
                                          batch_size=batch_settings
                                          )

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

            source_accuracy = 0.0 

            # results for each questions
            question_wise_results = {}

            #results for all of the questions in this config
            total_results = {}

            for question_item in self.queries_to_answers:

                if type(question_item) is not dict:
                    logger.error(f"Each item in the question to answer list must be a dictionary, but got {type(question_item)}")
                    continue

                if not all(field in question_item for field in self.required_fields):
                    logger.error(f"Each item in the question to answer list must contain the following fields: {self.required_fields}, but got {question_item.keys()}")
                    continue

                question = question_item['question']
                reference_answer = question_item.get('answer', 'N/A')
                reference_sources = question_item.get('sources', 'N/A')

                question_id +=1
                formatted_question = [("User", question)]
                start = time.perf_counter()
                result = self.chain(history=formatted_question)
                end = time.perf_counter()
                to_add = {}

                sources =  result['documents']
                contexts = [s.page_content for s in sources]

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

                if "SOURCES" in modes_being_run: 
                    reference_sources_match_field = question_item.get('source_match_field', self.benchmarking_configs['mode_settings'].get('sources_settings', {'display_name'}).get('default_match_field', 'display_name'))
                    source_info, match = self.get_source_results(
                        result,
                        reference_sources,
                        match_field=reference_sources_match_field
                    )
                    to_add.update(source_info)
                    if match: 
                        source_accuracy += 1.0

                question_wise_results[f"question_{question_id}"] = to_add
                logger.info(f"Finished answering question: {question_id}")

            if "RAGAS" in modes_being_run:
                logger.info(f"Starting to collect RAGAS results")
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

            if "SOURCES" in modes_being_run:
                total_results['source_accuracy'] = source_accuracy / len(self.queries_to_answers)

            ResultHandler.handle_results(self.benchmark_name, Path(self.current_config), question_wise_results, total_results)
            self.load_new_configuration()

        ResultHandler.add_metadata()
        ResultHandler.dump(self.benchmark_name)
        return

if __name__ == "__main__":

    query_file = Path("QandA.txt") 
    configs_folder = Path('configs')

    with open(Path(query_file), "r") as f:
        question_to_answer = json.load(f)

    benchmarker = Benchmarker(configs_folder, question_to_answer)
    benchmarker.run()
    logger.info("\n\nFINISHED RUNNING\n\n")
