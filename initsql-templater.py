#!/usr/bin/env python3
import os
from jinja2 import Environment, FileSystemLoader

# Configuration from environment
grafana_pg_password = os.getenv("GRAFANA_PG_PASSWORD", "")
use_grafana = bool(grafana_pg_password)

# Template the init.sql file
env = Environment(loader=FileSystemLoader("a2rchi/templates"))
init_sql_template = env.get_template("base-init.sql")
init_sql = init_sql_template.render({
    "use_grafana": use_grafana,
    "grafana_pg_password": grafana_pg_password,
})

with open("init.sql", 'w') as f:
    f.write(init_sql)

print(f"Created init.sql (grafana: {use_grafana})")
