import json, os
D = os.path.expanduser('~/ansible/roles/monitoring/files/dashboards')
ref = json.load(open(D + '/immich.json'))
prom = {"type": "prometheus", "uid": "prometheus"}
GF = {"type": "yesoreyeram-infinity-datasource", "uid": "grafana-api"}
HC = {"type": "yesoreyeram-infinity-datasource", "uid": "healthchecks-api"}
RULES_URL = "http://localhost:3000/api/prometheus/grafana/api/v1/rules"
# alert state history (annotations backend) within the dashboard's time range
HISTORY_URL = "http://localhost:3000/api/annotations?type=alert&limit=2000&from=${__from}&to=${__to}"
HC_URL = "https://healthchecks.io/api/v3/checks/"

# --- JSONata (Infinity backend parser) -------------------------------------------------
# Infinity only treats a selector reliably as JSONata when it is a ( ... ) block, and its Go
# engine is an older language level: no $distinct, no regex; lambdas with named parameters
# instead of relying on what $ means inside an object.
# every alert instance of every rule, the Watchdog left out (it has its own tile)
INSTANCES = 'data.groups.rules.alerts[labels.alertname != "Watchdog"]'
# the labels that name what an alert is about (history entries carry them as "k=v, k=v" text)
AFFECTED_LABELS = '["image", "service", "device", "exported_instance", "name"]'
NOW = ('( $a := ' + INSTANCES + '[$not($contains(state, "Normal"))]; $map($a, function($x){ { '
       '"state": $lowercase($substringBefore($x.state & " ", " ")), "severity": $x.labels.severity, '
       '"rule": $x.labels.alertname, '
       '"affected": [$x.labels.image, $x.labels.service, $x.labels.device, $x.labels.exported_instance, $x.labels.name][0], '
       '"message": $x.annotations.summary, "since": $toMillis($x.activeAt) } }) )')
# (a path expression, not a block: Infinity flattens it into one row per rule)
RULES = ('data.groups.( $i := interval; rules.{ "rule": name, "severity": labels.severity, '
         '"state": state = "inactive" ? "normal" : state, "health": health, "every": $string($i) & " s", '
         '"last": $toMillis(lastEvaluation), "next": $toMillis(lastEvaluation) + $i * 1000, '
         '"error": lastError } )')
HISTORY = ('( $a := *[alertName != "Watchdog"]; $map($a, function($e){ { "time": $e.time, "rule": $e.alertName, '
           '"affected": $join($map($filter($split($substringBefore($substringAfter($e.text, "{"), "}"), ", "), '
           'function($l){ $substringBefore($l, "=") in ' + AFFECTED_LABELS + ' }), '
           'function($l){ $substringAfter($l, "=") }), " "), '
           '"from": $lowercase($e.prevState), "to": $lowercase($e.newState) } }) )')
LAST_FIRED = ('( $f := *[newState = "Alerting" and alertName != "Watchdog"]; '
              '$last := $f{alertName: $max(time)}; $cnt := $f{alertName: $count(time)}; '
              '$sev := $f{alertName: tags[$substringBefore($, ":") = "severity"]}; '
              '$map($keys($last), function($k){ { "rule": $k, "severity": $substringAfter([$lookup($sev, $k)][0], ":"), '
              '"last fired": $lookup($last, $k), "times": $lookup($cnt, $k) } }) )')
HC_CHECKS = ('( $c := checks; $map($c, function($x){ { "check": $x.name, "status": $x.status, '
             # raw ISO strings, parsed by Infinity ("timestamp" columns): its $toMillis drops the time
             # of healthchecks.io's "+00:00" offsets
             '"last": $x.last_ping, "next": $x.next_ping, '
             '"schedule": $x.schedule ? $x.schedule : ($string($x.timeout / 60) & " min"), '
             '"grace": $string($x.grace / 60) & " min" } }) )')


def count(state, severity=None):
    sel = 'state = "%s"' % state if state == "Alerting" else '$contains(state, "%s")' % state
    if severity:
        sel += ' and labels.severity = "%s"' % severity
    return '( $c := $count(%s[%s]); [{ "n": $c }] )' % (INSTANCES, sel)


def infinity(src, url, jsonata, columns):
    return {"datasource": src, "refId": "A", "type": "json", "source": "url", "format": "table", "parser": "backend",
            "url": url, "url_options": {"method": "GET", "data": ""}, "root_selector": jsonata,
            "columns": [{"selector": c, "text": t, "type": k} for c, t, k in columns]}


def stat(id, title, desc, target, x, w, steps, mappings=None, y=1, h=4, no_value="0", src=GF):
    return {"datasource": src, "description": desc, "id": id, "title": title, "type": "stat",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": mappings or [], "unit": "none",
                                         "noValue": no_value,
                                         "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
            "options": {"colorMode": "background", "graphMode": "none", "justifyMode": "center", "orientation": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "textMode": "value", "wideLayout": True, "showPercentChange": False},
            "targets": [target]}


