import json, os
D = os.path.expanduser('~/ansible/roles/monitoring/files/dashboards')
ref = json.load(open(D + '/immich.json'))
ds = {"type": "prometheus", "uid": "prometheus"}


def stat(id, title, desc, expr, x, w, unit="none", steps=None, y=0, h=4):
    return {"datasource": ds, "description": desc, "id": id, "title": title, "type": "stat",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": [], "unit": unit,
                                         "thresholds": {"mode": "absolute", "steps": steps or [{"color": "green", "value": None}]}},
                            "overrides": []},
            "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "center", "orientation": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "textMode": "value", "wideLayout": True, "showPercentChange": False},
            "targets": [{"datasource": ds, "expr": expr, "refId": "A", "instant": True}]}


def bars(id, title, desc, expr, x, y, w, h, color):
    return {"datasource": ds, "description": desc, "id": id, "title": title, "type": "bargauge",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": color}, "unit": "none", "min": 0,
                                         "thresholds": {"mode": "absolute", "steps": [{"color": color, "value": None}]}},
                            "overrides": []},
            "options": {"displayMode": "basic", "orientation": "horizontal", "namePlacement": "top", "showUnfilled": True,
                        "valueMode": "text", "sizing": "manual", "minVizHeight": 30, "maxVizHeight": 34,
                        "text": {"titleSize": 13, "valueSize": 13},
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "targets": [{"datasource": ds, "expr": expr, "legendFormat": "{{image}}", "refId": "A", "instant": True}]}


def table(id, title, desc, expr, x, y, w, h, hide, rename, sort, link_col, order, pre=None):
    # expr: one PromQL string, or [(refId, expr), ...] (then `pre` joins the frames)
    ov = [{"matcher": {"id": "byName", "options": link_col}, "properties": [{"id": "links", "value": [
        {"title": "Details (osv.dev)", "url": "https://osv.dev/vulnerability/${__value.raw}", "targetBlank": True}]},
        {"id": "custom.width", "value": 170}]}]
    for col, wd in (("Severity", 100), ("CVSS", 70), ("Images", 90), ("Package", 190), ("Package fixed in", 230), ("Package installed", 190), ("Image", 330)):
        ov.append({"matcher": {"id": "byName", "options": col}, "properties": [{"id": "custom.width", "value": wd}]})
    # "Severity" is the numeric rank label (4/3/2) shown as text: sorts by severity, not alphabetically
    ov.append({"matcher": {"id": "byName", "options": "Severity"}, "properties": [
        {"id": "mappings", "value": [{"type": "value", "options": {
            "4": {"text": "CRITICAL", "color": "red", "index": 0},
            "3": {"text": "HIGH", "color": "orange", "index": 1},
            "2": {"text": "MEDIUM", "color": "yellow", "index": 2},
            "1": {"text": "LOW", "color": "blue", "index": 3}}}]},
        {"id": "custom.cellOptions", "value": {"type": "color-text"}}]})
    ov.append({"matcher": {"id": "byName", "options": "Fixed by"}, "properties": [
        {"id": "mappings", "value": [{"type": "value", "options": {
            "same-major": {"text": "minor update", "color": "green", "index": 0},
            "next-major": {"text": "next major", "color": "orange", "index": 1},
            "latest": {"text": "newest major", "color": "dark-orange", "index": 2},
            "none": {"text": "no update yet", "color": "red", "index": 3},
            "unknown": {"text": "–", "color": "#8e8e8e", "index": 4}}}]},
        {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "custom.width", "value": 130}]})
    ov.append({"matcher": {"id": "byName", "options": "Update to"}, "properties": [{"id": "custom.width", "value": 170}]})
    # "CVSS" is the series value (numeric sort); 0 = no score known
    ov.append({"matcher": {"id": "byName", "options": "CVSS"}, "properties": [
        {"id": "decimals", "value": 1},
        {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "–", "index": 0}}}]}]})
    org = {"excludeByName": {k: True for k in hide}, "renameByName": rename,
           "indexByName": {k: i for i, k in enumerate(order)}}
    sort_by = sort if isinstance(sort, list) else [sort]
    targets = [("A", expr)] if isinstance(expr, str) else expr
    return {"datasource": ds, "description": desc, "id": id, "title": title, "type": "table",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}}, "overrides": ov},
            "options": {"showHeader": True, "cellHeight": "sm",
                        "sortBy": [{"displayName": c, "desc": True} for c in sort_by]},
            "transformations": (pre or []) + [{"id": "organize", "options": org}],
            "targets": [{"datasource": ds, "expr": e, "format": "table", "instant": True, "refId": r}
                        for r, e in targets]}


