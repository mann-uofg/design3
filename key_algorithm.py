"""
waste_collection_routing.py  (BSF facility + single weekly visit + capacity monitoring)

Dynamic Waste Collection Routing — Farm Boy Regional Cluster
Stores   : Guelph | Kitchener | Waterloo | Cambridge
Facility : Black Soldier Fly processing plant — Woolwich Township farmland (Breslau area)

Algorithm : Exact TSP (brute-force ≤8 nodes) + Single-Visit VRP + Facility Capacity Monitor
Output    : Console report + Interactive Folium map

CHANGES FROM v2:
  § 1 : Added FACILITY_CAPACITY_KG, FACILITY_CURRENT_FILL_KG,
        VISITS_PER_STORE_PER_WEEK (fixed = 1), WORKING_HOURS_MIN.
        TRUCK_CAPACITY_KG remains fully configurable — change it to
        reflect any truck size; all downstream calculations update automatically.
  § 2 : FACILITY relocated from Guelph Waste Innovation District to
        Woolwich Township farmland (BSF-appropriate rural/agricultural zone,
        centred between all four stores to minimise total route distance).
        Store sizes kept from v2 (owner-verified).
  § 4 : Waste profile now enforces single visit per store per week.
        loads_per_visit = ceil(weekly_kg / TRUCK_CAPACITY_KG).
        Changing TRUCK_CAPACITY_KG directly changes loads_per_visit.
  § 6 : New facility_status() — returns fill band (NORMAL / WARNING /
        CAUTION / CRITICAL / FULL) + alert message at each threshold.
  § 7 : Scheduler fully rebuilt with three capacity gates:
          Gate 1  FACILITY_FULL       — store deferred entirely.
          Gate 2  PARTIAL_COLLECTION  — collect only what facility can absorb.
          Gate 3  OVERTIME_RISK       — day exceeds WORKING_HOURS_MIN cap.
  § 8 : Daily route builder handles N-load split trips on a single visit day.
  §10 : Report prints facility status banner + all flagged alerts.
  §11 : Map updated — BSF facility marker (leaf icon), capacity bar in legend.
"""

import itertools
from math import radians, sin, cos, sqrt, atan2, ceil
from collections import defaultdict
import folium

# ============================================================
# SECTION 1: OPERATIONAL PARAMETERS  ← tune all values here
# ============================================================

# ── Truck ────────────────────────────────────────────────────
TRUCK_CAPACITY_KG          = 3_000   # kg per single truck load ← change truck size here
TRUCK_SPEED_KPH            = 60      # avg road speed (urban slow-down included)
LOADING_TIME_MIN           = 25      # min to load/unload at each store stop
ROAD_FACTOR                = 1.35    # straight-line → actual road distance multiplier

# ── Schedule ────────────────────────────────────────────────
DAYS_PER_WEEK              = 5       # Mon–Fri
VISITS_PER_STORE_PER_WEEK  = 1       # fixed: each store visited exactly once/week
WORKING_HOURS_MIN          = 480     # 8-hour workday cap (minutes)

# ── BSF Facility capacity ───────────────────────────────────
FACILITY_CAPACITY_KG      = 50_000   # total weekly intake limit of the BSF plant
FACILITY_CURRENT_FILL_KG  = 0        # pre-existing fill at week start (0 = empty)
                                      # ← set e.g. 25_000 to simulate half-full

# Fill alert thresholds (fraction of FACILITY_CAPACITY_KG)
FILL_WARN     = 0.50   # >= 50% → WARNING
FILL_CAUTION  = 0.75   # >= 75% → CAUTION
FILL_CRITICAL = 0.90   # >= 90% → CRITICAL
                        # >= 100% → FULL (hard stop — no further intake)

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# ============================================================
# SECTION 2: FACILITY & STORE DATA
# ============================================================

# BSF Facility — Woolwich Township farmland, Breslau area, ON
# Rationale:
#   • Rural / agricultural zone — required zoning for BSF insect rearing.
#   • Geographic centroid of the four-store cluster → minimises total
#     round-trip distance for all collection runs.
#   • Road-accessible via Fountain St N / Regional Rd 17 / Hwy 7.
#   • ~12 km from Waterloo, ~11 km from Kitchener,
#     ~20 km from Guelph, ~18 km from Cambridge.
FACILITY = {
    "name"   : "BSF Processing Facility — Woolwich Township (Breslau area)",
    "coords" : [43.490, -80.420],   # farmland NW of Breslau, Woolwich Township
    "address": "Woolwich Township, ON  (rural / agricultural zone)",
}

