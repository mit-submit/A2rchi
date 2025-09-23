import pandas as pd 
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

# ========================================
# 1. SINGLE HISTOGRAM - Basic Example
# ========================================

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

# Usage example:
# plot_single_metric_histogram(results_df, 'answer_relevancy')

# ========================================
# 2. MULTIPLE HISTOGRAMS - Subplots
# ========================================

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

# Usage:
# plot_all_metrics_histograms(results_df)

# ========================================
# 3. OVERLAPPED HISTOGRAMS - Compare Metrics
# ========================================

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

# ========================================
# 4. ADVANCED: Box Plot + Histogram Combo
# ========================================

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

# ========================================
# 5. CUSTOM STYLING EXAMPLE
# ========================================

def plot_styled_histogram(df, metric_name):
    """Histogram with custom styling"""
    
    valid_scores = df[metric_name].dropna()
    
    # Set style
    plt.style.use('seaborn-v0_8-darkgrid')  # or try 'ggplot', 'bmh', etc.
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Create histogram with custom bins
    n, bins, patches = ax.hist(valid_scores, bins=np.linspace(0, 1, 21), 
                              alpha=0.8, color='steelblue', edgecolor='white', linewidth=1.2)
    
    # Color bars based on score ranges
    for i, (patch, bin_start) in enumerate(zip(patches, bins[:-1])):
        if bin_start < 0.5:
            patch.set_facecolor('lightcoral')
        elif bin_start < 0.7:
            patch.set_facecolor('khaki')
        else:
            patch.set_facecolor('lightgreen')
    
    # Add statistics
    mean_score = valid_scores.mean()
    ax.axvline(mean_score, color='darkred', linestyle='--', linewidth=3, 
               label=f'Mean: {mean_score:.3f}')
    
    # Customize
    ax.set_xlabel(f'{metric_name.replace("_", " ").title()} Score', fontsize=12)
    ax.set_ylabel('Number of Questions', fontsize=12)
    ax.set_title(f'{metric_name.replace("_", " ").title()} Score Distribution\n'
                 f'Total Questions: {len(valid_scores)}', fontsize=14, fontweight='bold')
    ax.legend(fontsize=11)
    
    # Add performance zone labels
    ax.text(0.25, ax.get_ylim()[1]*0.9, 'Needs\nImprovement', 
            ha='center', va='center', fontsize=10, 
            bbox=dict(boxstyle='round', facecolor='lightcoral', alpha=0.7))
    ax.text(0.6, ax.get_ylim()[1]*0.9, 'Good', 
            ha='center', va='center', fontsize=10,
            bbox=dict(boxstyle='round', facecolor='khaki', alpha=0.7))
    ax.text(0.85, ax.get_ylim()[1]*0.9, 'Excellent', 
            ha='center', va='center', fontsize=10,
            bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.7))
    
    plt.tight_layout()
    plt.show()


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