def row(id, title, y):
    return {"type": "row", "title": title, "id": id, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


F = 'fixable="true"'
RED = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
ORA = [{"color": "green", "value": None}, {"color": "orange", "value": 1}]
BLUE = [{"color": "text", "value": None}, {"color": "blue", "value": 1}]
AGE = [{"color": "green", "value": None}, {"color": "orange", "value": 93600}, {"color": "red", "value": 180000}]

button = {
    "id": 20, "title": "", "type": "canvas", "datasource": ds,
    "description": "Starts trivy-metrics now (POST /hooks/trivy-scan -> webhook on the host). A scan takes a few "
                   "minutes; \"Last scan (age)\" drops to seconds when it is done. Only one scan runs at a time.",
    "gridPos": {"h": 4, "w": 3, "x": 21, "y": 0},  # button
    "fieldConfig": {"defaults": {}, "overrides": []},
    "options": {"inlineEditing": False, "showAdvancedTypes": True, "panZoom": False, "infinitePan": False,
                "root": {"type": "frame", "name": "root",
                         "background": {"color": {"fixed": "transparent"}}, "border": {"color": {"fixed": "dark-green"}},
                         "constraint": {"horizontal": "left", "vertical": "top"},
                         "placement": {"top": 0, "left": 0, "width": 100, "height": 100},
                         "elements": [{
                             "type": "button", "name": "Start new scan",
                             "config": {"text": {"mode": "fixed", "fixed": "Start new scan"}, "align": "center",
                                        "color": {"fixed": "#F0F4FD"}, "size": 14,
                                        "api": {"endpoint": "/hooks/trivy-scan", "method": "POST",
                                                "contentType": "application/json", "data": "{}",
                                                "headerParams": [], "queryParams": []},
                                        "style": {"variant": "primary"}},
                             "background": {"color": {"fixed": "transparent"}},
                             "border": {"color": {"fixed": "dark-green"}},
                             "constraint": {"horizontal": "scale", "vertical": "scale"},
                             "placement": {"top": 15, "bottom": 15, "left": 6, "right": 6}}]}},
    "targets": [{"datasource": ds, "expr": "vector(1)", "refId": "A", "instant": True}]}

REPULL = ('sort_desc((sum by (image) (trivy_image_vulnerabilities{severity="%s"}) - sum by (image) '
          '(trivy_image_update_vulnerabilities{severity="%s"})) and on (image) (trivy_image_update_available == 1))')

KEY = 'sum by (update) (label_join(%s, "update", " | ", "image", "target", "version"))'
NOW = ('sum by (image) (trivy_image_vulnerabilities{severity="%s"}) * on (image) group_right() '
       'trivy_image_newer_version_info')
AFTER = 'sum by (image, target, version) (trivy_image_newer_vulnerabilities{severity="%s"})'

# one tile, three values: DIFFERENT CVEs per severity (the finding counts count per image + package)
DISTINCT = stat(10, "Distinct CVEs",
                "Number of DIFFERENT CVEs per severity across all running images. The finding counts on the left "
                "count a CVE once per image and package it occurs in — this is the number of distinct problems "
                "behind them. MEDIUM is only counted: its findings are not listed in the tables below (thousands of "
                "rows) but in /var/lib/trivy-report/vulnerabilities.tsv on the host.", "", 8, 6)
DISTINCT["targets"] = [{"datasource": ds, "refId": r, "instant": True, "legendFormat": sv,
                        "expr": 'max(trivy_distinct_cves{severity="%s"}) or vector(0)' % sv}
                       for r, sv in (("A", "CRITICAL"), ("B", "HIGH"), ("C", "MEDIUM"))]
DISTINCT["options"]["textMode"] = "value_and_name"
DISTINCT["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": "text"}
DISTINCT["fieldConfig"]["overrides"] = [
    {"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
    for n, c in (("CRITICAL", "red"), ("HIGH", "orange"), ("MEDIUM", "yellow"))]

INFO_KEY = 'label_join(trivy_vulnerability_info{severity=~"$severity"}, "key", "|", "cve", "pkg", "fixed")'

panels = [
    stat(1, "Critical — fix available",
         "CRITICAL FINDINGS (counted per image and package: the same CVE in two images, or in two packages of one "
         "image, counts twice) for which a fixed PACKAGE version exists. Note: that does not "
         "mean a newer IMAGE already ships the fix — see the re-pull row below.",
         'sum(trivy_image_vulnerabilities{severity="CRITICAL",%s}) or vector(0)' % F, 0, 3, steps=RED),
    stat(2, "High — fix available", "HIGH vulnerabilities in running images with a fixed package version available.",
         'sum(trivy_image_vulnerabilities{severity="HIGH",%s}) or vector(0)' % F, 3, 3, steps=ORA),
    stat(3, "Critical/High — no fix yet",
         "Findings without a fixed version upstream: nothing to update yet, just be aware.",
         'sum(trivy_image_vulnerabilities{severity=~"CRITICAL|HIGH",fixable="false"}) or vector(0)', 6, 2,
         steps=[{"color": "text", "value": None}]),
    DISTINCT,
    stat(4, "Images scanned", "Images of running containers scanned in the last run.",
         'count(trivy_image_scan_success == 1) or vector(0)', 14, 2),
    stat(5, "Failed scans",
         "Images the last run could not scan (timeout, memory cap, registry error): journalctl -u trivy-metrics.",
         'count(trivy_image_scan_success == 0) or vector(0)', 16, 2, steps=ORA),
    stat(6, "Last scan (age)",
         "Nightly timer trivy-metrics (and the button on the right). Orange after 26h = a run was missed.",
         'time() - max(trivy_scan_timestamp_seconds)', 18, 3, unit="s", steps=AGE),
    button,
    bars(7, "Fixable CRITICAL per image", "Images with CRITICAL findings whose package has a fix.",
         'sort_desc(sum by (image) (trivy_image_vulnerabilities{severity="CRITICAL",%s}) > 0)' % F, 0, 4, 12, 14, "red"),
    bars(8, "Fixable HIGH per image", "Images with HIGH findings whose package has a fix.",
         'sort_desc(sum by (image) (trivy_image_vulnerabilities{severity="HIGH",%s}) > 0)' % F, 12, 4, 12, 14, "orange"),

    row(30, "Would re-pulling help? (same tag, newer build in the registry)", 18),
    stat(31, "Images with a newer build",
         "Running images whose TAG points to a newer build in the registry (floating tags such as nextcloud:31-apache). "
         "0 = everything is at its tag's current build; pinned version tags normally never change.",
         'count(trivy_image_update_available == 1) or vector(0)', 0, 6, steps=BLUE, y=19, h=5),
    bars(32, "CRITICAL a re-pull would remove",
         "Per image with a newer build: CRITICAL findings now minus those in the registry's build (scanned straight "
         "from the registry, nothing was redeployed). Empty = no newer builds.",
         REPULL % ("CRITICAL", "CRITICAL"), 6, 19, 9, 5, "blue"),
    bars(33, "HIGH a re-pull would remove", "Same for HIGH findings.", REPULL % ("HIGH", "HIGH"), 15, 19, 9, 5, "blue"),

    row(50, "Would a version update help? (newer versions scanned straight from the registry)", 24),
    stat(51, "Images with a newer version",
         "Running images (with an update rule) for which the registry has a newer VERSION tag. same-major = newest "
         "within the running major version (the low-risk update); latest = newest overall when that is another major "
         "(may skip majors — e.g. Nextcloud must be upgraded one major at a time).",
         'count(count by (image) (trivy_image_newer_version_info)) or vector(0)', 0, 4, steps=BLUE, y=25, h=5),
    stat(54, "Registry comparison (age)",
         "When the comparison with the registries (newer builds + newer versions) last completed. It runs at most "
         "once per 12h (in practice: the nightly run) because every run costs registry requests — Docker Hub "
         "rate-limits anonymous IPs. Scans in between (button, after an upgrade) reuse the cached result and only "
         "rescan the local images; a newly running version gets its update targets from the cached tag lists, its "
         "'after' finding counts follow with the next comparison. Orange after 30h = "
         "the registry was unreachable or rate-limited (journalctl -u trivy-metrics).",
         'time() - max(trivy_registry_check_timestamp_seconds > 0)', 0, 4, unit="s",
         steps=[{"color": "green", "value": None}, {"color": "orange", "value": 108000}], y=30, h=5),
    {"datasource": ds, "id": 52, "title": "What an update would change", "type": "table",
     "description": "Per running image and update target: CRITICAL/HIGH findings now vs. in the newer version (that "
                    "version was scanned itself, so this is measured, not guessed). 'after' lower than 'now' = the "
                    "update removes findings; equal = it does not help (yet).",
     "gridPos": {"h": 10, "w": 20, "x": 4, "y": 25},
     "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}},
                     "overrides": [{"matcher": {"id": "byName", "options": "Image | target | new version"},
                                    "properties": [{"id": "custom.width", "value": 620}]}]},
     "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "Critical now", "desc": True}]},
     "transformations": [
         {"id": "joinByField", "options": {"byField": "update", "mode": "outer"}},
         {"id": "organize", "options": {
             "excludeByName": {k: True for k in ("Time", "Time 1", "Time 2", "Time 3", "Time 4")},
             "renameByName": {"update": "Image | target | new version", "Value #A": "Critical now",
                              "Value #B": "Critical after", "Value #C": "High now", "Value #D": "High after"},
             "indexByName": {"update": 0, "Value #A": 1, "Value #B": 2, "Value #C": 3, "Value #D": 4}}}],
     "targets": [{"datasource": ds, "expr": e, "format": "table", "instant": True, "refId": r} for r, e in (
         ("A", KEY % (NOW % "CRITICAL")), ("B", KEY % (AFTER % "CRITICAL")),
         ("C", KEY % (NOW % "HIGH")), ("D", KEY % (AFTER % "HIGH")))]},
    table(53, "Critical CVEs the update would remove (target: $target)",
          "CRITICAL findings of the running image that are NOT present any more in the newer version (pick the "
          "target at the top: same-major or latest). Empty = the update removes no critical finding.",
          'max by (image, cve, pkg, title) ((trivy_vulnerability_info{severity="CRITICAL"} * on (image) group_left() '
          'trivy_image_newer_version_info{target="$target"}) unless on (image, cve, pkg) '
          'trivy_vulnerability_in_newer{target="$target"})', 0, 35, 24, 12,
          ["Time"], {"image": "Image", "cve": "CVE", "Value": "CVSS", "pkg": "Package", "title": "Title"},
          ["CVSS", "Image"], "CVE", ["image", "cve", "Value", "pkg", "title"]),

    row(40, "Vulnerabilities by CVE (severity: $severity)", 47),
    table(41, "CVEs across all images",
          "One row per CVE + package: in how many running images it occurs (severity filter at the top; CVSS = score of the source the severity comes from, v4.0 before v3.x; "
          "score of the source the severity comes from, CVSS 4.0 before 3.x, 0-10; else the highest any source reports). 'Package fixed in' is the version "
          "of the PACKAGE inside the image (e.g. a Debian package) that contains the fix — NOT a version of the "
          "application image; empty = no fix yet. Whether a newer image already ships it is not known here. Click a CVE for details. Full list incl. HIGH + titles on the host: "
          "/var/lib/trivy-report/vulnerabilities.tsv",
          [("A", 'count by (key, cve, rank, pkg, fixed, title) (%s)' % INFO_KEY),     # -> Images
           ("B", 'max by (key) (%s)' % INFO_KEY)],                                     # -> CVSS
          0, 48, 24, 14,
          ["Time", "Time 1", "Time 2", "key"],
          {"cve": "CVE", "rank": "Severity", "Value #B": "CVSS", "Value #A": "Images", "pkg": "Package",
           "fixed": "Package fixed in", "title": "Title"},
          ["Severity", "CVSS", "Images"], "CVE",
          ["cve", "rank", "Value #B", "Value #A", "pkg", "fixed", "title"],
          pre=[{"id": "joinByField", "options": {"byField": "key", "mode": "outer"}}]),
    table(42, "CVEs per image ($image)",
          "Every finding of the selected image(s) and severity — pick both with the filters at the top. 'Fixed by' / "
          "'Update to': which update of the IMAGE removes the finding, measured by scanning the newer versions "
          "(minor update = newest version of the running major; major update = only the newest version overall; "
          "no update yet = still present in every newer version; – = no newer version known for the image). "
          "It is the version to update TO, not the first version that fixed it.",
          'max by (image, cve, rank, fix_target, fix_version, pkg, installed, fixed, title) '
          '(trivy_vulnerability_info{image=~"$image",severity=~"$severity"})', 0, 62, 24, 14,
          ["Time"],
          {"image": "Image", "cve": "CVE", "rank": "Severity", "Value": "CVSS", "fix_target": "Fixed by",
           "fix_version": "Update to", "pkg": "Package",
           "installed": "Package installed", "fixed": "Package fixed in", "title": "Title"},
          ["Severity", "CVSS", "Image"], "CVE",
          ["image", "cve", "rank", "Value", "fix_target", "fix_version", "pkg", "installed", "fixed", "title"]),

    {"datasource": ds,
     "description": "Fixable findings over time — drops when images are updated, rises when new CVEs are published "
                    "for what is running.",
     "id": 9, "title": "Fixable vulnerabilities over time", "type": "timeseries",
     "gridPos": {"h": 9, "w": 24, "x": 0, "y": 76},
     "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"},
                                  "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10,
                                             "showPoints": "never", "spanNulls": True},
                                  "unit": "none", "min": 0},
                     "overrides": [{"matcher": {"id": "byName", "options": "CRITICAL"},
                                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                                   {"matcher": {"id": "byName", "options": "HIGH"},
                                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
                                   {"matcher": {"id": "byName", "options": "MEDIUM"},
                                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]}]},
     "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "desc"}},
     "targets": [{"datasource": ds, "expr": 'sum by (severity) (trivy_image_vulnerabilities{%s})' % F,
                  "legendFormat": "{{severity}}", "refId": "A"}]},
]