# Store sizes owner-verified (March 2026).
# Waste rates: NRCAN 2021 + Retail Council of Canada (0.25–0.40 kg/sqft/week).
# Stores with full deli / butcher / bakery skew toward the high end.
STORES = {
    "Waterloo": {
        "coords" : [43.4855568, -80.5274827],
        "address": "417 King Street North, Waterloo",
        "size_sqft"                       : 26_725,   # VERIFIED
        "waste_rate_kg_per_sqft_per_week" : 0.38,
        "color"  : "blue",
        "notes"  : "Largest in cluster (26,725 sqft). Full-service flagship — deli/butcher/bakery.",
    },
    "Guelph": {
        "coords" : [43.5161018, -80.2369272],
        "address": "370 Stone Road West, Guelph",
        "size_sqft"                       : 25_276,   # VERIFIED
        "waste_rate_kg_per_sqft_per_week" : 0.36,
        "color"  : "purple",
        "notes"  : "2nd largest (25,276 sqft). High throughput, university corridor.",
    },
    "Cambridge": {
        "coords" : [43.3935986, -80.3206588],
        "address": "350 Hespeler Road, Cambridge",
        "size_sqft"                       : 22_800,   # VERIFIED
        "waste_rate_kg_per_sqft_per_week" : 0.33,
        "color"  : "orange",
        "notes"  : "3rd largest (22,800 sqft). Hespeler Road suburban.",
    },
    "Kitchener": {
        "coords" : [43.4209679, -80.4404296],
        "address": "385 Fairway Road South, Kitchener",
        "size_sqft"                       : 22_000,   # VERIFIED
        "waste_rate_kg_per_sqft_per_week" : 0.32,
        "color"  : "green",
        "notes"  : "Smallest in cluster (22,000 sqft). Fairway Road suburban.",
    },
}

# ============================================================
# SECTION 3: DISTANCE & TRAVEL UTILITIES  (unchanged)
# ============================================================

def haversine_km(c1, c2):
    """Great-circle straight-line distance in km."""
    R = 6_371.0
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def road_km(c1, c2):
    return haversine_km(c1, c2) * ROAD_FACTOR

def drive_min(c1, c2):
    return (road_km(c1, c2) / TRUCK_SPEED_KPH) * 60

# ============================================================
# SECTION 4: WASTE PROFILE  (single-visit policy)
# ============================================================

def compute_waste_profile():
    """
    Single-visit policy: each store is visited exactly ONCE per week.
    All weekly waste is collected in that one visit.
    If weekly_kg > TRUCK_CAPACITY_KG, the truck makes multiple
    Facility → Store → Facility runs on that day.

      weekly_kg       = size_sqft × waste_rate_kg_per_sqft_per_week
      loads_per_visit = ceil(weekly_kg / TRUCK_CAPACITY_KG)
      waste_per_load  = weekly_kg / loads_per_visit  [≤ TRUCK_CAPACITY_KG]

    Changing TRUCK_CAPACITY_KG automatically adjusts loads_per_visit
    for every store — no other code needs to change.
    """
    profile = {}
    for store, data in STORES.items():
        weekly_kg       = data["size_sqft"] * data["waste_rate_kg_per_sqft_per_week"]
        loads_per_visit = ceil(weekly_kg / TRUCK_CAPACITY_KG)
        waste_per_load  = weekly_kg / loads_per_visit
        profile[store] = {
            "weekly_kg"      : round(weekly_kg, 1),
            "loads_per_visit": loads_per_visit,
            "waste_per_load" : round(waste_per_load, 1),
            "collected_kg"   : round(weekly_kg, 1),   # may shrink if partial
            "deferred_kg"    : 0.0,
            "partial"        : False,
            "status_flag"    : "OK",
            "collection_day" : None,
        }
    return profile

# ============================================================
# SECTION 5: EXACT TSP SOLVER  (unchanged)
# ============================================================

