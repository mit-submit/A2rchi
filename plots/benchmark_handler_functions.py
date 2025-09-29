import json
import pandas as pd 
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from pathlib import Path

class display_functions:

    @staticmethod
    def plot_single_metric_histogram(df, metric_name):
        """Plot histogram for a single RAGAS metric"""
        
        # Filter out None/NaN values
        valid_scores = df[metric_name].dropna()
        
        if len(valid_scores) == 0:
            print(f"No valid data for {metric_name}")
            return
        
        plt.figure(figsize=(8, 6))
        
        # Create histogram
        plt.hist(valid_scores, bins=20, alpha=0.7, color='skyblue', edgecolor='black')
        
        # Add mean line
        mean_score = valid_scores.mean()
        plt.axvline(mean_score, color='red', linestyle='--', linewidth=2, 
                    label=f'Mean: {mean_score:.3f}')
        
        # Customize the plot
        plt.xlabel(f'{metric_name.replace("_", " ").title()} Score')
        plt.ylabel('Frequency')
        plt.title(f'Distribution of {metric_name.replace("_", " ").title()} Scores')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        # Add statistics text box
        stats_text = f'Count: {len(valid_scores)}\nMean: {mean_score:.3f}\nStd: {valid_scores.std():.3f}'
        plt.text(0.02, 0.98, stats_text, transform=plt.gca().transAxes, 
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
        
        plt.tight_layout()
        plt.show()
    
    @staticmethod
    def plot_all_metrics_histograms(df, metrics=None):
        """Plot histograms for all RAGAS metrics in subplots"""
        
        if metrics is None:
            metrics = ['answer_relevancy', 'faithfulness', 'context_precision', 'context_recall']
        
        # Filter metrics that exist in DataFrame
        available_metrics = [m for m in metrics if m in df.columns]
        
        if not available_metrics:
            print("No RAGAS metrics found in DataFrame")
            return
        
        # Calculate subplot grid
        n_metrics = len(available_metrics)
        cols = 2
        rows = (n_metrics + 1) // cols
        
        fig, axes = plt.subplots(rows, cols, figsize=(15, 5*rows))
        
        # Handle single row case
        if rows == 1:
            axes = [axes] if n_metrics == 1 else axes
        else:
            axes = axes.flatten()
        
        colors = ['skyblue', 'lightgreen', 'lightcoral', 'lightsalmon']
        
        for i, metric in enumerate(available_metrics):
            ax = axes[i]
            
            # Get valid scores
            valid_scores = df[metric].dropna()
            
            if len(valid_scores) == 0:
                ax.text(0.5, 0.5, f'No data for\n{metric}', 
                    ha='center', va='center', transform=ax.transAxes, fontsize=12)
                ax.set_title(f'{metric.replace("_", " ").title()}')
                continue
            
            # Create histogram
            ax.hist(valid_scores, bins=15, alpha=0.7, color=colors[i % len(colors)], 
                    edgecolor='black')
            
            # Add mean line
            mean_score = valid_scores.mean()
            ax.axvline(mean_score, color='red', linestyle='--', linewidth=2)
            
            # Customize
            ax.set_xlabel('Score')
            ax.set_ylabel('Frequency')
            ax.set_title(f'{metric.replace("_", " ").title()}\n(Mean: {mean_score:.3f}, n={len(valid_scores)})')
            ax.grid(True, alpha=0.3)
            ax.set_xlim(0, 1)  # RAGAS metrics are typically 0-1
        
        # Hide extra subplots
        for i in range(n_metrics, len(axes)):
            axes[i].set_visible(False)
        
        plt.tight_layout()
        plt.show()
    
    @staticmethod
    def plot_overlapped_histograms(df, metrics=None):
        """Plot multiple metrics as overlapped histograms"""
        
        if metrics is None:
            metrics = ['answer_relevancy', 'faithfulness', 'context_precision', 'context_recall']
        
        plt.figure(figsize=(12, 8))
        
        colors = ['blue', 'green', 'red', 'orange', 'purple']
        
        for i, metric in enumerate(metrics):
            if metric in df.columns:
                valid_scores = df[metric].dropna()
                if len(valid_scores) > 0:
                    plt.hist(valid_scores, bins=20, alpha=0.6, 
                            label=f'{metric.replace("_", " ").title()} (n={len(valid_scores)})',
                            color=colors[i % len(colors)])
        
        plt.xlabel('Score')
        plt.ylabel('Frequency')
        plt.title('Distribution Comparison of All RAGAS Metrics')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.xlim(0, 1)
        plt.tight_layout()
        plt.show()
    
    @staticmethod
    def plot_metric_analysis(df, metric_name):
        """Combined histogram and box plot for detailed analysis"""
        
        valid_scores = df[metric_name].dropna()
        
        if len(valid_scores) == 0:
            print(f"No valid data for {metric_name}")
            return
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 10))
        
        # Histogram on top
        ax1.hist(valid_scores, bins=20, alpha=0.7, color='skyblue', edgecolor='black')
        mean_score = valid_scores.mean()
        ax1.axvline(mean_score, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_score:.3f}')
        ax1.set_ylabel('Frequency')
        ax1.set_title(f'{metric_name.replace("_", " ").title()} - Distribution Analysis')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Box plot on bottom
        box_plot = ax2.boxplot(valid_scores, vert=False, patch_artist=True)
        box_plot['boxes'][0].set_facecolor('lightblue')
        ax2.set_xlabel('Score')
        ax2.set_title('Box Plot (shows quartiles and outliers)')
        ax2.grid(True, alpha=0.3)
        
        # Add statistics
        stats = {
            'Count': len(valid_scores),
            'Mean': mean_score,
            'Median': valid_scores.median(),
            'Std Dev': valid_scores.std(),
            'Min': valid_scores.min(),
            'Max': valid_scores.max()
        }
        
        stats_text = '\n'.join([f'{k}: {v:.3f}' if isinstance(v, float) else f'{k}: {v}' 
                            for k, v in stats.items()])
        
        ax1.text(0.98, 0.98, stats_text, transform=ax1.transAxes, 
                verticalalignment='top', horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
        
        plt.tight_layout()
        plt.show()
    
    @staticmethod
    def plot_dfs_against_eachother(df1, df2, metric, bins=20):
        """Compare two dataframes with identical scales"""
        
        # Get data
        data1 = df1[metric].dropna()
        data2 = df2[metric].dropna()
        
        # Define common bins (crucial for comparison)
        if isinstance(bins, int):
            # Use the full range of both datasets
            min_val = min(data1.min(), data2.min())
            max_val = max(data1.max(), data2.max())
            bins = np.linspace(min_val, max_val, bins + 1)
        
        # Create subplots
        fig, axes = plt.subplots(1, 2, figsize=(15, 6))
        
        # Plot both histograms with same bins
        n1, _, _ = axes[0].hist(data1, bins=bins, alpha=0.7, color='blue', edgecolor='black')
        n2, _, _ = axes[1].hist(data2, bins=bins, alpha=0.7, color='red', edgecolor='black')
        
        # Set same y-axis limits
        max_count = max(n1.max(), n2.max())
        axes[0].set_ylim(0, max_count * 1.1)
        axes[1].set_ylim(0, max_count * 1.1)
        
        # Set same x-axis limits
        x_min, x_max = bins[0], bins[-1]
        axes[0].set_xlim(x_min, x_max)
        axes[1].set_xlim(x_min, x_max)
        
        # Add labels and stats
        axes[0].set_title(f'Dataset 1 - {metric}\n(n={len(data1)}, mean={data1.mean():.3f})')
        axes[1].set_title(f'Dataset 2 - {metric}\n(n={len(data2)}, mean={data2.mean():.3f})')
        
        axes[0].set_xlabel('Score')
        axes[1].set_xlabel('Score')
        axes[0].set_ylabel('Frequency')
        
        # Add mean lines
        axes[0].axvline(data1.mean(), color='darkblue', linestyle='--', linewidth=2)
        axes[1].axvline(data2.mean(), color='darkred', linestyle='--', linewidth=2)
        
        plt.tight_layout()
        plt.show()
    
    @staticmethod
    def plot_ragas_scores_time_series(df, metrics = ['faithfulness']):
        # Number of metrics to show
        num_metrics = len(metrics)
        
        x = np.arange(len(df))  # question indices
        # Width of each bar
        bar_width = 0.2
        
        # Plot each metric
        for i, metric in enumerate(metrics):
            plt.bar(x + i * bar_width, df[metric], width=bar_width, label=metric)
        
        # Labels & title
        plt.title("Scores per question", fontsize=16)
        plt.ylabel("Score", fontsize=14)
        plt.xlabel("Question #", fontsize=14)
        
        # Use numeric ticks for questions
        plt.xticks(x + bar_width * (num_metrics - 1) / 2, [str(i) for i in x], fontsize=10)
        
        plt.legend(fontsize=12, loc="upper right")
        plt.show()
    
    @staticmethod
    def plot_bars_against_questions(df):
        # Set figure size before plotting
        plt.figure(figsize=(12, 6))  
        
        df_scores = df[["question", "faithfulness", "answer_relevancy", "context_recall", "context_precision"]]
        df_scores.set_index("question").plot(kind="bar", figsize=(12, 6), width = 0.9)  # you can also pass here
        plt.title("Scores per question", fontsize=16)
        plt.ylabel("Score", fontsize=14)
        plt.xlabel("Question", fontsize=14)
        plt.xticks(rotation=45, ha="right", fontsize=10)  # rotate labels for readability
        plt.legend(fontsize=12, loc="upper right")
    
    @staticmethod
    def plot_metrics_across_all_dfs(dfs, labels, metric, bins=20):
        plt.figure(figsize=(10, 6))
    
        for df, label in zip(dfs, labels):
            if metric not in df.columns:
                raise ValueError(f"Metric '{metric}' not found in dataset {label}")
            plt.hist(
                df[metric].dropna(), 
                bins=bins, 
                alpha=0.5, 
                label=label, 
                edgecolor="black"
            )
    
        plt.xlabel(metric)
        plt.ylabel("Frequency")
        plt.title(f"Distribution of {metric} across datasets")
        plt.legend()
        plt.show()


class output_handler: 

    def __init__(self, file):
        self.file_path = Path(file)
        self.parse_file(file)

    def parse_file(self, file):
        with open(file, 'r') as f:
            all_data = json.load(f)

        self.config_info = {}
        for name, data  in all_data.items(): 
            if "benchmarking" in name: 
                self.config_info[name] = data

        single_question_data = []
        for config_name, data in self.config_info.items(): 
            single_question_data.append((config_name, data['single question results']))

        self.dfs = {}
        for name, data in single_question_data:
            self.dfs[name] = pd.DataFrame.from_dict(data, orient="index")

        def get_correct_val(s):
            if s == "NOT FOUND": 
                return 0
            else: 
                return 1

        for dataframe in self.dfs.values():
            dataframe['link_result_bools'] = dataframe['link_result'].apply(get_correct_val) 

    def get_config_names(self):
        return list(self.config_info.keys())

    def get_full_config(self, name):
        config = self.config_info[name]
        return config

    def get_data_manager_config(self, name):
        config = self.config_info[name]
        return config.get("data_manager") 
    
    def get_models_configs(self, name):
        config = self.config_info[name]
        pipeline_name = config.get('a2rchi').get('pipelines')[0]

        return config.get('a2rchi').get('pipeline_map').get(pipeline_name).get('models')

    def get_model_class_map(self, name):
        config = self.config_info[name]
        return config.get('model_class_map') 

    def get_prompts(self, name):
        config = self.config_info[name]
        pipeline_name = config.get('a2rchi').get('pipelines')[0]
        return config.get('a2rchi').get('pipeline_map').get(pipeline_name).get('prompts')

    def get_dfs(self):
        return self.dfs