# --- top block: one row per severity with three identical tiles (fix available | no fix yet |
# distinct CVEs); scan status + button on the right. Replaces the first-generation tiles above.
TOP_IDS = {1, 2, 3, 10, 4, 5, 6}
rest = [p for p in panels if p.get("id") not in TOP_IDS]
for p in rest:
    if p.get("id") == 20:                       # scan button: full height of the block
        p["gridPos"] = {"h": 6, "w": 3, "x": 21, "y": 0}
    elif p["gridPos"]["y"] >= 4:                # the block is 2 rows taller than before
        p["gridPos"]["y"] += 2
top = []
for base, sev, y, steps in ((60, "CRITICAL", 0, RED), (70, "HIGH", 3, ORA)):
    name = sev.capitalize()
    top += [
        stat(base + 1, name + " — fix available",
             sev + " FINDINGS for which a fixed PACKAGE version exists (counted per image and package: the same "
             "CVE in two images, or in two packages of one image, counts twice). A fixed package does not mean a "
             "newer IMAGE already ships it — see 'Would a version update help?' below.",
             'sum(trivy_image_vulnerabilities{severity="%s",fixable="true"}) or vector(0)' % sev, 0, 5, steps=steps, y=y, h=3),
        stat(base + 2, name + " — no fix yet",
             sev + " findings without a fixed package version upstream: nothing to update yet, just be aware "
             "(a later image release usually picks the fix up).",
             'sum(trivy_image_vulnerabilities{severity="%s",fixable="false"}) or vector(0)' % sev, 5, 5,
             steps=[{"color": "text", "value": None}], y=y, h=3),
        stat(base + 3, name + " — distinct CVEs",
             "Number of DIFFERENT " + sev + " CVEs across all running images (with or without fix) — the two tiles "
             "on the left count a CVE once per image and package, this is the number of distinct problems behind them.",
             'max(trivy_distinct_cves{severity="%s"}) or vector(0)' % sev, 10, 5, steps=steps, y=y, h=3),
    ]