def solve_tsp(store_list, origin_coord):
    """
    Brute-force exact TSP: origin → [perm of stores] → origin.
    Feasible for ≤ 8 nodes (8! = 40,320 permutations, milliseconds).
    Returns (ordered_store_list, total_road_km).
    """
    if len(store_list) == 1:
        d = road_km(origin_coord, STORES[store_list[0]]["coords"]) * 2
        return store_list, round(d, 2)
    best_d, best_r = float("inf"), None
    for perm in itertools.permutations(store_list):
        d = road_km(origin_coord, STORES[perm[0]]["coords"])
        for i in range(len(perm) - 1):
            d += road_km(STORES[perm[i]]["coords"], STORES[perm[i+1]]["coords"])
        d += road_km(STORES[perm[-1]]["coords"], origin_coord)
        if d < best_d:
            best_d, best_r = d, list(perm)
    return best_r, round(best_d, 2)

# ============================================================
# SECTION 6: FACILITY STATUS CHECKER 
# ============================================================

def facility_status(current_fill_kg):
    """
    Returns (status_label, fill_ratio, alert_message).

    Bands:
      NORMAL   [0%  – 50%)  — intake capacity freely available.
      WARNING  [50% – 75%)  — facility past half; plan ahead.
      CAUTION  [75% – 90%)  — significant fill; review schedule.
      CRITICAL [90% – 100%) — approaching limit; immediate review.
      FULL     [100%+)      — hard stop; no further intake this week.

    Usage: call before every store collection to decide whether to
    proceed, partially collect, or defer entirely.
    """
    ratio = current_fill_kg / FACILITY_CAPACITY_KG
    if ratio >= 1.0:
        return "FULL",     ratio, "🚫 FACILITY FULL — no further intake possible this week."
    if ratio >= FILL_CRITICAL:
        return "CRITICAL", ratio, f"🔴 CRITICAL ({ratio*100:.1f}%) — approaching limit. Review immediately."
    if ratio >= FILL_CAUTION:
        return "CAUTION",  ratio, f"🟠 CAUTION ({ratio*100:.1f}%) — significant fill. Monitor closely."
    if ratio >= FILL_WARN:
        return "WARNING",  ratio, f"🟡 WARNING ({ratio*100:.1f}%) — facility past half capacity."
    return     "NORMAL",   ratio, f"🟢 NORMAL ({ratio*100:.1f}%) — intake capacity available."

# ============================================================
# SECTION 7: WEEKLY SCHEDULER  
# ============================================================

