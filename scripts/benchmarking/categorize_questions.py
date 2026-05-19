#!/usr/bin/env python3
import json, re, sys
from collections import Counter

CATEGORIES = ['factual_lookup','debugging','data_query','jira_investigation','procedural','exploratory']

LOG_PAT = re.compile(
    r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|Traceback|Exception|INFO:|ERROR:|WARNING:|'
    r'exit.?code|ExternalLHEProducer|SCRAMScript|WMException|WMTaskSpace|cmsRun\d|'
    r'FrameworkJobReport|FrameworkError', re.I)

JIRA_PAT = re.compile(
    r'its\.cern\.ch/jira|CMS(?:PROD|TRANSF|DM|TZ|HLT|COMPPR)-\d+|'
    r'cmstransf-\d+|cmsprod-\d+|cmsdm-\d+|cmstz-\d+', re.I)

DQ_PAT = re.compile(
    r'last\s+\d+\s*(?:hour|day|min|week|h\b)|in the last|failure.?rate|CPU.?efficiency|'
    r'how much data (?:sent|transfer|written|delet)|total amount of data|random FTS|'
    r'opensearch|monit.?tool|(?:transfer|deletion).?fail(?:ure|ed|ing)|number of core|'
    r'rucio.*(?:event|rule.*fail)|(?:recently|recent)\s+(?:fail|stuck)|'
    r'how many.*(?:delet|transfer)|most (?:common|amount).*(?:error|transfer|fail)|'
    r'(?:highest|lowest) failure|evolving over time|success and fail|available space', re.I)

PROC_PAT = re.compile(
    r'^how (?:to|can I|do I)|give me the (?:command|rucio command|query|das query)|'
    r'procedure to|what is the command|'
    r'how I can (?:run|deploy|submit|read|set|find|check|create|stage)|'
    r'^where (?:do I|can I) find|give me (?:the )?(?:rucio|das)', re.I|re.M)

EXPL_PAT = re.compile(
    r'^summarize|^summary|deep analysis|create a (?:plan|skill)|'
    r'most (?:repeated|relevant) topic|what are the (?:main|current)|'
    r'what do you know about|explain (?:in detail|what|how)|weekly report|'
    r'please explain|overview|research what|provide a description|'
    r'give me a list of|keeping track of chat|working on my previous|laser tag', re.I|re.M)


def classify(q):
    ql = q.lower()
    n = len(q)
    lm = len(LOG_PAT.findall(q))
    if lm >= 3 and n > 300:
        return 'debugging'
    if re.search(r'analyze this log|full log|full error|compare error', ql):
        return 'debugging'
    if n > 800 and lm >= 2:
        return 'debugging'
    if JIRA_PAT.search(q):
        if lm >= 3:
            return 'debugging'
        return 'jira_investigation'
    if re.search(r'latest (?:prod |P&R )?(?:jira )?ticket', ql):
        return 'jira_investigation'
    if re.search(r'latest.*ticket.*(?:site|issue)', ql):
        return 'jira_investigation'
    if re.search(r'jira ticket', ql) and not re.search(r'how|what is', ql):
        return 'jira_investigation'
    if DQ_PAT.search(q):
        return 'data_query'
    if PROC_PAT.search(q):
        return 'procedural'
    if EXPL_PAT.search(q):
        return 'exploratory'
    return 'factual_lookup'


def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else 'configs/submit76/curated_questions.json'
    out = sys.argv[2] if len(sys.argv) > 2 else inp.replace('.json', '_categorized.json')
    with open(inp) as f:
        qs = json.load(f)
    print(f'Classifying {len(qs)} questions...')
    res = []
    for i, q in enumerate(qs):
        cat = classify(q['question'])
        mt = bool(q.get('history'))
        q['category'] = cat
        q['multi_turn'] = mt
        res.append((i+1, cat, mt))
        h = 'H' if mt else ' '
        txt = q['question'][:100].replace('\n', ' ')
        print(f'  {i+1:3d} {h} {cat:<20s} {txt}')
    cc = Counter(r[1] for r in res)
    mc = sum(1 for r in res if r[2])
    print()
    print('Category distribution:')
    for c in CATEGORIES:
        print(f'  {c}: {cc.get(c,0)}')
    print(f'  multi_turn: {mc}/{len(qs)}')
    with open(out, 'w') as f:
        json.dump(qs, f, indent=2)
    print(f'Written to {out}')


if __name__ == '__main__':
    main()