top += [
    stat(4, "Images scanned", "Images of running containers scanned in the last run.",
         'count(trivy_image_scan_success == 1) or vector(0)', 15, 3, y=0, h=3),
    stat(5, "Failed scans",
         "Images the last run could not scan (timeout, memory cap, registry error): journalctl -u trivy-metrics.",
         'count(trivy_image_scan_success == 0) or vector(0)', 18, 3, steps=ORA, y=0, h=3),
    stat(6, "Last scan (age)",
         "Nightly timer trivy-metrics (and the button on the right). Orange after 26h = a run was missed.",
         'time() - max(trivy_scan_timestamp_seconds)', 15, 6, unit="s", steps=AGE, y=3, h=3),
]
panels = top + rest

# --- "Update overview per image": age of the running image + what each update step removes ---
for p in panels:
    if p["gridPos"]["y"] >= 37:
        p["gridPos"]["y"] += 14
PRIO_COLS = [("A", "same-major", ">= 9", "① fixes CVSS 9–10", "red"),
             ("D", "same-major", ">= 7 < 9", "① fixes CVSS 7–8.9", "yellow"),
             ("I", "next-major", ">= 9", "② fixes more CVSS 9–10", "orange"),
             ("J", "next-major", ">= 7 < 9", "② fixes more CVSS 7–8.9", "text"),
             ("B", "latest", ">= 9", "③ fixes more CVSS 9–10", "orange"),
             ("E", "latest", ">= 7 < 9", "③ fixes more CVSS 7–8.9", "text"),
             ("C", "none", ">= 9", "④ stays open CVSS 9–10", "text"),
             ("F", "none", ">= 7 < 9", "④ stays open CVSS 7–8.9", "text")]