def build_schedule(profile):
    """
    Assigns each store exactly one collection day (Mon – Thu).
    Friday is reserved as a buffer / facility processing day.

    Stores sorted by weekly_kg DESC so the heaviest loads arrive when
    the facility is emptiest (start of week), leaving buffer room.

    Three capacity gates applied for every store in sequence:

      Gate 1 — FACILITY_FULL
        Facility at 100%+ → store collection deferred to next week.
        Flag: CRITICAL / FACILITY_FULL.

      Gate 2 — PARTIAL_COLLECTION
        Facility has remaining space < store's weekly_kg.
        Truck collects only what fits; deficit is flagged as deferred.
        Flag: WARNING / PARTIAL_COLLECTION.

      Gate 3 — OVERTIME_RISK
        Estimated day time (N loads × round-trip + loading) > WORKING_HOURS_MIN.
        Collection still proceeds; flag raised for operator awareness.
        Flag: WARNING / OVERTIME_RISK.

    Edge cases:
      • If >4 stores exist, overflow stores share Thursday (day index 3).
      • If all stores are deferred (facility full from start), a
        BUFFER_DAY_NEEDED critical flag is raised.
      • FACILITY_CURRENT_FILL_KG can be set to any non-zero value to
        simulate mid-week or pre-filled facility states.
    """
    schedule = defaultdict(list)
    flags    = []
    fill     = FACILITY_CURRENT_FILL_KG

    sorted_stores = sorted(
        profile.items(), key=lambda x: x[1]["weekly_kg"], reverse=True
    )

    day_idx = 0   # Mon=0 … Thu=3; Fri=4 reserved as buffer

    for store, info in sorted_stores:

        # ── Gate 1: facility already full? ──────────────────────────
        status, ratio, _ = facility_status(fill)
        if status == "FULL":
            info.update({
                "status_flag"   : "DEFERRED — FACILITY FULL",
                "collection_day": "DEFERRED",
                "collected_kg"  : 0.0,
                "deferred_kg"   : info["weekly_kg"],
            })
            flags.append({
                "severity": "CRITICAL",
                "type"    : "FACILITY_FULL",
                "store"   : store,
                "detail"  : (f"Facility at {ratio*100:.1f}% capacity. "
                             f"{info['weekly_kg']:,.0f} kg fully deferred."),
            })
            continue

        # ── Gate 2: partial capacity remaining? ─────────────────────
        remaining = FACILITY_CAPACITY_KG - fill
        if remaining < info["weekly_kg"]:
            new_loads = ceil(remaining / TRUCK_CAPACITY_KG)
            info.update({
                "collected_kg"   : round(remaining, 1),
                "deferred_kg"    : round(info["weekly_kg"] - remaining, 1),
                "loads_per_visit": new_loads,
                "waste_per_load" : round(remaining / max(1, new_loads), 1),
                "partial"        : True,
                "status_flag"    : "PARTIAL COLLECTION",
            })
            flags.append({
                "severity": "WARNING",
                "type"    : "PARTIAL_COLLECTION",
                "store"   : store,
                "detail"  : (f"Only {remaining:,.0f} kg space available; "
                             f"{info['collected_kg']:,.0f} kg collected, "
                             f"{info['deferred_kg']:,.0f} kg deferred."),
            })
            fill += remaining
        else:
            fill += info["weekly_kg"]

        # ── Overflow: >4 stores all land on Thursday ─────────────────
        if day_idx > 3:
            day_idx = 3

        # ── Gate 3: overtime risk? ────────────────────────────────────
        fac_c   = FACILITY["coords"]
        leg_min = drive_min(fac_c, STORES[store]["coords"]) * 2 + LOADING_TIME_MIN
        est_min = info["loads_per_visit"] * leg_min
        if est_min > WORKING_HOURS_MIN:
            flags.append({
                "severity": "WARNING",
                "type"    : "OVERTIME_RISK",
                "store"   : store,
                "detail"  : (f"Est. {est_min:.0f} min ({est_min/60:.1f} hrs) for "
                             f"{info['loads_per_visit']} loads — exceeds "
                             f"{WORKING_HOURS_MIN // 60}-hr workday cap."),
            })
            if not info["partial"]:
                info["status_flag"] = "OVERTIME RISK"

        schedule[day_idx].append(store)
        info["collection_day"] = DAY_NAMES[day_idx]
        day_idx += 1

    # Friday buffer: raise critical flag if any store was deferred
    deferred = [s for s, i in profile.items() if i["collection_day"] == "DEFERRED"]
    if deferred:
        flags.append({
            "severity": "CRITICAL",
            "type"    : "BUFFER_DAY_NEEDED",
            "store"   : ", ".join(deferred),
            "detail"  : ("Friday buffer day required for deferred collections. "
                         "Increase FACILITY_CAPACITY_KG or reduce weekly intake."),
        })

    return schedule, flags, fill

# ============================================================
# SECTION 8: DAILY ROUTE BUILDER  (single-visit split-load)
# ============================================================

