#!/bin/bash
scp -q /Users/jason/projects/A2rchi/.scratch/probe_trace.py orcd-login:probe_trace.py
ssh orcd-login 'python3 ~/probe_trace.py'