VER = 'max by (image, %s) (label_replace(trivy_image_newer_version_info{target="%s"}, "%s", "$1", "version", "(.*)"))'
prio_targets = [(r, 'count by (image) (trivy_vulnerability_info{fix_target="%s"} %s)' % (t, cond))
                for r, t, cond, _, _ in PRIO_COLS]
prio_targets += [("G", VER % ("minor", "same-major", "minor")), ("K", VER % ("nextmajor", "next-major", "nextmajor")),
                 ("H", VER % ("major", "latest", "major")),
                 ("L", 'max by (image) (time() - trivy_image_created_timestamp_seconds) / 86400')]
prio_rename = {"image": "Image", "Value #L": "Image age", "minor": "① Minor update to",
               "nextmajor": "② Next major", "major": "③ Newest major"}
prio_rename.update({"Value #" + r: name for r, _, _, name, _ in PRIO_COLS})
prio_order = ["image", "Value #L", "minor", "Value #A", "Value #D", "nextmajor", "Value #I", "Value #J",
              "major", "Value #B", "Value #E", "Value #C", "Value #F"]
prio_ov = [{"matcher": {"id": "byName", "options": name}, "properties": [
               {"id": "custom.cellOptions", "value": {"type": "color-text"}},
               {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                   {"color": "text", "value": None}, {"color": color, "value": 1}]}},
               {"id": "custom.width", "value": 175}]}
           for _, _, _, name, color in PRIO_COLS]