def build_daily_routes(schedule, profile):
    """
    For each day with stores assigned:

    Single-store day (typical — one store per day policy):
      Truck makes N = loads_per_visit runs of:
          Facility → Store → Facility
      Total distance = N × 2 × one_way_km
      Total time     = N × (2 × drive_min + LOADING_TIME_MIN)

    Multi-store day (edge case — e.g., overflow stores share a day):
      TSP finds the optimal visit order for one circuit.
      If stores need different numbers of loads, extra-load stores
      get additional dedicated Facility → Store → Facility runs
      after the shared circuit completes.

    overtime flag raised if total_time > WORKING_HOURS_MIN.
    """
    fac    = FACILITY["coords"]
    routes = []

    for day_idx in range(DAYS_PER_WEEK):
        stores_today = schedule.get(day_idx, [])

        if not stores_today:
            routes.append({
                "day"         : DAY_NAMES[day_idx],
                "stores"      : [],
                "route_order" : [],
                "distance_km" : 0,
                "time_min"    : 0,
                "loads_detail": [],
                "overtime"    : False,
            })
            continue

        route_order, _ = solve_tsp(stores_today, fac)
        total_dist = 0.0
        total_min  = 0.0
        loads_detail = []

        if len(stores_today) == 1:
            # ── Normal case: dedicated single-store day ──────────────
            s   = stores_today[0]
            n   = profile[s]["loads_per_visit"]
            leg = road_km(fac, STORES[s]["coords"]) * 2
            total_dist = round(n * leg, 2)
            total_min  = n * (drive_min(fac, STORES[s]["coords"]) * 2 + LOADING_TIME_MIN)
            loads_detail.append({
                "store"      : s,
                "loads"      : n,
                "kg_each"    : profile[s]["waste_per_load"],
                "total_kg"   : profile[s]["collected_kg"],
                "one_way_km" : round(leg / 2, 2),
            })
        else:
            # ── Edge case: multiple stores share a day ───────────────
            # Run load cycles; stores drop out once their loads are served
            max_loads = max(profile[s]["loads_per_visit"] for s in stores_today)
            for cycle in range(max_loads):
                in_cycle = [s for s in route_order
                            if profile[s]["loads_per_visit"] > cycle]
                if not in_cycle:
                    break
                _, cd = solve_tsp(in_cycle, fac)
                total_dist += cd
                total_min  += (cd / TRUCK_SPEED_KPH * 60
                               + len(in_cycle) * LOADING_TIME_MIN)
            total_dist = round(total_dist, 2)
            for s in stores_today:
                loads_detail.append({
                    "store"      : s,
                    "loads"      : profile[s]["loads_per_visit"],
                    "kg_each"    : profile[s]["waste_per_load"],
                    "total_kg"   : profile[s]["collected_kg"],
                    "one_way_km" : round(road_km(fac, STORES[s]["coords"]), 2),
                })

        overtime = total_min > WORKING_HOURS_MIN
        routes.append({
            "day"         : DAY_NAMES[day_idx],
            "stores"      : stores_today,
            "route_order" : route_order,
            "distance_km" : total_dist,
            "time_min"    : round(total_min, 1),
            "loads_detail": loads_detail,
            "overtime"    : overtime,
        })

    return routes

# ============================================================
# SECTION 9: SPLIT-TRIP OVERHEAD UTILITY  (retained)
# ============================================================

def compute_split_trip_overhead(store, loads):
    """
    Returns (extra_km, extra_min) for loads 2..N of a single store.
    Load 1 is already included in the base route calculation.
    """
    fac         = FACILITY["coords"]
    extra_loads = loads - 1
    leg_km      = road_km(fac, STORES[store]["coords"]) * 2
    extra_km    = round(extra_loads * leg_km, 2)
    extra_min   = round(
        extra_loads * (drive_min(fac, STORES[store]["coords"]) * 2 + LOADING_TIME_MIN), 1
    )
    return extra_km, extra_min

# ============================================================
# SECTION 10: CONSOLE REPORT
# ============================================================

