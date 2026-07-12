#!/usr/bin/env python3
import json, glob

DATASOURCE = {'type': 'prometheus', 'uid': 'prometheus'}

def fix_obj(obj):
    if isinstance(obj, dict):
        if 'datasource' in obj:
            ds = obj['datasource']
            if isinstance(ds, dict):
                # Old format: {"type": "prometheus", "uid": "..."}
                # New Grafana 13 format: {"name": "${DS_PROMETHEUS}"}
                if (ds.get('type') == 'prometheus' or
                    'uid' in ds or
                    '${DS_' in str(ds.get('name', '')) or
                    'prometheus' in str(ds.get('name', '')).lower()):
                    obj['datasource'] = DATASOURCE
            elif isinstance(ds, str) and ('prometheus' in ds.lower() or '${DS_' in ds):
                obj['datasource'] = DATASOURCE
        for v in list(obj.values()):
            fix_obj(v)
    elif isinstance(obj, list):
        for item in obj:
            fix_obj(item)

for filepath in glob.glob('/opt/monitoring/provisioning/dashboards/json/*.json'):
    with open(filepath, 'r') as f:
        data = json.load(f)
    fix_obj(data)
    data.pop('__inputs', None)
    data.pop('__requires', None)
    data['id'] = None
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Fixed: {filepath}")
