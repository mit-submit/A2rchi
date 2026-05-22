#!/bin/bash
scp -q /Users/jason/projects/A2rchi/.scratch/trace_analysis.py orcd-login:trace_analysis.py
ssh orcd-login '/orcd/data/submit/001/mohoney/conda/envs/archi/bin/python ~/trace_analysis.py 2>&1 || python3 ~/trace_analysis.py'
