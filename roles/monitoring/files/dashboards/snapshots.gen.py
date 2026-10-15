import json, os
D = os.path.expanduser('~/ansible/roles/monitoring/files/dashboards')
ref = json.load(open(D + '/immich.json'))
ds = {"type": "prometheus", "uid": "prometheus"}
CFG = 'config=~"$config"'


def stat(id, title, desc, expr, x, w, unit="none", steps=None, y=0, h=4, decimals=None):
    d = {"color": {"mode": "thresholds"}, "mappings": [], "unit": unit,
         "thresholds": {"mode": "absolute", "steps": steps or [{"color": "text", "value": None}]}}
    if decimals is not None:
        d["decimals"] = decimals
    return {"datasource": ds, "description": desc, "id": id, "title": title, "type": "stat",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "center", "orientation": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "textMode": "value", "wideLayout": True, "showPercentChange": False},
            "targets": [{"datasource": ds, "expr": expr, "refId": "A", "instant": True}]}


def bars(id, title, desc, expr, x, y, w, h, color, legend):
    return {"datasource": ds, "description": desc, "id": id, "title": title, "type": "bargauge",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": color}, "unit": "bytes", "min": 0,
                                         "thresholds": {"mode": "absolute", "steps": [{"color": color, "value": None}]}},
                            "overrides": []},
            "options": {"displayMode": "basic", "orientation": "horizontal", "namePlacement": "left", "showUnfilled": True,
                        "valueMode": "text", "sizing": "manual", "minVizHeight": 18, "maxVizHeight": 26,
                        "text": {"titleSize": 12, "valueSize": 12},
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "targets": [{"datasource": ds, "expr": expr, "refId": "A", "instant": True, "legendFormat": legend}]}


KEY = 'label_join(%s, "key", " #", "config", "number")'
ORANGE = [{"color": "text", "value": None}, {"color": "orange", "value": 1}]