def n(jsonata):
    return infinity(GF, RULES_URL, jsonata, [("n", "n", "number")])


def steps(color):
    return [{"color": "green", "value": None}, {"color": color, "value": 1}]


def row(id, title, y):
    return {"id": id, "title": title, "type": "row", "collapsed": False, "panels": [],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}


def order(*names):
    return {"id": "organize", "options": {"indexByName": {nm: i for i, nm in enumerate(names)}}}


COLORS = [{"type": "value", "options": {
    "critical": {"color": "red", "index": 0}, "warning": {"color": "orange", "index": 1},
    "info": {"color": "blue", "index": 2},
    "alerting": {"text": "firing", "color": "red", "index": 3}, "firing": {"color": "red", "index": 4},
    "pending": {"color": "yellow", "index": 5}, "normal": {"color": "green", "index": 6},
    "recovering": {"color": "yellow", "index": 7}, "nodata": {"text": "no data", "color": "orange", "index": 8},
    "error": {"color": "red", "index": 9}, "ok": {"color": "green", "index": 10},
    "up": {"color": "green", "index": 11}, "new": {"text": "new (no ping yet)", "color": "text", "index": 12},
    "grace": {"text": "late (grace)", "color": "orange", "index": 13}, "down": {"text": "DOWN", "color": "red", "index": 14},
    "started": {"text": "running", "color": "green", "index": 15}, "paused": {"color": "text", "index": 16}}}]


def colored(*names):
    return [{"matcher": {"id": "byName", "options": nm},
             "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                            {"id": "mappings", "value": COLORS}]} for nm in names]


def when(*names):
    return [{"matcher": {"id": "byName", "options": nm},
             "properties": [{"id": "unit", "value": "dateTimeFromNow"}]} for nm in names]


def table(id, title, desc, src, target, grid, overrides, cols, sort):
    return {"id": id, "title": title, "type": "table", "description": desc, "datasource": src,
            "gridPos": grid,
            "fieldConfig": {"defaults": {"custom": {"align": "left"}}, "overrides": overrides},
            "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [sort]},
            "transformations": [order(*cols)], "targets": [target]}


# healthchecks.io status over time (Prometheus scrape): one number from its four 0/1 metrics
HC_STATUS = ('max by (name) (hc_check_up) + 2 * max by (name) (hc_check_grace) '
             '+ 4 * max by (name) (hc_check_started) + 8 * max by (name) (hc_check_paused)')
HC_MAP = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red", "index": 0}, "1": {"text": "up", "color": "green", "index": 1},
    "2": {"text": "late", "color": "orange", "index": 2}, "3": {"text": "late", "color": "orange", "index": 3},
    "5": {"text": "running", "color": "green", "index": 4}, "7": {"text": "running, late", "color": "orange", "index": 5}}},
    {"type": "range", "options": {"from": 8, "to": 15, "result": {"text": "paused", "color": "text", "index": 6}}}]