prio_ov += [{"matcher": {"id": "byName", "options": "Image"}, "properties": [{"id": "custom.width", "value": 330}]}]
# the version cells link to the on-host upgrade page (roles/local_upgrade): /hooks/upgrade on the
# Grafana host redirects to the login-protected page, which asks for confirmation first
prio_ov += [{"matcher": {"id": "byName", "options": n}, "properties": [
                {"id": "custom.width", "value": 150},
                {"id": "links", "value": [{"title": "Upgrade to ${__value.raw} …", "targetBlank": True,
                                           "url": "/hooks/upgrade?image=${__data.fields.Image}&version=${__value.raw}"}]}]}
            for n in ("① Minor update to", "② Next major", "③ Newest major")]
prio_ov.append({"matcher": {"id": "byName", "options": "Image age"}, "properties": [
    {"id": "unit", "value": "d"}, {"id": "decimals", "value": 0}, {"id": "custom.width", "value": 105},
    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
    {"id": "thresholds", "value": {"mode": "absolute", "steps": [
        {"color": "text", "value": None}, {"color": "orange", "value": 90}, {"color": "red", "value": 180}]}}]})
panels.append({
    "datasource": ds, "id": 55, "title": "Update overview per image", "type": "table",
    "description": "Every running image, read left to right. Image age = days since the running image was BUILT: "
                   "orange from 90, red from 180 days — an image that is no longer rebuilt gets no security fixes "
                   "(typical for the tag of an end-of-life major version), whatever the CVE counts say. "
                   "① Minor update (newest version of the running major): the version to go to and how many "
                   "CRITICAL/HIGH findings it removes — red = CVSS 9–10, update soon. ② Next major: how many MORE "
                   "findings only that removes — the realistic next step where majors must not be skipped "
                   "(Nextcloud). ③ Newest major: what going all the way removes on top. ④ What stays open even "
                   "then — nothing to do but know. Counts are findings (per package); findings without a CVSS score "
                   "are in no column. Empty cell = none / no such version. Click a version to upgrade to it from "
                   "the server itself (opens the confirm page; needs on-host upgrades, roles/local_upgrade).",
    "gridPos": {"h": 14, "w": 24, "x": 0, "y": 37},
    "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}}, "overrides": prio_ov},
    "options": {"showHeader": True, "cellHeight": "sm",
                "sortBy": [{"displayName": "① fixes CVSS 9–10", "desc": True},
                           {"displayName": "② fixes more CVSS 9–10", "desc": True},
                           {"displayName": "Image age", "desc": True}]},
    "transformations": [
        {"id": "joinByField", "options": {"byField": "image", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": dict({k: True for k in ["Time"] + ["Time %d" % i for i in range(1, 14)]},
                                  **{"Value #G": True, "Value #K": True, "Value #H": True}),
            "renameByName": prio_rename,
            "indexByName": {k: i for i, k in enumerate(prio_order)}}}],
    "targets": [{"datasource": ds, "expr": e, "format": "table", "instant": True, "refId": r} for r, e in prio_targets]})