def print_report(profile, routes, flags, final_fill):
    SEP  = "=" * 72
    SEP2 = "-" * 68

    print(f"\n{SEP}")
    print("  FARM BOY × BSF WASTE COLLECTION SYSTEM")
    print(SEP)

    # ── Facility status ───────────────────────────────────────────────
    s0, r0, _  = facility_status(FACILITY_CURRENT_FILL_KG)
    sF, rF, mF = facility_status(final_fill)
    print(f"\n  BSF FACILITY STATUS")
    print(f"  {SEP2}")
    print(f"  Name     : {FACILITY['name']}")
    print(f"  Address  : {FACILITY['address']}")
    print(f"  Capacity : {FACILITY_CAPACITY_KG:>10,} kg / week")
    print(f"  Fill (start of week) : {FACILITY_CURRENT_FILL_KG:>10,} kg  "
          f"({r0*100:.1f}%)  [{s0}]")
    print(f"  Fill (after collect) : {final_fill:>10,.1f} kg  "
          f"({rF*100:.1f}%)  [{sF}]")
    print(f"  Status   : {mF}")

    # ── Alerts ───────────────────────────────────────────────────────
    if flags:
        print(f"\n  ⚠  ALERTS & FLAGS  ({len(flags)} total)")
        print(f"  {SEP2}")
        for fl in flags:
            icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(fl["severity"], "⚪")
            print(f"  {icon} [{fl['severity']}]  {fl['type']}  —  {fl['store']}")
            print(f"         {fl['detail']}")

    # ── Store profiles ────────────────────────────────────────────────
    print(f"\n\n  STORE WASTE PROFILES  (1 visit / store / week)")
    print(f"  {SEP2}")
    print(f"  {'Store':<12} {'sqft':>7} {'kg/wk':>9} {'Loads':>6} "
          f"{'kg/load':>8} {'Collected':>10} {'Deferred':>9}  Day         Status")
    print("  " + "-" * 90)
    for store, info in profile.items():
        flag_tag = f"  ← {info['status_flag']}" if info["status_flag"] != "OK" else ""
        print(f"  {store:<12} {STORES[store]['size_sqft']:>7,} "
              f"{info['weekly_kg']:>9,.1f} {info['loads_per_visit']:>6} "
              f"{info['waste_per_load']:>8,.1f} {info['collected_kg']:>10,.1f} "
              f"{info['deferred_kg']:>9,.1f}  "
              f"{str(info.get('collection_day','—')):<12}{flag_tag}")

    print(f"\n  Truck capacity  : {TRUCK_CAPACITY_KG:,} kg / load  "
          f"← change TRUCK_CAPACITY_KG to resize truck")
    print(f"  Truck speed     : {TRUCK_SPEED_KPH} km/h  |  "
          f"Load/unload: {LOADING_TIME_MIN} min/stop  |  Road factor: {ROAD_FACTOR}×")
    print(f"  Workday cap     : {WORKING_HOURS_MIN} min ({WORKING_HOURS_MIN // 60} hrs)")

    # ── Weekly schedule ───────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("  WEEKLY COLLECTION SCHEDULE  (1 visit/store/week — split loads to BSF facility)")
    print(SEP)

    for route in routes:
        ot_tag = "  ⚠ OVERTIME" if route["overtime"] else ""
        print(f"\n  {route['day'].upper()}{ot_tag}")
        print("  " + "─" * 64)

        if not route["stores"]:
            print("  [Buffer / BSF facility processing day — no collections]")
            continue

        for ld in route["loads_detail"]:
            s, n = ld["store"], ld["loads"]
            print(f"  Store      : {s}")
            print(f"  Route      : Facility → {s} → Facility  ×{n} load(s)")
            print(f"  One-way    : {ld['one_way_km']:.1f} km  |  "
                  f"Round-trip/load: {ld['one_way_km']*2:.1f} km")
            print(f"  Waste      : {ld['total_kg']:,.1f} kg total  "
                  f"({n} × {ld['kg_each']:,.1f} kg / load)")
            if profile[s].get("partial"):
                print(f"  ⚠ PARTIAL : {profile[s]['deferred_kg']:,.1f} kg NOT collected "
                      f"(facility capacity limit reached)")
            print()

        print(f"  Day total  : {route['distance_km']:.1f} km  |  "
              f"{route['time_min']:.0f} min ({route['time_min']/60:.1f} hrs){ot_tag}")

    # ── Weekly totals ─────────────────────────────────────────────────
    total_km        = sum(r["distance_km"] for r in routes)
    total_min       = sum(r["time_min"]    for r in routes)
    total_collected = sum(i["collected_kg"] for i in profile.values())
    total_deferred  = sum(i["deferred_kg"]  for i in profile.values())

    print(f"\n{SEP}")
    print("  WEEKLY TOTALS")
    print(f"  Total route distance   : {total_km:.1f} km")
    print(f"  Total operational time : {total_min / 60:.1f} hrs")
    print(f"  Total waste collected  : {total_collected:,.0f} kg")
    if total_deferred > 0:
        print(f"  ⚠  Waste deferred      : {total_deferred:,.0f} kg  "
              f"← facility capacity exceeded")
    print(f"  Facility fill (end)    : {final_fill:,.0f} / {FACILITY_CAPACITY_KG:,} kg  "
          f"({final_fill / FACILITY_CAPACITY_KG * 100:.1f}%)")
    print(SEP)

# ============================================================
# SECTION 11: INTERACTIVE MAP  (BSF facility + capacity bar)
# ============================================================

