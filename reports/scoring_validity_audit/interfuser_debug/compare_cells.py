"""Compare re-run InterFuser cells (collector output) with the v2 leaderboard cells. Run from the checkout root."""
import csv, json, sys
old = {r["scenario"]: r for r in csv.DictReader(open("reports/scoring_validity_audit/data/episode_decomposition.csv", encoding="utf-8")) if r["model"] == "interfuser"}
new = {r["scenario"]: r for r in csv.DictReader(open("tmp/_validation_interfuser/authority_scored/cells_v2.csv", encoding="utf-8"))}
rows = []
for sc, o in old.items():
    n = new.get(sc)
    if not n: print("missing", sc); continue
    # motion from new telemetry
    tel = json.load(open(n["episode"] + "/strict_telemetry.json", encoding="utf-8"))
    tel = tel.get("telemetry") if isinstance(tel, dict) else tel
    vmax = max(float(r["ego_speed_kmh"] or 0) for r in tel)
    rows.append((sc, o["expected_action"], o["strict"], float(o["credit"]), n["strict"], float(n["credit"] or 0), float(o["all_max_v"]), vmax, o["motion_class"], float(o["weight"])))
print("| scenario | expected | v2 strict | v2 credit | new strict | new credit | v2 v_max | new v_max | v2 motion class |")
print("|---|---|---|---|---|---|---|---|---|")
for r in rows:
    flag = "**" if (r[2] != r[4] or abs(r[3]-r[5]) > 0.05) else ""
    print(f"| {flag}{r[0]}{flag} | {r[1]} | {r[2]} | {r[3]:.3f} | {r[4]} | {r[5]:.3f} | {r[6]:.1f} | {r[7]:.1f} | {r[8]} |")
W = sum(r[9] for r in rows)
print("graded old %.2f new %.2f; strict old %d new %d" % (100*sum(r[9]*r[3] for r in rows)/W, 100*sum(r[9]*r[5] for r in rows)/W, sum(r[2]=="PASS" for r in rows), sum(r[4]=="PASS" for r in rows)))
