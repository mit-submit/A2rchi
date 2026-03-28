#!/usr/bin/env python3
"""
Compare Expected vs Actual Outputs from archi Benchmarking

This script helps evaluate benchmarking results by showing:
- The question asked
- archi's actual answer
- The expected (reference) answer
- Retrieved contexts
- RAGAS scores (if available)

Usage:
    python generate_benchmark_report.py <results.json>
    python generate_benchmark_report.py <results.json> --html output.html
    python generate_benchmark_report.py <results.json> --question 1
"""

import json
import sys
import argparse
import html
from pathlib import Path
from datetime import datetime


def get_single_question_results(config_data):
    """Return the single question results regardless of key format."""
    return (
        config_data.get('single question results')
        or config_data.get('single_question_results')
        or {}
    )

def get_total_results(config_data):
    """Return the total results regardless of key format."""
    return (
        config_data.get('total results')
        or config_data.get('total_results')
        or {}
    )

def load_benchmark_results(filepath):

    """Load and parse benchmark results JSON"""
    with open(filepath, 'r') as f:
        data = json.load(f)

    return data['benchmarking_results'], data['metadata']

def parse_benchmark_results(results, metadata):
    """Parse benchmark results JSON"""

    result = results[0]
    
    questions = result.get('single_question_results', {})
    total_results = result.get('total_results', {})
    config_name = result.get('configuration_file', 'Unknown configuration')
    config_data = result.get('configuration', {})
    timestamp = metadata.get("time", "Unknown time")
    
    return config_data, config_name, timestamp, questions, total_results


def format_total_duration(raw_duration):
    """Convert a raw duration value from LangChain messages into a readable string.

    LangChain providers differ in units; Ollama, for example, reports nanoseconds.
    Use simple magnitude-based heuristics and keep the raw value available for reference.
    """
    try:
        value = float(raw_duration)
    except (TypeError, ValueError):
        return None, None

    if value <= 0:
        return None, None

    if value >= 1_000_000_000:
        seconds = value / 1_000_000_000  # assume nanoseconds
        assumed_unit = "nanoseconds"
    elif value >= 1_000_000:
        seconds = value / 1_000_000  # assume microseconds
        assumed_unit = "microseconds"
    elif value >= 1_000:
        seconds = value / 1_000  # assume milliseconds
        assumed_unit = "milliseconds"
    else:
        seconds = value
        assumed_unit = "seconds"

    if seconds >= 1:
        friendly = f"{seconds:.2f}s"
    elif seconds >= 0.001:
        friendly = f"{seconds * 1000:.0f}ms"
    else:
        friendly = f"{seconds * 1_000_000:.0f}µs"

    return friendly, assumed_unit