def generate_map(profile, routes, final_fill):
    ROUTE_COLORS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6"]
    fac_coord    = FACILITY["coords"]

    m = folium.Map(location=[43.46, -80.39], zoom_start=11)

    # ── BSF Facility marker ──────────────────────────────────────────
    _, ratio_end, _ = facility_status(final_fill)
    fac_popup = (
        f"<b>{FACILITY['name']}</b><br>"
        f"{FACILITY['address']}<br><br>"
        f"Weekly capacity : {FACILITY_CAPACITY_KG:,} kg<br>"
        f"Fill after week : {final_fill:,.0f} kg ({ratio_end*100:.1f}%)"
    )
    folium.Marker(
        location=fac_coord,
        popup=folium.Popup(fac_popup, max_width=290),
        tooltip="BSF Facility — Woolwich Township",
        icon=folium.Icon(color="black", icon="leaf", prefix="fa"),
    ).add_to(m)

    # ── Store markers  (radius ∝ weekly waste) ───────────────────────
    for store, data in STORES.items():
        info   = profile[store]
        radius = max(8, info["weekly_kg"] / 120)
        partial_note = (
            f"<br><b>⚠ PARTIAL: {info['deferred_kg']:,.0f} kg deferred</b>"
            if info.get("partial") else ""
        )
        popup_html = (
            f"<b>{store}</b><br>{data['address']}<br>"
            f"Size: {data['size_sqft']:,} sqft<br>"
            f"Weekly waste: {info['weekly_kg']:,} kg<br>"
            f"Truck loads/visit: {info['loads_per_visit']}<br>"
            f"Collection day: {info.get('collection_day', '—')}"
            f"{partial_note}"
        )
        folium.CircleMarker(
            location=data["coords"],
            radius=radius,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{store} — {info['weekly_kg']:,} kg/wk",
            color=data["color"],
            fill=True,
            fillOpacity=0.75,
        ).add_to(m)

    # ── Route polylines  (thicker line = more truck loads) ───────────
    active = [r for r in routes if r["route_order"]]
    for i, route in enumerate(active):
        s       = route["stores"][0]
        n_loads = profile[s]["loads_per_visit"]
        coords  = [fac_coord, STORES[route["route_order"][0]]["coords"], fac_coord]
        label   = (f"{route['day']}: "
                   f"{' → '.join(route['route_order'])}  "
                   f"({n_loads} loads, {route['distance_km']:.1f} km)")
        folium.PolyLine(
            locations=coords,
            color=ROUTE_COLORS[i % len(ROUTE_COLORS)],
            weight=3 + n_loads * 0.5,
            opacity=0.85,
            tooltip=label,
            dash_array="8 4",
        ).add_to(m)

    # ── Legend with capacity bar ─────────────────────────────────────
    fill_pct  = min(100, ratio_end * 100)
    bar_color = ("#e74c3c" if fill_pct >= 90 else
                 "#f39c12" if fill_pct >= 75 else
                 "#f1c40f" if fill_pct >= 50 else "#2ecc71")
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                background:white;padding:14px;border:2px solid #aaa;
                border-radius:8px;font-size:13px;line-height:1.9;min-width:210px;">
        <b>🗓 Route Legend</b><br>
        {"".join(
            f'<span style="color:{ROUTE_COLORS[i%len(ROUTE_COLORS)]}">━━</span> {r["day"]}<br>'
            for i, r in enumerate(active)
        )}
        <hr style="margin:6px 0;">
        <b>🌿 BSF Facility Fill</b><br>
        <div style="background:#eee;border-radius:4px;height:14px;width:170px;">
          <div style="background:{bar_color};width:{fill_pct:.0f}%;height:14px;
                      border-radius:4px;"></div>
        </div>
        <small>{fill_pct:.1f}% of {FACILITY_CAPACITY_KG:,} kg</small>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend_html))
    m.save("waste_collection_routes.html")
    print("\n  Interactive map saved → waste_collection_routes.html")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    profile              = compute_waste_profile()
    schedule, flags, fF  = build_schedule(profile)
    routes               = build_daily_routes(schedule, profile)
    print_report(profile, routes, flags, fF)
    generate_map(profile, routes, fF)
