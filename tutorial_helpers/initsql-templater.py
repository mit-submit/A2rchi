#!/usr/bin/env python3
import os
import shutil
from jinja2 import Environment, FileSystemLoader

# Configuration from environment
grafana_pg_password = os.getenv("GRAFANA_PG_PASSWORD", "")
use_grafana = bool(grafana_pg_password)

# Set up Jinja2 environment
env = Environment(loader=FileSystemLoader("a2rchi/templates"))

# Template the init.sql file
init_sql_template = env.get_template("base-init.sql")
init_sql = init_sql_template.render({
    "use_grafana": use_grafana,
    "grafana_pg_password": grafana_pg_password,
})

with open("init.sql", 'w') as f:
    f.write(init_sql)

print(f"Created init.sql (grafana: {use_grafana})")

# Create grafana directory in current directory
os.makedirs("grafana", exist_ok=True)

if use_grafana:
    # Template datasources.yaml
    datasources_template = env.get_template("grafana/datasources.yaml")
    datasources_yaml = datasources_template.render({
        "grafana_pg_password": grafana_pg_password,
    })
    
    with open("grafana/datasources.yaml", 'w') as f:
        f.write(datasources_yaml)
    
    print("Created grafana/datasources.yaml")
else:
    # Copy datasources.yaml without templating (fallback)
    shutil.copy("a2rchi/templates/grafana/datasources.yaml", "grafana/")
    print("Copied grafana/datasources.yaml (no templating)")

# Copy other grafana files (these don't need templating)
grafana_files = [
    "a2rchi-default-dashboard.json",
    "dashboards.yaml", 
    "grafana.ini"
]

for file in grafana_files:
    src = f"a2rchi/templates/grafana/{file}"
    dst = f"grafana/{file}"
    if os.path.exists(src):
        shutil.copy(src, dst)
        print(f"Copied grafana/{file}")
    else:
        print(f"Warning: {src} not found, skipping")