# --- results of on-host upgrades (roles/local_upgrade: app-upgrade.prom) ---
for p in panels:
    if p["gridPos"]["y"] >= 51:
        p["gridPos"]["y"] += 6
panels.append({
    "datasource": ds, "id": 56, "title": "On-host upgrades (last result per service)", "type": "table",
    "description": "Upgrades started on the server itself (click a version in the table above, or open the "
                   "upgrade page). Result: what the upgrade playbook reported — FAILED means it rolled back "
                   "(details: journalctl -u 'app-upgrade@*' on the host). Empty = none yet, or on-host upgrades "
                   "are not enabled on this host.",
    "gridPos": {"h": 6, "w": 20, "x": 0, "y": 51},
    "links": [{"title": "Upgrade page", "url": "/hooks/upgrade", "targetBlank": True}],
    "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Result"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "1": {"text": "upgraded", "color": "green", "index": 0},
                "0": {"text": "FAILED / rolled back", "color": "red", "index": 1}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}}]},
        {"matcher": {"id": "byName", "options": "Finished"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]},
        {"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "s"}]},
        {"matcher": {"id": "byName", "options": "Target"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "same-major": {"text": "minor update", "index": 0}, "next-major": {"text": "next major", "index": 1},
                "latest": {"text": "newest major", "index": 2}}}]}]}]},
    "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "Finished", "desc": True}]},
    "transformations": [
        {"id": "joinByField", "options": {"byField": "service", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "Time 3": True},
            "renameByName": {"service": "Service", "version": "Version", "target": "Target", "Value #A": "Result",
                             "Value #B": "Finished", "Value #C": "Duration"},
            "indexByName": {"service": 0, "version": 1, "target": 2, "Value #A": 3, "Value #B": 4, "Value #C": 5}}}],
    "targets": [{"datasource": ds, "expr": e, "format": "table", "instant": True, "refId": r} for r, e in (
        ("A", 'max by (service, version, target) (app_upgrade_last_result)'),
        ("B", 'max by (service) (app_upgrade_last_timestamp_seconds) * 1000'),
        ("C", 'max by (service) (app_upgrade_last_duration_seconds)'))]})