panels = [
    stat(1, "Snapshots", "Snapshots of the selected snapper configs.",
         'sum(snapshot_count{%s})' % CFG, 0, 3),
    stat(2, "Held by snapshots only",
         "Data that ONLY snapshots still reference (all volumes together): what deleting every snapshot would "
         "free. Estimated by sampling, like all sizes here.",
         'max(snapshot_held_bytes)', 3, 5, unit="bytes", decimals=1),
    stat(3, "… of the data in use", "Share of the filesystem's data that only snapshots hold.",
         'max(snapshot_held_bytes) / max(snapshot_data_used_bytes)', 8, 4, unit="percentunit", decimals=0),
    stat(4, "Free on /", "Free space of the filesystem all snapshots live on.",
         'node_filesystem_avail_bytes{mountpoint="/",fstype="btrfs"}', 12, 3, unit="bytes", decimals=1,
         steps=[{"color": "red", "value": None}, {"color": "orange", "value": 15e9}, {"color": "green", "value": 30e9}]),
    stat(5, "Estimate resolution",
         "One sample stands for this much data: sizes are estimates in steps of roughly this size, so small "
         "values are rough and anything below it can show as 0. Raise monitoring_snapshot_metrics_samples for "
         "finer values (longer run).",
         'max(snapshot_sample_bytes)', 15, 3, unit="bytes", decimals=1),
    stat(6, "Sizes measured (age)", "When the collector (snapshot-metrics, nightly) last finished a pass.",
         'time() - max(snapshot_metrics_timestamp_seconds)', 18, 3, unit="s",
         steps=[{"color": "green", "value": None}, {"color": "orange", "value": 129600}]),
    {"id": 20, "title": "", "type": "canvas", "datasource": ds,
     "description": "Starts snapshot-metrics now (POST /hooks/snapshot-measure -> webhook on the host). It measures what "
                    "all sizes again by sampling, at idle priority — several minutes. 'Sizes measured (age)' drops "
                    "when it is done. Only one run at a time.",
     "gridPos": {"h": 4, "w": 3, "x": 21, "y": 0},
     "fieldConfig": {"defaults": {}, "overrides": []},
     "options": {"inlineEditing": False, "showAdvancedTypes": True, "panZoom": False, "infinitePan": False,
                 "root": {"type": "frame", "name": "root",
                          "background": {"color": {"fixed": "transparent"}}, "border": {"color": {"fixed": "dark-green"}},
                          "constraint": {"horizontal": "left", "vertical": "top"},
                          "placement": {"top": 0, "left": 0, "width": 100, "height": 100},
                          "elements": [{
                              "type": "button", "name": "Measure now",
                              "config": {"text": {"mode": "fixed", "fixed": "Measure now"}, "align": "center",
                                         "color": {"fixed": "#F0F4FD"}, "size": 14,
                                         "api": {"endpoint": "/hooks/snapshot-measure", "method": "POST",
                                                 "contentType": "application/json", "data": "{}",
                                                 "headerParams": [], "queryParams": []},
                                         "style": {"variant": "primary"}},
                              "background": {"color": {"fixed": "transparent"}},
                              "border": {"color": {"fixed": "dark-green"}},
                              "constraint": {"horizontal": "scale", "vertical": "scale"},
                              "placement": {"top": 15, "bottom": 15, "left": 6, "right": 6}}]}},
     "targets": [{"datasource": ds, "expr": "vector(1)", "refId": "A", "instant": True}]},

    bars(7, "Freed by deleting ONE snapshot — top 20",
         "What only this one snapshot still references. Often small even when the snapshots together hold a lot: "
         "data a snapshot shares with its neighbours is freed only when ALL of them are gone, and shows up in none "
         "of these single values. See the panels on the right for whole sets.",
         'topk(20, %s)' % (KEY % ('snapshot_exclusive_bytes{%s}' % CFG)), 0, 4, 12, 18, "orange", "{{key}}"),
    bars(8, "Freed by deleting ALL snapshots of a volume",
         "What the volume's snapshots hold together that its live data no longer has — the full price of the "
         "history. ('root' = the OS volume; it also holds the Docker images, so every image upgrade leaves the old "
         "layers in the older root snapshots.)",
         'sort_desc(snapshot_set_exclusive_bytes{%s,set="all"})' % CFG, 12, 4, 12, 6, "blue", "{{config}}"),
    bars(9, "Freed by deleting all automatic snapshots except the newest",
         "Keeps the permanent ones (deploy checkpoints, 'keep') and the newest automatic snapshot as the way back, "
         "deletes the rest of the timeline / package-run history.",
         'sort_desc(snapshot_set_exclusive_bytes{%s,set="auto_but_newest"})' % CFG, 12, 10, 12, 6, "orange", "{{config}}"),
    bars(13, "Freed by deleting automatic snapshots older than 7 days",
         "What a shorter retention would bring: the automatic snapshots older than a week, permanent ones kept.",
         'sort_desc(snapshot_set_exclusive_bytes{%s,set="auto_older_7d"})' % CFG, 12, 16, 12, 6, "orange", "{{config}}"),

    {"datasource": ds, "id": 10, "title": "All snapshots", "type": "table",
     "description": "One row per snapshot. 'Freed if deleted' = what only this snapshot still references "
                    "(estimate). A small value does NOT mean the snapshot is cheap to keep — data it shares with "
                    "neighbouring snapshots is freed only when all of them are gone; the set panels above show that. Cleanup: number/timeline = removed automatically by snapper's retention; none = "
                    "kept until deleted by hand. Click a volume name to open the snapshot page of the on-host "
                    "portal (delete / keep / roll back; needs roles/local_upgrade).",
     "gridPos": {"h": 18, "w": 24, "x": 0, "y": 22},
     "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}}, "overrides": [
         {"matcher": {"id": "byName", "options": "Freed if deleted"}, "properties": [
             {"id": "unit", "value": "bytes"}, {"id": "decimals", "value": 1}, {"id": "custom.width", "value": 150},
             {"id": "custom.cellOptions", "value": {"type": "color-text"}},
             {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                 {"color": "text", "value": None}, {"color": "orange", "value": 1e9}, {"color": "red", "value": 5e9}]}}]},
         {"matcher": {"id": "byName", "options": "References"}, "properties": [
             {"id": "unit", "value": "bytes"}, {"id": "decimals", "value": 1}, {"id": "custom.width", "value": 120}]},
         {"matcher": {"id": "byName", "options": "Taken"}, "properties": [
             {"id": "unit", "value": "dateTimeAsIso"}, {"id": "custom.width", "value": 170}]},
         {"matcher": {"id": "byName", "options": "Age"}, "properties": [
             {"id": "unit", "value": "d"}, {"id": "decimals", "value": 1}, {"id": "custom.width", "value": 80}]},
         {"matcher": {"id": "byName", "options": "Volume"}, "properties": [
             {"id": "custom.width", "value": 140},
             {"id": "links", "value": [{"title": "Snapshot page", "targetBlank": True, "url": "/hooks/snapshots"}]}]},
         {"matcher": {"id": "byName", "options": "#"}, "properties": [{"id": "custom.width", "value": 60}]},
         {"matcher": {"id": "byName", "options": "Type"}, "properties": [{"id": "custom.width", "value": 80}]},
         {"matcher": {"id": "byName", "options": "Cleanup"}, "properties": [{"id": "custom.width", "value": 100}]}]},
     "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "Freed if deleted", "desc": True}]},
     "transformations": [
         {"id": "joinByField", "options": {"byField": "key", "mode": "outer"}},
         {"id": "organize", "options": {
             "excludeByName": {k: True for k in ("Time", "Time 1", "Time 2", "Time 3", "Time 4", "key")},
             "renameByName": {"config": "Volume", "number": "#", "type": "Type", "cleanup": "Cleanup",
                              "description": "Description", "Value #A": "Taken", "Value #D": "Age",
                              "Value #B": "Freed if deleted", "Value #C": "References"},
             "indexByName": {"config": 0, "number": 1, "Value #A": 2, "Value #D": 3, "type": 4, "cleanup": 5,
                             "description": 6, "Value #B": 7, "Value #C": 8}}}],
     "targets": [{"datasource": ds, "expr": e, "format": "table", "instant": True, "refId": r} for r, e in (
         ("A", 'max by (key, config, number, type, cleanup, description) (%s) * 1000'
               % (KEY % ('snapshot_created_timestamp_seconds{%s}' % CFG))),
         ("B", 'max by (key) (%s)' % (KEY % ('snapshot_exclusive_bytes{%s}' % CFG))),
         ("C", 'max by (key) (%s)' % (KEY % ('snapshot_referenced_bytes{%s}' % CFG))),
         ("D", '(time() - max by (key) (%s)) / 86400' % (KEY % ('snapshot_created_timestamp_seconds{%s}' % CFG))))]},

    {"datasource": ds, "id": 11, "title": "Snapshots per volume over time", "type": "timeseries",
     "description": "Number of snapshots per config — rises with apt runs / upgrades / the daily timeline, falls "
                    "when snapper's cleanup removes old ones.",
     "gridPos": {"h": 8, "w": 12, "x": 0, "y": 40},
     "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "unit": "none", "min": 0,
                                  "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "showPoints": "never",
                                             "lineInterpolation": "stepAfter", "spanNulls": True}}, "overrides": []},
     "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "desc"}},
     "targets": [{"datasource": ds, "expr": 'snapshot_count{%s}' % CFG, "legendFormat": "{{config}}", "refId": "A"}]},
    {"datasource": ds, "id": 12, "title": "Space held by the snapshots of a volume over time", "type": "timeseries",
     "description": "What all snapshots of a volume hold together. A step up after an upgrade is the old version's "
                    "data that only the snapshots still reference; it falls when the cleanup removes them.",
     "gridPos": {"h": 8, "w": 12, "x": 12, "y": 40},
     "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "unit": "bytes", "min": 0,
                                  "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10, "showPoints": "never",
                                             "spanNulls": True}}, "overrides": []},
     "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "desc"}},
     "targets": [{"datasource": ds, "expr": 'snapshot_set_exclusive_bytes{%s,set="all"}' % CFG,
                  "legendFormat": "{{config}}", "refId": "A"}]},
]

d = {k: ref[k] for k in ref if k not in ('panels', 'title', 'uid', 'tags', 'description', 'id', 'version', 'time', 'templating', 'links')}
d.update(title="Disk snapshots (btrfs)", uid="snapshots", tags=["storage", "ansible"], panels=panels,
         time={"from": "now-30d", "to": "now"},
         links=[{"title": "Snapshot page (delete / keep / roll back)", "url": "/hooks/snapshots", "type": "link",
                 "icon": "external link", "targetBlank": True}],
         templating={"list": [{"name": "config", "label": "volume", "type": "query", "datasource": ds,
                               "definition": "label_values(snapshot_count, config)",
                               "query": {"query": "label_values(snapshot_count, config)", "refId": "A"},
                               "multi": True, "includeAll": True, "allValue": ".*",
                               "current": {"text": "All", "value": "$__all"}, "refresh": 2, "sort": 1}]},
         description="btrfs snapshots (snapper) per volume: age, retention, and how much space deleting one would free.")
d['refresh'] = '5m'
with open(D + '/snapshots.json', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
print('dashboard ok', len(panels), 'panels')