panels = [
    row(100, "Now", 0),
    stat(1, "Critical", "Alert instances firing with severity critical (sent immediately, also at night).",
         n(count("Alerting", "critical")), 0, 4, steps("red")),
    stat(2, "Warnings", "Firing with severity warning (held 22-08, sent when the quiet hours end).",
         n(count("Alerting", "warning")), 4, 4, steps("orange")),
    stat(3, "Hints", "Firing daily hints (sent in the morning window).",
         n(count("Alerting", "info")), 8, 4, steps("blue")),
    stat(4, "Pending", "Instances whose condition holds, but not yet for the rule's 'for' duration.",
         n(count("Pending")), 12, 4, steps("yellow")),
    stat(5, "healthchecks.io down", "Checks at healthchecks.io that are down (heartbeat, nightly jobs, watchdog), "
         "seen from the outside. No data = the read-only key is missing or healthchecks.io is unreachable.",
         infinity(HC, HC_URL, '( $c := $count(checks[status = "down"]); [{ "n": $c }] )', [("n", "n", "number")]),
         16, 4, steps("red"), no_value="no data", src=HC),
    stat(6, "Alerting path", "The always-firing Watchdog: firing = Grafana evaluates, Prometheus answers and ntfy "
         "is reachable; it pings healthchecks.io every 5 min. Anything else = the alerting path is broken.",
         n('( $c := $count(data.groups.rules.alerts[labels.alertname = "Watchdog" and state = "Alerting"]); [{ "n": $c }] )'),
         20, 4, [{"color": "red", "value": None}, {"color": "green", "value": 1}],
         mappings=[{"type": "value", "options": {"1": {"text": "alive", "index": 0},
                                                 "0": {"text": "BROKEN", "index": 1}}}], no_value="BROKEN"),
    table(7, "Firing & pending now",
          "Every alert instance that is not normal, with the message it sends and since when.",
          GF, infinity(GF, RULES_URL, NOW, [("state", "state", "string"), ("severity", "severity", "string"),
                                            ("rule", "rule", "string"), ("affected", "affected", "string"),
                                            ("message", "message", "string"), ("since", "since", "timestamp_epoch")]),
          {"h": 9, "w": 24, "x": 0, "y": 5}, colored("state", "severity") + when("since"),
          ["state", "severity", "rule", "affected", "message", "since"], {"displayName": "severity", "desc": False}),
    row(101, "History (time range above)", 14),
    table(8, "Last fired", "When each rule last started firing in the selected time range, and how often.",
          GF, infinity(GF, HISTORY_URL, LAST_FIRED, [("rule", "rule", "string"), ("severity", "severity", "string"),
                                                     ("last fired", "last fired", "timestamp_epoch"),
                                                     ("times", "times", "number")]),
          {"h": 10, "w": 8, "x": 0, "y": 15}, colored("severity") + when("last fired"),
          ["rule", "severity", "last fired", "times"], {"displayName": "last fired", "desc": True}),
    table(9, "State changes", "Every state change of every alert instance in the selected time range (Grafana's "
          "alert state history — the same as the History tab of a rule).",
          GF, infinity(GF, HISTORY_URL, HISTORY, [("time", "time", "timestamp_epoch"), ("rule", "rule", "string"),
                                                  ("affected", "affected", "string"), ("from", "from", "string"),
                                                  ("to", "to", "string")]),
          {"h": 10, "w": 16, "x": 8, "y": 15}, colored("from", "to"),
          ["time", "rule", "affected", "from", "to"], {"displayName": "time", "desc": True}),
    row(102, "healthchecks.io (seen from the outside)", 25),
    table(10, "Checks", "Every check at healthchecks.io: status, last ping, when the next ping is due, schedule and "
          "grace (its API, read-only key).",
          HC, infinity(HC, HC_URL, HC_CHECKS, [("check", "check", "string"), ("status", "status", "string"),
                                               ("last", "last ping", "timestamp"),
                                               ("next", "next ping due", "timestamp"),
                                               ("schedule", "schedule", "string"), ("grace", "grace", "string")]),
          {"h": 9, "w": 12, "x": 0, "y": 26}, colored("status") + when("last ping", "next ping due"),
          ["check", "status", "last ping", "next ping due", "schedule", "grace"], {"displayName": "check", "desc": False}),
    {"datasource": prom, "id": 11, "title": "Check status over time", "type": "state-timeline",
     "description": "up / late / down of every healthchecks.io check (Prometheus scrapes its metrics every minute; "
                    "needs healthchecks_project_uuid).",
     "gridPos": {"h": 9, "w": 12, "x": 12, "y": 26},
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": HC_MAP,
                                  "thresholds": {"mode": "absolute", "steps": [{"color": "transparent", "value": None}]},
                                  "custom": {"fillOpacity": 80, "lineWidth": 0, "spanNulls": False}}, "overrides": []},
     "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8, "alignValue": "left",
                 "legend": {"showLegend": False}, "tooltip": {"mode": "single", "sort": "none"}},
     "targets": [{"datasource": prom, "expr": HC_STATUS, "legendFormat": "{{name}}", "refId": "A",
                  "range": True, "instant": False}]},
    row(103, "Alert rules", 35),
    table(12, "Alert rules", "Every Grafana alert rule: state, how often it is evaluated, when it was last "
          "evaluated and is next, and the error if its query failed.",
          GF, infinity(GF, RULES_URL, RULES, [("rule", "rule", "string"), ("severity", "severity", "string"),
                                              ("state", "state", "string"), ("health", "health", "string"),
                                              ("every", "evaluated every", "string"),
                                              ("last", "last evaluated", "timestamp_epoch"),
                                              ("next", "next evaluation", "timestamp_epoch"),
                                              ("error", "error", "string")]),
          {"h": 14, "w": 24, "x": 0, "y": 36}, colored("severity", "state", "health") + when("last evaluated", "next evaluation"),
          ["rule", "severity", "state", "health", "evaluated every", "last evaluated", "next evaluation", "error"],
          {"displayName": "state", "desc": False}),
]

d = {k: ref[k] for k in ref if k not in ('panels', 'title', 'uid', 'tags', 'description', 'id', 'version', 'time', 'templating', 'links')}
d.update(title="Alerts overview", uid="alerts", tags=["alerting", "ansible"], panels=panels,
         time={"from": "now-7d", "to": "now"}, templating={"list": []},
         links=[{"title": "Alert rules", "url": "/alerting/list", "type": "link", "icon": "bolt"},
                {"title": "healthchecks.io", "url": "https://healthchecks.io/projects/", "type": "link",
                 "icon": "external link", "targetBlank": True}],
         description="Grafana alerts (now, history, every rule) and the healthchecks.io checks in one place.")
d['refresh'] = '1m'
d['version'] = 10   # bump when the file must win over Grafana's stored copy (e.g. after moving folders)
with open(D + '/alerts.json', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
print('dashboard ok', len(panels), 'panels')
