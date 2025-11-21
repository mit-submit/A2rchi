#!/usr/bin/env python3
"""
Compare Expected vs Actual Outputs from A2rchi Benchmarking

This script helps evaluate benchmarking results by showing:
- The question asked
- A2rchi's actual answer
- The expected (reference) answer
- Retrieved contexts
- RAGAS scores (if available)

Usage:
    python compare_benchmark_outputs.py <results.json> -o <output.html>
"""

import json
import sys
import argparse
import html
from pathlib import Path
from datetime import datetime


def load_benchmark_results(filepath):

    """Load and parse benchmark results JSON"""
    with open(filepath, 'r') as f:
        data = json.load(f)

    return data['benchmarking_results'], data['metadata']

def format_html_output(result):
    """Format results as HTML for easier reading"""

    questions = result.get('single_question_results', {})
    total_results = result.get('total_results', {})
    config_name = result.get('configuration_file', 'Unknown configuration')
    config = result.get('configuration', {})
    timestamp = result.get("metadata", {}).get("time", "Unknown time")
    
    html_parts = ["""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Benchmark Results Comparison</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }
        .metrics {
            background: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .question-card {
            background: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .section {
            margin: 20px 0;
        }
        .section-title {
            font-weight: bold;
            font-size: 1.1em;
            margin-bottom: 10px;
            color: #667eea;
        }
        .answer-box {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            border-left: 4px solid #667eea;
            margin: 10px 0;
            white-space: pre-wrap;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 0.9em;
        }
        .expected-box {
            border-left-color: #28a745;
        }
        .context-box {
            background: #fff3cd;
            padding: 10px;
            border-radius: 5px;
            margin: 5px 0;
            font-size: 0.85em;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }
        .metric-item {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            text-align: center;
        }
        .metric-value {
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }
        .metric-label {
            font-size: 0.9em;
            color: #666;
            margin-top: 5px;
        }
        .score-low { color: #dc3545; }
        .score-medium { color: #ffc107; }
        .score-high { color: #28a745; }
    </style>
</head>
<body>
"""]
    
    # Header
    html_parts.append(f"""
    <div class="header">
        <h1>📊 Benchmark Results Comparison</h1>
        <p><strong>Configuration:</strong> {config_name}</p>
        <p><strong>Timestamp:</strong> {timestamp}</p>
        <p><strong>Questions Processed:</strong> {len(questions)}</p>
    </div>
""")
    
    # sources (retrieval accuracy) metrics
    if 'SOURCES' in config.get('services', {}).get('benchmarking', {}).get('modes', []):
        ret_accuracy = total_results.get('source_accuracy', None)
        if ret_accuracy: ret_accuracy *= 100
        ret_partial = total_results.get('relative_source_accuracy', None)
        if ret_partial: ret_partial *= 100

        html_parts.append('<div class="metrics">')
        html_parts.append('<h2>🎯 Retrieval Accuracy</h2>')
        html_parts.append('<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; max-width: 900px; margin: 0 auto;">')
        
        # Fully Correct
        score_class = 'score-low' if ret_accuracy < 50 else 'score-medium' if ret_accuracy < 80 else 'score-high'
        html_parts.append(f"""
            <div class="metric-item">
                <div class="metric-value {score_class}">{ret_accuracy:.1f}%</div>
                <div class="metric-label">Retrieval Accuracy (at least one source found)</div>
            </div>
        """)
        html_parts.append(f"""
            <div class="metric-item">
                <div class="metric-value score-medium">{ret_partial:.1f}%</div>
                <div class="metric-label">Partially Correct (some sources found)</div>
            </div>
        """)

        html_parts.append('</div></div>')
    
    # ragas metrics
    if 'RAGAS' in config.get('services', {}).get('benchmarking', {}).get('modes', []):
        html_parts.append('<div class="metrics">')
        html_parts.append('<h2>Aggregate RAGAS Metrics</h2>')
        html_parts.append('<div class="metrics-grid">')
        for metric, value in total_results.items():
            if 'aggregate' in metric:
                clean_name = metric.replace('aggregate_', '').replace('_', ' ').title()
                score_class = 'score-low' if value < 0.5 else 'score-medium' if value < 0.7 else 'score-high'
                html_parts.append(f"""
                <div class="metric-item">
                    <div class="metric-value {score_class}">{value:.3f}</div>
                    <div class="metric-label">{clean_name}</div>
                </div>
                """)
        html_parts.append('</div></div>')
    
    # Each Question
    for i, (qid, q_data) in enumerate(questions.items(), 1):

        html_parts.append(f'<div class="question-card">')
        html_parts.append(f'<h2>Question {i}: {qid}</h2>')
        
        # Question
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">❓ Question</div>')
        html_parts.append(f'<p>{q_data["question"]}</p>')
        html_parts.append(f'</div>')
        
        # Retrieval Check
        
        # reference sources
        reference_sources_metadata = q_data.get('reference_sources_metadata', [])
        reference_sources_match_fields = q_data.get('reference_sources_match_fields', [])
        expected_sources = []
        for ref_source, match_field in zip(reference_sources_metadata, reference_sources_match_fields):
            expected_sources.append(ref_source[match_field])
        found_sources = [source for i, source in enumerate(expected_sources) if reference_sources_metadata[i]['matched']]

        # retrieved sources
        sources_metadata = q_data.get('sources_metadata', [])
        sources_trunc_content = q_data.get('sources_trunc_content', [])
        retrieved_sources = [s['display_name'] for s in sources_metadata]
        
        # Check if any expected source was retrieved
        expected_sources_set = set(expected_sources) 
        
        retrieval_status = 'none'
        if len(found_sources) == len(expected_sources_set) and len(expected_sources_set) > 0:
            retrieval_status = 'full'
        elif len(found_sources) > 0:
            retrieval_status = 'partial'
        
        if expected_sources:
            if retrieval_status == 'full':
                status_class = 'score-high'
                status_icon = '✅'
                status_text = 'FULLY CORRECT'
            elif retrieval_status == 'partial':
                status_class = 'score-medium'
                status_icon = '⚠️'
                status_text = f'PARTIALLY CORRECT ({len(found_sources)}/{len(expected_sources_set)} sources found)'
            else:
                status_class = 'score-low'
                status_icon = '❌'
                status_text = 'INCORRECT'
            
            # Display expected sources
            expected_display = ', '.join(expected_sources)
    
            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">🎯 Retrieval Check</div>')
            html_parts.append(f'<div style="background: #f8f9fa; padding: 15px; border-radius: 5px;">')
            html_parts.append(f'<p><strong>Expected Document(s):</strong> {expected_display}</p>')
            html_parts.append(f'<p><strong>Retrieved Documents:</strong> {", ".join(retrieved_sources) if retrieved_sources else "None"}</p>')
            html_parts.append(f'<p><strong class="{status_class}">{status_icon} Status: {status_text}</strong></p>')
            html_parts.append(f'</div>')
            html_parts.append(f'</div>')
        
        # A2rchi's Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">🤖 A2rchi\'s Answer</div>')
        html_parts.append(f'<div class="answer-box">{q_data["answer"]}</div>')
        html_parts.append(f'</div>')
        
        # Expected Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">✅ Expected Answer</div>')
        html_parts.append(f'<div class="answer-box expected-box">{q_data["reference_answer"]}</div>')
        html_parts.append(f'</div>')
        
        # Expected Documents/Sources
        if expected_sources:
            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">🎯 Expected Source Documents</div>')
            html_parts.append(f'<div style="background: #e8f5e9; border-left: 4px solid #4CAF50; padding: 15px; border-radius: 5px;">')
            html_parts.append(f'<ul style="margin: 0; padding-left: 20px;">')
            for source in expected_sources:
                html_parts.append(f'<li style="padding: 5px 0;"><strong>{source}</strong></li>')
            html_parts.append(f'</ul>')
            html_parts.append(f'</div>')
            html_parts.append(f'</div>')
        
        # Retrieved Contexts/Documents
        contexts = q_data.get('contexts', [])
        if contexts:
            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">📚 Retrieved Documents ({len(contexts)})</div>')
            for j, ctx in enumerate(contexts, 1):
                # Extract ticket ID from context
                ticket_id = retrieved_sources[j-1]
                ticket_badge = f'<span style="background: #2196F3; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.85em; margin-left: 10px;">{ticket_id}</span>' if ticket_id else ''
                
                # Parse context if it's a Document representation
                if isinstance(ctx, str) and ctx.startswith('page_content='):
                    try:
                        content_start = ctx.find("page_content='") + len("page_content='")
                        content_end = ctx.find("' metadata=", content_start)
                        if content_end != -1:
                            ctx_text = ctx[content_start:content_end]
                            # Extract metadata
                            metadata_start = ctx.find("metadata={", content_end)
                            if metadata_start != -1:
                                metadata_end = ctx.find("}", metadata_start)
                                metadata_text = ctx[metadata_start:metadata_end+1]
                        else:
                            ctx_text = ctx
                            metadata_text = ""
                    except:
                        ctx_text = ctx
                        metadata_text = ""
                else:
                    ctx_text = str(ctx)
                    metadata_text = ""
                
                # Truncate if too long for display
                display_text = ctx_text[:500] + "..." if len(ctx_text) > 500 else ctx_text
                full_text = ctx_text.replace('<', '&lt;').replace('>', '&gt;')
                
                html_parts.append(f'''
                <div class="context-box" style="background: #f8f9fa; border-left: 3px solid #2196F3; padding: 15px; margin: 10px 0; border-radius: 5px;">
                    <div style="font-weight: bold; margin-bottom: 8px;">Document {j}{ticket_badge}</div>
                    <div style="font-size: 0.9em; white-space: pre-wrap; font-family: 'Courier New', monospace;">{display_text}</div>
                    {f'<details style="margin-top: 10px;"><summary style="cursor: pointer; color: #667eea;">Show full document</summary><pre style="margin-top: 10px; font-size: 0.85em; overflow-x: auto;">{full_text}</pre></details>' if len(ctx_text) > 500 else ''}
                </div>
                ''')
            html_parts.append(f'</div>')

        # Agent message trace
        messages = q_data.get('messages', [])
        if messages:
            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">💬 Agent Messages ({len(messages)})</div>')
            html_parts.append(f'<div style="display: flex; flex-direction: column; gap: 12px;">')
            for m_idx, message in enumerate(messages, 1):
                msg_type = message.get('type', 'message')
                if msg_type == 'tool_call':
                    title = f'🛠️ Tool Call #{m_idx}: {message.get("tool_name", "Unknown Tool")}'
                    args = message.get('tool_args')
                    body = f'<strong>Args:</strong> {html.escape(str(args))}' if args is not None else '<em>No arguments provided</em>'
                    border_color = '#17a2b8'
                elif msg_type == 'ai_message':
                    title = f'🤖 Assistant Message #{m_idx}'
                    content = message.get('content', '')
                    body = html.escape(str(content)).replace('\\n', '<br>')
                    border_color = '#6f42c1'
                else:
                    title = f'📝 Message #{m_idx}'
                    fallback = message.get('content', message)
                    body = html.escape(str(fallback)).replace('\\n', '<br>')
                    border_color = '#343a40'
                html_parts.append(f'''
                <div class="answer-box" style="background: #fff; border-left-color: {border_color};">
                    <div style="font-weight: 600; margin-bottom: 6px;">{title}</div>
                    <div style="font-size: 0.9em; white-space: pre-wrap;">{body}</div>
                </div>
                ''')
            html_parts.append(f'</div>')
            html_parts.append(f'</div>')


        if 'RAGAS' in config.get('services', {}).get('benchmarking', {}).get('modes', []):
        
            # RAGAS Metrics
            ragas_metrics = {
                'answer_relevancy': 'Answer Relevancy',
                'faithfulness': 'Faithfulness',
                'context_precision': 'Context Precision',
                'context_recall': 'Context Recall'
            }
            
            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">📊 RAGAS Scores</div>')
            html_parts.append(f'<div class="metrics-grid">')
            for metric_key, metric_name in ragas_metrics.items():
                if metric_key in q_data and q_data[metric_key] is not None:
                    value = q_data[metric_key]
                    score_class = 'score-low' if value < 0.5 else 'score-medium' if value < 0.7 else 'score-high'
                    html_parts.append(f"""
                    <div class="metric-item">
                        <div class="metric-value {score_class}">{value:.3f}</div>
                        <div class="metric-label">{metric_name}</div>
                    </div>
                    """)
            html_parts.append(f'</div></div>')
            
        html_parts.append(f'</div>')
        
    html_parts.append('</body></html>')
    return '\n'.join(html_parts)