def format_html_output(config_data, config_name, timestamp,questions, total_results):
    """Format results as HTML for easier reading"""

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
    if 'SOURCES' in config_data.get('services', {}).get('benchmarking', {}).get('modes', []):

        # Retrieval Accuracy
        ret_accuracy = total_results.get('source_accuracy', None)
        ret_total = len(questions)
        ret_correct =  int(ret_total*ret_accuracy)

        if ret_accuracy: ret_accuracy *= 100
        ret_partial = total_results.get('relative_source_accuracy', None)
        ret_partial =  int(ret_total*ret_partial)-ret_correct

        html_parts.append('<div class="metrics">')
        html_parts.append('<h2>🎯 Retrieval Accuracy</h2>')
        html_parts.append('<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; max-width: 900px; margin: 0 auto;">')

        # Fully Correct
        score_class = 'score-low' if ret_accuracy < 50 else 'score-medium' if ret_accuracy < 80 else 'score-high'
        html_parts.append(f"""
            <div class="metric-item">
                <div class="metric-value {score_class}">{ret_accuracy:.1f}%</div>
                <div class="metric-label">Fully Correct: {ret_correct}/{ret_total}</div>
            </div>
        """)

        # Partially Correct
        if ret_partial > 0:
            html_parts.append(f"""
                <div class="metric-item">
                    <div class="metric-value score-medium">{ret_partial}</div>
                    <div class="metric-label">Partially Correct (some sources found)</div>
                </div>
            """)

        # Incorrect
        ret_incorrect = ret_total - ret_correct - ret_partial
        if ret_incorrect > 0:
            html_parts.append(f"""
                <div class="metric-item">
                    <div class="metric-value score-low">{ret_incorrect}</div>
                    <div class="metric-label">Incorrect (no sources found)</div>
                </div>
            """)

        html_parts.append('</div></div>')

    if 'RAGAS' in config_data.get('services', {}).get('benchmarking', {}).get('modes', []):

        # Aggregate RAGAS Metrics
        if total_results:
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
        
        # reference sources
        reference_sources_metadata = q_data.get('reference_sources_metadata', [])
        reference_sources_match_fields = q_data.get('reference_sources_match_fields', [])
        expected_sources = []
        for ref_source, match_field in zip(reference_sources_metadata, reference_sources_match_fields):
            expected_sources.append(ref_source[match_field])
        found_sources = [source for i, source in enumerate(expected_sources) if reference_sources_metadata[i]['matched']]

        # retrieved sources
        sources_metadata = q_data.get('sources_metadata', [])
        retrieved_sources = [
            s.get("display_name") or s.get("file_name") or "" for s in sources_metadata
        ]
        
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


        # archi's Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">🤖 archi\'s Answer</div>')
        html_parts.append(f'<div class="answer-box">{q_data.get("answer", "N/A")}</div>')
        html_parts.append(f'</div>')
        
        # Expected Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">✅ Expected Answer</div>')
        html_parts.append(f'<div class="answer-box expected-box">{q_data.get("reference_answer", "N/A")}</div>')
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
                duration_display, duration_unit = format_total_duration(message.get("total_duration"))
                duration_suffix = f" ({duration_display})" if duration_display else ""
                duration_title = ""
                if duration_display:
                    raw_duration = html.escape(str(message.get("total_duration")))
                    unit_hint = f"assumed {duration_unit}" if duration_unit else "raw value"
                    duration_title = f' title="Raw duration: {raw_duration} ({unit_hint})"'
                if msg_type == 'tool_call':
                    title = f'🛠️ Tool Call #{m_idx}: {message.get("tool_name", "Unknown Tool")}{duration_suffix}'
                    args = message.get('tool_args')
                    body = f'<strong>Args:</strong> {html.escape(str(args))}' if args is not None else '<em>No arguments provided</em>'
                    border_color = '#17a2b8'
                elif msg_type == 'ai_message':
                    title = f'🤖 Assistant Message #{m_idx}{duration_suffix}'
                    content = message.get('content', '')
                    body = html.escape(str(content)).replace('\\n', '<br>')
                    border_color = '#6f42c1'
                else:
                    title = f'📝 Message #{m_idx}{duration_suffix}'
                    fallback = message.get('content', message)
                    body = html.escape(str(fallback)).replace('\\n', '<br>')
                    border_color = '#343a40'
                html_parts.append(f'''
                <div class="answer-box" style="background: #fff; border-left-color: {border_color};">
                    <div style="font-weight: 600; margin-bottom: 6px;"{duration_title}>{title}</div>
                    <div style="font-size: 0.9em; white-space: pre-wrap;">{body}</div>
                </div>
                ''')
            html_parts.append(f'</div>')
            html_parts.append(f'</div>')
            

        # RAGAS Metrics
        ragas_metrics = {
            'answer_relevancy': 'Answer Relevancy',
            'faithfulness': 'Faithfulness',
            'context_precision': 'Context Precision',
            'context_recall': 'Context Recall'
        }

        if 'RAGAS' in config_data.get('services', {}).get('benchmarking', {}).get('modes', []):
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