RUNNING = stat(57, "Upgrade running", "1 while an upgrade started on the server is running (only one at a time).",
               'max(app_upgrade_running) or vector(0)', 20, 4, steps=BLUE, y=51, h=6)
RUNNING["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {
    "0": {"text": "no", "index": 0}, "1": {"text": "yes", "index": 1}}}]
panels.append(RUNNING)

d = {k: ref[k] for k in ref if k not in ('panels', 'title', 'uid', 'tags', 'description', 'id', 'version', 'time', 'templating')}
d.update(title="Vulnerabilities", uid="vulnerabilities", tags=["security", "ansible"], panels=panels,
         time={"from": "now-30d", "to": "now"},
         templating={"list": [{"name": "image", "label": "image", "type": "query", "datasource": ds,
                               "definition": "label_values(trivy_image_scan_success, image)",
                               "query": {"query": "label_values(trivy_image_scan_success, image)", "refId": "A"},
                               "multi": True, "includeAll": True, "allValue": ".*",
                               "current": {"text": "All", "value": "$__all"}, "refresh": 2, "sort": 1},
                              {"name": "severity", "label": "severity", "type": "custom", "query": "CRITICAL,HIGH",
                               "options": [{"text": "CRITICAL", "value": "CRITICAL", "selected": True},
                                           {"text": "HIGH", "value": "HIGH", "selected": True}],
                               "current": {"text": ["CRITICAL", "HIGH"], "value": ["CRITICAL", "HIGH"]},
                               "multi": True, "includeAll": False},
                              {"name": "target", "label": "update target", "type": "custom", "query": "same-major,next-major,latest",
                               "options": [{"text": "same-major", "value": "same-major", "selected": True},
                                           {"text": "next-major", "value": "next-major", "selected": False},
                                           {"text": "latest", "value": "latest", "selected": False}],
                               "current": {"text": "same-major", "value": "same-major"}, "multi": False, "includeAll": False}]},
         description="Known CRITICAL/HIGH vulnerabilities of the container images running on this host (trivy, nightly).")
d['refresh'] = '5m'   # the data changes once per scan; the CVE tables are big (thousands of rows)
with open(D + '/vulnerabilities.json', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
print('dashboard ok', len(panels), 'panels')