def main():
    parser = argparse.ArgumentParser(
        description='Compare expected vs actual outputs from A2rchi benchmarking',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="e.g. python compare_benchmark_outputs.py results.json -o report.html"
    )
    
    parser.add_argument('results_file', help='Path to benchmark results JSON file')
    parser.add_argument('--question', '-q', type=int, help='Show only specific question number')
    parser.add_argument('--config', '-c', type=str, help='Show only specific configuration name')
    parser.add_argument('-o', '--output', type=str, help="Output directory for HTML report") 
    parser.add_argument('--oname', type=str, help="Output html file name. (Default: '<results_file>_report.html')")
    
    args = parser.parse_args()
    
    # Validate input file
    if not Path(args.results_file).exists():
        print(f"Error: File '{args.results_file}' not found", file=sys.stderr)
        sys.exit(1)
    
    # Load results
    try:
        results, metadata = load_benchmark_results(args.results_file)
    except Exception as e:
        print(f"Error loading results: {e}", file=sys.stderr)
        sys.exit(1)

    # iterate over the configurations used to run the benchmark
    for result in results:

        # Filter by question number
        config_name = result.get('configuration_file', 'Unknown Configuration')
        if args.config and args.config != config_name:
            continue

        # produce the html
        html_content = format_html_output(result)

        # Save output
        ofilename = args.oname if args.oname else f"{Path(args.results_file).stem}_report.html"
        if args.output:
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            ofilename = out_dir / ofilename
        with open(ofilename, 'w') as f:
            f.write(html_content)
        print(f"✅ HTML report generated: {ofilename}")

if __name__ == '__main__':
    main()
