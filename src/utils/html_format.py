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
    python compare_benchmark_outputs.py <results.json>
    python compare_benchmark_outputs.py <results.json> --html output.html
    python compare_benchmark_outputs.py <results.json> --question 1
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

def parse_benchmark_results(data):
    """Parse benchmark results JSON"""
    
    # Extract timestamp from either legacy or new metadata location
    timestamp = data.get('time') or data.get('metadata', {}).get('time', 'Unknown')
    
    config_data = None
    config_name = None
    
    # New schema: benchmarking_results is a list of result objects
    benchmarking_results = data.get('benchmarking_results')
    if isinstance(benchmarking_results, list):
        for entry in benchmarking_results:
            if not isinstance(entry, dict):
                continue
            if any(key in entry for key in ('single_question_results', 'single question results')):
                config_data = entry
                config_name = (
                    entry.get('configuration', {}).get('name')
                    or entry.get('configuration_file')
                    or entry.get('configuration_name')
                )
                break
    
    # Legacy schema: look for dict keys containing "benchmarking"
    if not config_data:
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            if 'benchmarking' in key and any(
                result_key in value for result_key in ('single question results', 'single_question_results')
            ):
                config_data = value
                config_name = key
                break
    
    if not config_data:
        raise ValueError("No benchmarking results found in JSON file")
    
    if not config_name:
        config_name = 'benchmarking_results'
    
    questions = get_single_question_results(config_data)
    
    # Try to find and load the queries file to get expected links and sources
    config_section = (
        config_data.get('configuration used')
        or config_data.get('configuration')
        or {}
    )
    queries_path = (
        config_section
        .get('services', {})
        .get('benchmarking', {})
        .get('queries_path')
    )
    expected_links = {}
    expected_sources_map = {}
    
    if queries_path:
        queries_file = Path(queries_path)
        if queries_file.exists():
            try:
                with open(queries_file, 'r') as f:
                    queries_data = json.load(f)
                    for q in queries_data:
                        question_text = q.get('question', '')
                        if 'link' in q:
                            expected_links[question_text] = q.get('link', '')
                        if 'sources' in q:
                            expected_sources_map[question_text] = q.get('sources', [])
            except:
                pass
    
    # Add expected links and sources to question results
    for q_data in questions.values():
        question_text = q_data.get('question', '')
        if question_text in expected_links:
            q_data['expected_link'] = expected_links[question_text]
        if question_text in expected_sources_map:
            q_data['expected_sources'] = expected_sources_map[question_text]
        
        # Normalise field names between legacy and new schemas
        if 'chat_answer' not in q_data and 'answer' in q_data:
            q_data['chat_answer'] = q_data['answer']
        if 'ground_truth' not in q_data and 'reference_answer' in q_data:
            q_data['ground_truth'] = q_data['reference_answer']
        
        if 'contexts' not in q_data:
            contexts = []
            trunc_contents = q_data.get('sources_trunc_content') or []
            sources_metadata = q_data.get('sources_metadata') or []
            max_len = max(len(trunc_contents), len(sources_metadata))
            if max_len > 0:
                for idx in range(max_len):
                    meta = sources_metadata[idx] if idx < len(sources_metadata) else {}
                    content = trunc_contents[idx] if idx < len(trunc_contents) else ''
                    meta_parts = []
                    if meta:
                        display_name = meta.get('display_name') or meta.get('ticket_id') or meta.get('url')
                        if display_name:
                            meta_parts.append(str(display_name))
                        ticket_id = meta.get('ticket_id')
                        if ticket_id and ticket_id not in meta_parts:
                            meta_parts.append(ticket_id)
                    combined_parts = []
                    if meta_parts:
                        combined_parts.append(" | ".join(meta_parts))
                    if content:
                        combined_parts.append(content)
                    if combined_parts:
                        contexts.append("\n".join(combined_parts))
                if contexts:
                    q_data['contexts'] = contexts
        if 'document_scores' not in q_data and 'sources_metadata' in q_data:
            # If similarity scores exist in metadata, surface them
            scores = []
            for meta in q_data.get('sources_metadata', []):
                score = meta.get('score') or meta.get('similarity') or meta.get('distance')
                if score is not None:
                    try:
                        scores.append(float(score))
                    except (TypeError, ValueError):
                        continue
            if scores:
                q_data['document_scores'] = scores
    
    return config_data, config_name, timestamp


def extract_ticket_id(text):
    """Extract JIRA ticket ID from various formats"""
    import re
    # Match patterns like CMSTRANSF-1078, jira_CMSTRANSF-1078, jira_CMSTRANSF-1078.txt
    patterns = [
        r'(\w+-\d+)',
        r'(\w+-\d+)',
        r'jira_(\w+-\d+)',
        r'jira_(\w+-\d+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex == 1 else match.group(1).replace('jira_', '')
    return None

def calculate_retrieval_accuracy(questions):
    """Calculate how many questions retrieved the correct document(s)"""
    total = 0
    correct = 0
    partial = 0
    
    for q_data in questions.values():
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

        #expected_link = q_data.get('expected_link', '')
        #expected_sources = q_data.get('expected_sources', [])
        contexts = q_data.get('contexts', [])
        
        # Get expected tickets from both formats
        expected_tickets = set()
        if expected_sources:
            expected_tickets.update(expected_sources)
        #elif expected_link:
        #    ticket = extract_ticket_id(expected_link)
        #    if ticket:
        #        expected_tickets.add(ticket)
        
        if not expected_tickets:
            continue
            
        total += 1
        
        # Get retrieved tickets
        retrieved_tickets = set()
        for ctx in contexts:
            retrieved_ticket = extract_ticket_id(str(ctx))
            if retrieved_ticket:
                retrieved_tickets.add(retrieved_ticket)
        
        # Check how many expected tickets were retrieved
        found_tickets = expected_tickets & retrieved_tickets
        if len(found_tickets) == len(expected_tickets):
            correct += 1
        elif len(found_tickets) > 0:
            partial += 1
    
    accuracy = (correct / total * 100) if total > 0 else 0
    return accuracy, correct, total, partial



def format_html_output(config_data, config_name, timestamp):
    """Format results as HTML for easier reading"""
    questions = get_single_question_results(config_data)
    total_results = get_total_results(config_data)


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

    # Retrieval Accuracy
    ret_accuracy, ret_correct, ret_total, ret_partial = calculate_retrieval_accuracy(questions)
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
        html_parts.append(f'<div class="answer-box">{q_data.get("chat_answer", "N/A")}</div>')
        html_parts.append(f'</div>')
        
        # Expected Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">✅ Expected Answer</div>')
        html_parts.append(f'<div class="answer-box expected-box">{q_data.get("ground_truth", "N/A")}</div>')
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
                ticket_id = extract_ticket_id(str(ctx))
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
            

        # RAGAS Metrics
        ragas_metrics = {
            'answer_relevancy': 'Answer Relevancy',
            'faithfulness': 'Faithfulness',
            'context_precision': 'Context Precision',
            'context_recall': 'Context Recall'
        }

        has_ragas = any(metric in q_data for metric in ragas_metrics.keys())

        if has_ragas:
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