def format_ab_html_output(ab_comparison):
    """Format A/B comparison results as a side-by-side HTML report."""
    config_a = ab_comparison.get("config_a", {})
    config_b = ab_comparison.get("config_b", {})
    aggregate = ab_comparison.get("aggregate", {})
    per_question = ab_comparison.get("per_question", [])

    label_a = f"{config_a.get('agent_class', '?')}/{config_a.get('provider', '?')}/{config_a.get('model', '?')}"
    label_b = f"{config_b.get('agent_class', '?')}/{config_b.get('provider', '?')}/{config_b.get('model', '?')}"

    wins_a = aggregate.get("wins_a", 0)
    wins_b = aggregate.get("wins_b", 0)
    ties = aggregate.get("ties", 0)
    mean_a = aggregate.get("mean_scores_a", {})
    mean_b = aggregate.get("mean_scores_b", {})

    parts = ["""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>A/B Benchmark Comparison</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1400px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }
.card { background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
.side-by-side { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.answer-box { background: #f8f9fa; padding: 15px; border-radius: 5px; border-left: 4px solid #667eea; margin: 10px 0; white-space: pre-wrap; font-family: 'Monaco', 'Courier New', monospace; font-size: 0.9em; }
.box-b { border-left-color: #e85d04; }
.winner { background: #d4edda; border-color: #28a745; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 10px 14px; border: 1px solid #dee2e6; text-align: center; }
th { background: #f8f9fa; }
.win-a { color: #667eea; font-weight: bold; }
.win-b { color: #e85d04; font-weight: bold; }
.tie { color: #6c757d; }
.metric-value { font-size: 2em; font-weight: bold; }
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-top: 15px; }
.metric-item { background: #f8f9fa; padding: 15px; border-radius: 5px; text-align: center; }
</style></head><body>"""]

    parts.append(f"""
<div class="header">
    <h1>A/B Benchmark Comparison</h1>
    <p><strong>Config A:</strong> {html.escape(label_a)}</p>
    <p><strong>Config B:</strong> {html.escape(label_b)}</p>
    <p><strong>Questions:</strong> {len(per_question)}</p>
</div>""")

    # Aggregate summary
    parts.append('<div class="card"><h2>Aggregate Results</h2>')
    parts.append('<div class="metrics-grid">')
    parts.append(f'<div class="metric-item"><div class="metric-value win-a">{wins_a}</div><div>Wins A</div></div>')
    parts.append(f'<div class="metric-item"><div class="metric-value win-b">{wins_b}</div><div>Wins B</div></div>')
    parts.append(f'<div class="metric-item"><div class="metric-value tie">{ties}</div><div>Ties</div></div>')
    parts.append('</div>')

    # Mean scores table
    all_metrics = sorted(set(list(mean_a.keys()) + list(mean_b.keys())))
    if all_metrics:
        parts.append('<table style="margin-top:20px;"><tr><th>Metric</th><th>Config A</th><th>Config B</th><th>Winner</th></tr>')
        for m in all_metrics:
            sa = mean_a.get(m, 0.0)
            sb = mean_b.get(m, 0.0)
            if abs(sa - sb) < 1e-9:
                w_class, w_text = "tie", "Tie"
            elif sa > sb:
                w_class, w_text = "win-a", "A"
            else:
                w_class, w_text = "win-b", "B"
            parts.append(f'<tr><td>{html.escape(m.replace("_", " ").title())}</td><td>{sa:.4f}</td><td>{sb:.4f}</td><td class="{w_class}">{w_text}</td></tr>')
        parts.append('</table>')
    parts.append('</div>')

    # Per-question cards
    for idx, qr in enumerate(per_question, 1):
        question_text = html.escape(qr.get("question", ""))
        ref = html.escape(qr.get("reference_answer", ""))
        ans_a = html.escape(qr.get("answer_a", ""))
        ans_b = html.escape(qr.get("answer_b", ""))
        time_a = qr.get("time_a", 0.0)
        time_b = qr.get("time_b", 0.0)
        ragas_a = qr.get("ragas_a", {})
        ragas_b = qr.get("ragas_b", {})
        winner_by = qr.get("winner_by_metric", {})

        parts.append(f'<div class="card"><h2>Question {idx}</h2>')
        parts.append(f'<p><strong>Q:</strong> {question_text}</p>')
        if ref:
            parts.append(f'<p><strong>Reference:</strong> {ref}</p>')

        parts.append('<div class="side-by-side">')
        # Config A answer
        a_cls = " winner" if sum(1 for w in winner_by.values() if w == "a") > sum(1 for w in winner_by.values() if w == "b") else ""
        b_cls = " winner" if sum(1 for w in winner_by.values() if w == "b") > sum(1 for w in winner_by.values() if w == "a") else ""
        parts.append(f'<div><h4>Config A ({time_a:.2f}s)</h4><div class="answer-box{a_cls}">{ans_a}</div></div>')
        parts.append(f'<div><h4>Config B ({time_b:.2f}s)</h4><div class="answer-box box-b{b_cls}">{ans_b}</div></div>')
        parts.append('</div>')

        # Per-question metrics table
        q_metrics = sorted(set(list(ragas_a.keys()) + list(ragas_b.keys())))
        if q_metrics:
            parts.append('<table style="margin-top:10px;"><tr><th>Metric</th><th>A</th><th>B</th><th>Winner</th></tr>')
            for m in q_metrics:
                sa = ragas_a.get(m, float("nan"))
                sb = ragas_b.get(m, float("nan"))
                w = winner_by.get(m, "tie")
                w_class = "win-a" if w == "a" else "win-b" if w == "b" else "tie"
                w_text = "A" if w == "a" else "B" if w == "b" else "Tie"
                parts.append(f'<tr><td>{html.escape(m.replace("_", " ").title())}</td><td>{sa:.4f}</td><td>{sb:.4f}</td><td class="{w_class}">{w_text}</td></tr>')
            parts.append('</table>')

        parts.append('</div>')

    parts.append('</body></html>')
    return '\n'.join(parts)


def main():
    parser = argparse.ArgumentParser(
        description='Compare expected vs actual outputs from archi benchmarking',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate HTML report to default results.html
  python generate_benchmark_report.py results.json
  
  # View specific question
  python generate_benchmark_report.py results.json --question 3
  
  # Save HTML report to specific path
  python generate_benchmark_report.py results.json --html report.html
        """
    )
    
    parser.add_argument('results_file', help='Path to benchmark results JSON file')
    parser.add_argument('--html_output', help='Generate HTML output file')
    parser.add_argument('--question', '-q', type=int, help='Show only specific question number')
    
    args = parser.parse_args()
    
    # Validate input file
    if not Path(args.results_file).exists():
        print(f"Error: File '{args.results_file}' not found", file=sys.stderr)
        sys.exit(1)
    
    if not args.html_output:
        print(f"HTML output path not found, using default.")
        html_path = Path(args.results_file).stem + '.html'
    else:
        html_path = args.html_output

    # Load results
    try:
        results, metadata = load_benchmark_results(args.results_file)
        config_data, config_name, timestamp, questions, total_results = parse_benchmark_results(results, metadata)
    except Exception as e:
        print(f"Error loading results: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Generates HTML output
    html_content = format_html_output(config_data, config_name, timestamp,questions, total_results)
    with open(html_path, 'w') as f:
        f.write(html_content)
    print(f"✅ HTML report generated: {args.html}")


if __name__ == '__main__':
    main()
