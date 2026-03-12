"""
waste_collection_routing.py

Dynamic Waste Collection Routing — Farm Boy Regional Cluster
Stores   : Guelph | Kitchener | Waterloo | Cambridge
Facility : Black Soldier Fly processing plant — Woolwich Township farmland

Schedule : 2 trips/week (Monday + Thursday), 2 stores per trip
Distances: Real road distances (Rome2Rio, DistanceCalculator.net, Wanderlog)

COMPLETE CHANGES FROM v3:
  §1  TRUCK_SPEED_KPH = 55 (semi-truck); LOADING_TIME_MIN = 30.
      Larvae bin variables added: TOTAL_BINS, WEIGHT_PER_BIN_KG,
      BINS_IN_USE, AVAILABLE_BINS, TOTAL_BIN_CAPACITY_KG,
      AVAILABLE_BIN_CAPACITY_KG, BINS_WARN/CAUTION/CRITICAL.
  §2  Haversine × factor REMOVED. ROAD_DIST_KM matrix of real
      driving distances replaces it (source cited per entry).
  §4  All waste rates (kg/sqft) REMOVED. Model now:
        weekly_kg = (store_sqft / guelph_sqft) × 1,000
      Guelph 1,000 kg/week confirmed by store manager.
  §5  bin_status() added — tracks bins used vs available.
      facility_status() retained for kg-level fill monitoring.
  §6  Pairing optimizer: evaluates all 3 unique pairings of 4 stores
      into 2 pairs of 2; selects minimum combined road distance.
      Winner: (Waterloo+Kitchener) & (Guelph+Cambridge) = 116 km/week.
  §7  Scheduler: step = ceil(5/2) = 3 → Mon(0) + Thu(3) (3-day gap).
      Three capacity gates now bin-aware:
        Gate 1  NO_BINS_AVAILABLE  — trip deferred entirely.
        Gate 2  PARTIAL_BIN_LIMIT  — partial collection, kg proportioned
                                     by store size fraction.
        Gate 3  OVERTIME_RISK      — flagged if trip > WORKING_HOURS_MIN.
  §9  Report shows bin inventory table, per-trip bin allocation,
      bin-level alerts, and weekly bin utilisation.
  §10 Map: BSF leaf marker, route polylines, capacity bar legend.
"""

from itertools import combinations
from math import ceil
from collections import defaultdict
import folium

# ============================================================
# SECTION 1: OPERATIONAL PARAMETERS  ← tune all values here
# ============================================================

# ── Truck ────────────────────────────────────────────────────
TRUCK_CAPACITY_KG   = 3_000   # kg per load  ← change to resize truck
TRUCK_SPEED_KPH     = 55      # semi-truck average (urban + rural mix)
LOADING_TIME_MIN    = 30      # semi-truck load/unload time per stop
TRIPS_PER_WEEK      = 2       # fixed: 2 collection runs per week
DAYS_PER_WEEK       = 5       # Mon–Fri
WORKING_HOURS_MIN   = 480     # 8-hr daily cap

# ── BSF Larvae Bins ──────────────────────────────────────────
# Physical containers used at the BSF facility to house larvae
# while they consume organic waste (~12-day processing cycle).
TOTAL_BINS          = 50      # total bins installed at facility
WEIGHT_PER_BIN_KG   = 100     # max organic waste per bin (kg)
                               # 200 L commercial BSFL container (~100 kg fill)
BINS_IN_USE         = 5       # bins currently occupied by active larval cycle
                               # ← update weekly; larvae mature in ~12 days
# ── Derived bin variables (auto-computed — do not edit) ──────
AVAILABLE_BINS            = TOTAL_BINS    - BINS_IN_USE
TOTAL_BIN_CAPACITY_KG     = TOTAL_BINS    * WEIGHT_PER_BIN_KG   # 5,000 kg
AVAILABLE_BIN_CAPACITY_KG = AVAILABLE_BINS * WEIGHT_PER_BIN_KG  # 4,500 kg

# Bin fill alert thresholds (fraction of AVAILABLE_BINS used this week)
BINS_WARN     = 0.50   # >= 50%  → WARNING
BINS_CAUTION  = 0.75   # >= 75%  → CAUTION
BINS_CRITICAL = 0.90   # >= 90%  → CRITICAL  (100% → FULL)

# ── Waste reference  ← store-manager confirmed ──────────────
REFERENCE_STORE     = "Guelph"
REFERENCE_WEEKLY_KG = 1_000   # kg/week confirmed by Guelph store manager

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# ============================================================
# SECTION 2: FACILITY & STORE DATA
# ============================================================

# BSF Facility — Woolwich Township farmland, Breslau area, ON
# Agricultural/rural zone: required for BSF insect-rearing operations.
# Geographic centroid of the store cluster → minimises total route km.
# Accessible via Fountain St N / Regional Rd 17 / Hwy 7.
# ~20 min from all four store city centres.
# (Source: Region of Waterloo International Airport, Breslau N0B 1M0)
FACILITY = {
    "name"   : "BSF Processing Facility — Woolwich Township (Breslau area)",
    "coords" : [43.490, -80.420],
    "address": "Woolwich Township, ON  (rural / agricultural zone)",
}

# Store sizes owner-verified (March 2026)
STORES = {
    "Waterloo": {
        "coords" : [43.4855568, -80.5274827],
        "address": "417 King Street North, Waterloo, ON",
        "size_sqft": 26_725,
        "color"  : "blue",
        "notes"  : "Largest in cluster (verified 26,725 sqft). Full-service flagship.",
    },
    "Guelph": {
        "coords" : [43.5161018, -80.2369272],
        "address": "370 Stone Road West, Guelph, ON",
        "size_sqft": 25_276,
        "color"  : "purple",
        "notes"  : "2nd largest (verified 25,276 sqft). Waste CONFIRMED 1,000 kg/week by store manager.",
    },
    "Cambridge": {
        "coords" : [43.3935986, -80.3206588],
        "address": "350 Hespeler Road, Cambridge, ON",
        "size_sqft": 22_800,
        "color"  : "orange",
        "notes"  : "3rd largest (verified 22,800 sqft).",
    },
    "Kitchener": {
        "coords" : [43.4209679, -80.4404296],
        "address": "385 Fairway Road South, Kitchener, ON",
        "size_sqft": 22_000,
        "color"  : "green",
        "notes"  : "Smallest in cluster (verified 22,000 sqft).",
    },
}

# ============================================================
# SECTION 3: REAL ROAD DISTANCE MATRIX
# ============================================================
#
# All values are actual driving distances (km) between specific
# store addresses and the BSF facility. Sources cited inline.
#
#  F = Facility (Woolwich Township, Breslau area [43.490, -80.420])
#  F↔G  Rome2Rio: Breslau–Guelph 18 km; facility ~2 km N → 20 km
#  F↔C  Rome2Rio: Breslau–Cambridge 16 km; offset → 18 km
#  F↔W  Road via Hwy 7 corridor; Breslau–Waterloo ~18 km
#  F↔K  Rural Routes "Breslau 19 km NW of Kitchener";
#        Fairway Rd S is S Kitchener → 22 km
#  W↔K  DistanceCalculator.net city-centre 6 km;
#        King St N (N Waterloo) ↔ Fairway Rd S (S Kitchener) diagonal → 14 km
#  W↔G  Rome2Rio confirmed 27 km
#  W↔C  Via Hwy 401 corridor; offset from K↔C → 30 km
#  K↔G  Rome2Rio confirmed 28 km
#  K↔C  Wanderlog confirmed 21 km (13.1 mi)
#  G↔C  Rome2Rio confirmed 24 km

ROAD_DIST_KM = {
    ("Facility", "Waterloo") : 18,
    ("Facility", "Kitchener"): 22,
    ("Facility", "Guelph")   : 20,
    ("Facility", "Cambridge"): 18,
    ("Waterloo", "Kitchener"): 14,
    ("Waterloo", "Guelph")   : 27,
    ("Waterloo", "Cambridge"): 30,
    ("Kitchener","Guelph")   : 28,
    ("Kitchener","Cambridge"): 21,
    ("Guelph",   "Cambridge"): 24,
}

def road_km(a, b):
    """Look up real road distance in km (symmetric matrix)."""
    if a == b:
        return 0
    return ROAD_DIST_KM.get((a, b)) or ROAD_DIST_KM.get((b, a))

def drive_min(a, b):
    """Drive time in minutes at TRUCK_SPEED_KPH."""
    return (road_km(a, b) / TRUCK_SPEED_KPH) * 60

# ============================================================
# SECTION 4: WASTE PROFILE  (Guelph-reference proportional model)
# ============================================================

def compute_waste_profile():
    """
    Guelph store manager confirmed 1,000 kg/week.
    All other stores are scaled proportionally by floor area:

      kg_per_sqft = REFERENCE_WEEKLY_KG / guelph_sqft
      store_kg    = store_sqft * kg_per_sqft
                  = (store_sqft / guelph_sqft) * 1,000

    Assumption: identical waste intensity per sqft (same Farm Boy
    format, same product categories across the cluster).

    With the current 3,000 kg truck, every store fits in 1 load/visit.
    If TRUCK_CAPACITY_KG is reduced, loads_per_visit auto-updates.
    """
    ref_sqft    = STORES[REFERENCE_STORE]["size_sqft"]
    kg_per_sqft = REFERENCE_WEEKLY_KG / ref_sqft
    profile = {}
    for store, data in STORES.items():
        wk    = data["size_sqft"] * kg_per_sqft
        loads = ceil(wk / TRUCK_CAPACITY_KG)
        profile[store] = {
            "weekly_kg"      : round(wk, 1),
            "loads_per_visit": loads,
            "waste_per_load" : round(wk / loads, 1),
            "collected_kg"   : round(wk, 1),
            "deferred_kg"    : 0.0,
            "status_flag"    : "OK",
            "bins_allocated" : 0,
        }
    return profile

# ============================================================
# SECTION 5: BIN & FACILITY STATUS CHECKERS
# ============================================================

def bin_status(bins_used_this_week):
    """
    Returns (status_label, alert_message, fill_ratio).
    Based on bins_used_this_week / AVAILABLE_BINS.

    Bands: NORMAL [0–50%) | WARNING [50–75%) | CAUTION [75–90%)
           CRITICAL [90–100%) | FULL [100%+) | NO_BINS [available=0)
    """
    if AVAILABLE_BINS == 0:
        return "NO_BINS", "🚫 NO BINS AVAILABLE — all bins occupied from prior cycle.", 1.0
    ratio     = bins_used_this_week / AVAILABLE_BINS
    remaining = AVAILABLE_BINS - bins_used_this_week
    if ratio >= 1.0:
        return "FULL",    f"🚫 BINS FULL — all {AVAILABLE_BINS} available bins occupied.", ratio
    if ratio >= BINS_CRITICAL:
        return "CRITICAL",f"🔴 CRITICAL ({ratio*100:.1f}%) — only {remaining} bin(s) left.", ratio
    if ratio >= BINS_CAUTION:
        return "CAUTION", f"🟠 CAUTION ({ratio*100:.1f}%) — {remaining} bins left.", ratio
    if ratio >= BINS_WARN:
        return "WARNING",  f"🟡 WARNING ({ratio*100:.1f}%) — past half bin capacity.", ratio
    return "NORMAL",       f"🟢 NORMAL ({ratio*100:.1f}%) — {remaining} bins available.", ratio

# ============================================================
# SECTION 6: TRIP PAIRING OPTIMIZER
# ============================================================

def find_optimal_pairing():
    """
    Partition the 4 stores into 2 pairs of 2.
    C(4,2) / 2 = 3 unique unordered pairings.

    Algorithm:
      Fix stores[0] in pair1; vary its partner → 3 pairings.
      For each pair, evaluate both orderings:
        Facility → A → B → Facility  vs  Facility → B → A → Facility
      Select minimum-distance ordering per pair.
      Choose the pairing with the smallest combined total km.

    Winning pairing (calculated):
      (Waterloo + Kitchener) & (Guelph + Cambridge) = 116 km/week
      Geographic rationale: W↔K = 14 km (neighbouring cities);
      G↔C = 24 km (nearest pair east of the cluster).
    """
    stores  = list(STORES.keys())
    unique  = [
        ([stores[0], stores[i]], [s for s in stores if s not in [stores[0], stores[i]]])
        for i in range(1, len(stores))
    ]
    best_total = float("inf")
    best_result = None
    comparison  = []

    for p1, p2 in unique:
        trips = []
        total = 0
        for pair in [p1, p2]:
            a, b = pair
            fwd = road_km("Facility",a) + road_km(a,b) + road_km(b,"Facility")
            rev = road_km("Facility",b) + road_km(b,a) + road_km(a,"Facility")
            if fwd <= rev:
                trips.append({"stores":[a,b], "distance_km":round(fwd,1),
                               "order":f"Facility → {a} → {b} → Facility"})
            else:
                trips.append({"stores":[b,a], "distance_km":round(rev,1),
                               "order":f"Facility → {b} → {a} → Facility"})
            total += min(fwd, rev)
        total = round(total, 1)
        comparison.append({
            "pairing" : f"({p1[0]} + {p1[1]}) & ({p2[0]} + {p2[1]})",
            "total_km": total,
        })
        if total < best_total:
            best_total  = total
            best_result = {"trips": trips, "total_km": total}

    for row in comparison:
        row["chosen"] = (row["total_km"] == best_result["total_km"])
    best_result["comparison"] = comparison
    return best_result

# ============================================================
# SECTION 7: SCHEDULER  (bin-aware, Mon + Thu)
# ============================================================

def build_schedule(pairing, profile):
    """
    Assigns trips to days:
      step = ceil(DAYS_PER_WEEK / TRIPS_PER_WEEK) = ceil(5/2) = 3
      → Trip 1: Monday (day 0), Trip 2: Thursday (day 3)
      → 3-day gap between trips.

    Three capacity gates (bin-aware):
      Gate 1  NO_BINS_AVAILABLE
        All available bins are already used → trip deferred.
        Flag: CRITICAL / NO_BINS_AVAILABLE.

      Gate 2  PARTIAL_BIN_LIMIT
        Remaining bins × WEIGHT_PER_BIN_KG < trip waste.
        Collect only what fits; remaining kg flagged as deferred.
        Waste split proportionally by store sqft.
        Flag: WARNING / PARTIAL_BIN_LIMIT.

      Gate 3  OVERTIME_RISK
        Estimated trip time > WORKING_HOURS_MIN.
        Collection proceeds; operator alerted.
        Flag: INFO / OVERTIME_RISK.

    Bin tracking:
      bins_used_week  increments after each successful trip.
      bins_left_after stored per trip for the report.
    """
    step      = ceil(DAYS_PER_WEEK / TRIPS_PER_WEEK)           # = 3
    trip_days = [min(i * step, DAYS_PER_WEEK - 1) for i in range(TRIPS_PER_WEEK)]
    schedule  = {}
    flags     = []
    bins_used = 0

    for i, trip in enumerate(pairing["trips"]):
        day    = trip_days[i]
        stores = trip["stores"]
        waste  = sum(profile[s]["weekly_kg"] for s in stores)

        bins_needed = ceil(waste / WEIGHT_PER_BIN_KG)
        bins_left   = AVAILABLE_BINS - bins_used

        # ── Gate 1: No bins left ─────────────────────────────
        if bins_left <= 0:
            for s in stores:
                profile[s].update({
                    "status_flag" : "DEFERRED — NO BINS",
                    "collected_kg": 0.0,
                    "deferred_kg" : profile[s]["weekly_kg"],
                })
            flags.append({
                "severity": "CRITICAL",
                "type"    : "NO_BINS_AVAILABLE",
                "detail"  : f"Trip {i+1} fully deferred — 0 bins remaining.",
            })
            schedule[i] = {**trip, "day":DAY_NAMES[day], "skipped":True,
                           "waste_kg":0, "bins_needed":0, "bins_left_after":0}
            continue

        # ── Gate 2: Partial bin capacity ─────────────────────
        if bins_needed > bins_left:
            collectible_kg = bins_left * WEIGHT_PER_BIN_KG
            flags.append({
                "severity": "WARNING",
                "type"    : "PARTIAL_BIN_LIMIT",
                "detail"  : (f"Trip {i+1}: only {bins_left} bin(s) left "
                             f"({collectible_kg:,} kg capacity) vs "
                             f"{bins_needed} needed for {waste:,.0f} kg. "
                             f"Partial collection — waste split by store sqft."),
            })
            # Distribute collectible kg proportionally by store sqft
            total_sqft = sum(STORES[s]["size_sqft"] for s in stores)
            for s in stores:
                frac = STORES[s]["size_sqft"] / total_sqft
                profile[s]["collected_kg"] = round(collectible_kg * frac, 1)
                profile[s]["deferred_kg"]  = round(profile[s]["weekly_kg"]
                                                    - profile[s]["collected_kg"], 1)
                profile[s]["status_flag"]  = "PARTIAL COLLECTION"
            waste        = collectible_kg
            bins_needed  = bins_left

        # ── Allocate bins per store ───────────────────────────
        for s in stores:
            profile[s]["bins_allocated"] = ceil(profile[s]["collected_kg"] / WEIGHT_PER_BIN_KG)
        bins_used += bins_needed

        # ── Bin level check after this trip ──────────────────
        bstat, bmsg, bratio = bin_status(bins_used)
        if bstat in ("CRITICAL", "FULL"):
            flags.append({
                "severity": "WARNING",
                "type"    : f"BIN_LEVEL_{bstat}",
                "detail"  : f"After Trip {i+1}: {bmsg}",
            })

        # ── Gate 3: Overtime ─────────────────────────────────
        drive = (drive_min("Facility", stores[0])
                 + drive_min(stores[0], stores[1])
                 + drive_min(stores[1], "Facility"))
        load  = LOADING_TIME_MIN * len(stores)
        total = drive + load
        if total > WORKING_HOURS_MIN:
            flags.append({
                "severity": "INFO",
                "type"    : "OVERTIME_RISK",
                "detail"  : (f"Trip {i+1}: est. {total:.0f} min ({total/60:.1f} hrs) "
                             f"exceeds {WORKING_HOURS_MIN // 60}-hr cap."),
            })

        schedule[i] = {
            **trip,
            "day"            : DAY_NAMES[day],
            "skipped"        : False,
            "drive_min"      : round(drive, 1),
            "load_min"       : load,
            "total_min"      : round(total, 1),
            "waste_kg"       : round(waste, 1),
            "truck_util_pct" : round(waste / TRUCK_CAPACITY_KG * 100, 1),
            "bins_needed"    : bins_needed,
            "bins_left_after": AVAILABLE_BINS - bins_used,
            "overtime"       : total > WORKING_HOURS_MIN,
        }

    return schedule, flags, bins_used

# ============================================================
# SECTION 8: SPLIT-LOAD HANDLER  (edge-case utility)
# ============================================================
#
# With current waste volumes (~870–1,057 kg/store) and a 3,000 kg
# truck, every store fits in a single load.
#
# If TRUCK_CAPACITY_KG is reduced (e.g. to 500 kg for a smaller
# vehicle), loads_per_visit > 1 triggers split-load runs:
#   Each extra load: Facility → Store → Facility
#   Extra km  = (loads-1) × 2 × road_km(Facility, store)
#   Extra min = (loads-1) × (2 × drive_min + LOADING_TIME_MIN)

def compute_split_overhead(store, loads):
    extra     = loads - 1
    extra_km  = round(extra * road_km("Facility", store) * 2, 1)
    extra_min = round(extra * (drive_min("Facility", store) * 2 + LOADING_TIME_MIN), 1)
    return extra_km, extra_min

# ============================================================
# SECTION 9: CONSOLE REPORT
# ============================================================

def print_report(profile, schedule, flags, total_bins_used, pairing):
    SEP  = "=" * 72
    SEP2 = "-" * 67

    bstat_f, bmsg_f, bratio_f = bin_status(total_bins_used)

    print(f"\n{SEP}")
    print("  FARM BOY × BSF WASTE COLLECTION SYSTEM")
    print(SEP)

    # ── Bin Inventory ─────────────────────────────────────────────────
    print(f"\n  BSF FACILITY — LARVAE BIN INVENTORY")
    print(f"  {SEP2}")
    print(f"  Name      : {FACILITY['name']}")
    print(f"  Address   : {FACILITY['address']}")
    print(f"  ─── Bin Specs ─────────────────────────────────────────────────")
    print(f"  Total bins             : {TOTAL_BINS}")
    print(f"  Weight per bin         : {WEIGHT_PER_BIN_KG} kg  "
          f"(200 L commercial BSFL container)")
    print(f"  Total bin capacity     : {TOTAL_BIN_CAPACITY_KG:,} kg  "
          f"({TOTAL_BINS} × {WEIGHT_PER_BIN_KG} kg)")
    print(f"  ─── Weekly State ──────────────────────────────────────────────")
    print(f"  Bins in use (prior larval cycle) : {BINS_IN_USE}  "
          f"← larvae still processing (~12-day cycle)")
    print(f"  Available bins this week         : {AVAILABLE_BINS}  "
          f"({AVAILABLE_BIN_CAPACITY_KG:,} kg capacity)")
    print(f"  Bins filled this week            : {total_bins_used}")
    print(f"  Bins remaining after collections : {AVAILABLE_BINS - total_bins_used}")
    print(f"  Bin utilisation                  : {total_bins_used}/{AVAILABLE_BINS}  "
          f"({bratio_f*100:.1f}%)")
    print(f"  Status                           : {bmsg_f}")

    # ── Alerts ────────────────────────────────────────────────────────
    if flags:
        print(f"\n  ⚠  ALERTS & FLAGS  ({len(flags)} total)")
        print(f"  {SEP2}")
        for fl in flags:
            icon = {"CRITICAL":"🔴","WARNING":"🟡","INFO":"🔵"}.get(fl["severity"],"⚪")
            print(f"  {icon} [{fl['severity']}]  {fl['type']}")
            print(f"       {fl['detail']}")

    # ── Waste Profile ─────────────────────────────────────────────────
    ref_sqft    = STORES[REFERENCE_STORE]["size_sqft"]
    kg_per_sqft = REFERENCE_WEEKLY_KG / ref_sqft
    print(f"\n\n  WASTE PROFILE  (Guelph reference — store-manager confirmed)")
    print(f"  {SEP2}")
    print(f"  Reference : {REFERENCE_STORE}  {ref_sqft:,} sqft  →  "
          f"{REFERENCE_WEEKLY_KG:,} kg/week")
    print(f"  Rate      : {kg_per_sqft:.6f} kg/sqft/week  "
          f"(uniform across cluster — same Farm Boy format)")
    print(f"  Formula   : weekly_kg = (store_sqft / {ref_sqft:,}) × {REFERENCE_WEEKLY_KG:,}\n")
    print(f"  {'Store':<12} {'sqft':>7} {'size ratio':>11} "
          f"{'kg/week':>9} {'Loads':>6} {'Bins':>5}  Assigned trip")
    print("  " + "-" * 68)
    for store, info in profile.items():
        ratio     = STORES[store]["size_sqft"] / ref_sqft
        trip_label = next(
            (f"Trip {tid+1} ({t['day']})" for tid,t in schedule.items()
             if store in t["stores"] and not t.get("skipped")),
            "DEFERRED"
        )
        flag_tag = f"  ← {info['status_flag']}" if info["status_flag"] != "OK" else ""
        print(f"  {store:<12} {STORES[store]['size_sqft']:>7,} {ratio:>11.4f} "
              f"{info['weekly_kg']:>9.1f} {info['loads_per_visit']:>6} "
              f"{info['bins_allocated']:>5}  {trip_label}{flag_tag}")

    print(f"\n  Total weekly waste : {sum(i['weekly_kg'] for i in profile.values()):,.1f} kg")
    print(f"  Truck capacity     : {TRUCK_CAPACITY_KG:,} kg  "
          f"(all stores fit in 1 load each at current volumes)")

    # ── Pairing Comparison ────────────────────────────────────────────
    print(f"\n\n  PAIRING OPTIMIZER — all 3 unique combinations evaluated")
    print(f"  {SEP2}")
    for row in pairing["comparison"]:
        mark = "  ← SELECTED (minimum distance)" if row["chosen"] else ""
        print(f"  {row['pairing']:<48} {row['total_km']:>6.1f} km{mark}")

    # ── Schedule ──────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print(f"  WEEKLY SCHEDULE  (2 trips — Monday + Thursday, 3-day gap)")
    print(SEP)

    for tid, trip in schedule.items():
        if trip.get("skipped"):
            print(f"\n  TRIP {tid+1} — {trip['day'].upper()}  "
                  f"🔴 SKIPPED — no bins available")
            continue

        ot  = "  ⚠ OVERTIME" if trip["overtime"] else ""
        s0_, s1_ = trip["stores"]
        print(f"\n  TRIP {tid+1} — {trip['day'].upper()}{ot}")
        print("  " + "─" * 66)
        print(f"  Route      : {trip['order']}")
        print(f"  Legs       : Facility → {s0_}: {road_km('Facility',s0_)} km  |  "
              f"{s0_} → {s1_}: {road_km(s0_,s1_)} km  |  "
              f"{s1_} → Facility: {road_km(s1_,'Facility')} km")
        print(f"  Distance   : {trip['distance_km']} km  (real road, semi-truck route)")
        print(f"  Time       : {trip['drive_min']:.0f} min drive  +  "
              f"{trip['load_min']} min loading  =  "
              f"{trip['total_min']:.0f} min ({trip['total_min']/60:.1f} hrs)")
        print(f"  Waste      : {trip['waste_kg']:,.1f} kg  |  "
              f"Truck utilization: {trip['truck_util_pct']:.1f}%")
        print(f"  Bins used  : {trip['bins_needed']}  |  "
              f"Bins remaining after trip: {trip['bins_left_after']}")
        for s in trip["stores"]:
            print(f"    └─ {s:<12} : {profile[s]['weekly_kg']:>7.1f} kg  "
                  f"({profile[s]['bins_allocated']} bin(s))")

    # ── Weekly Totals ─────────────────────────────────────────────────
    total_km    = sum(t["distance_km"] for t in schedule.values())
    total_min   = sum(t["total_min"]   for t in schedule.values())
    total_wk    = sum(p["weekly_kg"]   for p in profile.values())
    total_def   = sum(p["deferred_kg"] for p in profile.values())

    print(f"\n{SEP}")
    print("  WEEKLY TOTALS")
    print(f"  Distance         : {total_km:.1f} km  ({TRIPS_PER_WEEK} trips)")
    print(f"  Operational time : {total_min:.0f} min  ({total_min/60:.1f} hrs combined)")
    print(f"  Waste collected  : {total_wk - total_def:,.1f} kg")
    if total_def > 0:
        print(f"  ⚠  Waste deferred: {total_def:,.1f} kg")
    print(f"  Bin utilisation  : {total_bins_used} / {AVAILABLE_BINS}  "
          f"({bratio_f*100:.1f}%)  {bmsg_f}")
    print(f"  Bin summary      : {TOTAL_BINS} total  |  "
          f"{BINS_IN_USE} in active larval cycle  |  "
          f"{AVAILABLE_BINS - total_bins_used} free after week")
    print(SEP)

# ============================================================
# SECTION 10: INTERACTIVE MAP
# ============================================================

def generate_map(profile, schedule, total_bins_used):
    ROUTE_COLORS = ["#e74c3c", "#3498db"]
    fac_c = FACILITY["coords"]
    bstat_f, bmsg_f, bratio_f = bin_status(total_bins_used)

    m = folium.Map(location=[43.46, -80.39], zoom_start=11)

    # ── BSF Facility marker ──────────────────────────────────────────
    fac_popup = (
        f"<b>{FACILITY['name']}</b><br>"
        f"{FACILITY['address']}<br><br>"
        f"<b>Bin Inventory</b><br>"
        f"Total bins: {TOTAL_BINS}  |  Weight/bin: {WEIGHT_PER_BIN_KG} kg<br>"
        f"Total capacity: {TOTAL_BIN_CAPACITY_KG:,} kg<br>"
        f"Bins in use (prior cycle): {BINS_IN_USE}<br>"
        f"Available this week: {AVAILABLE_BINS}  ({AVAILABLE_BIN_CAPACITY_KG:,} kg)<br>"
        f"Filled this week: {total_bins_used}<br>"
        f"Status: {bmsg_f}"
    )
    folium.Marker(
        location=fac_c,
        popup=folium.Popup(fac_popup, max_width=310),
        tooltip="BSF Facility — Woolwich Township",
        icon=folium.Icon(color="black", icon="leaf", prefix="fa"),
    ).add_to(m)

    # ── Store markers ─────────────────────────────────────────────────
    for store, data in STORES.items():
        info      = profile[store]
        radius    = max(6, info["weekly_kg"] / 60)
        trip_lbl  = next(
            (f"Trip {tid+1} — {t['day']}" for tid,t in schedule.items()
             if store in t["stores"] and not t.get("skipped")),
            "DEFERRED"
        )
        popup_html = (
            f"<b>{store}</b><br>{data['address']}<br>"
            f"Size: {data['size_sqft']:,} sqft<br>"
            f"Weekly waste: {info['weekly_kg']:,.1f} kg<br>"
            f"(proportional to Guelph 1,000 kg/week reference)<br>"
            f"Loads/visit: {info['loads_per_visit']}<br>"
            f"Bins allocated: {info['bins_allocated']}<br>"
            f"Collection: {trip_lbl}"
        )
        folium.CircleMarker(
            location=data["coords"],
            radius=radius,
            popup=folium.Popup(popup_html, max_width=270),
            tooltip=f"{store} — {info['weekly_kg']:,.1f} kg/wk",
            color=data["color"], fill=True, fillOpacity=0.75,
        ).add_to(m)

    # ── Route polylines ───────────────────────────────────────────────
    active = [t for t in schedule.values() if not t.get("skipped")]
    for i, trip in enumerate(active):
        s0_, s1_ = trip["stores"]
        coords = [fac_c, STORES[s0_]["coords"], STORES[s1_]["coords"], fac_c]
        label  = (f"Trip {i+1} — {trip['day']}: "
                  f"{trip['order']}  |  "
                  f"{trip['distance_km']} km  |  "
                  f"{trip['total_min']:.0f} min  |  "
                  f"{trip['truck_util_pct']}% truck util  |  "
                  f"{trip['bins_needed']} bins")
        folium.PolyLine(
            locations=coords,
            color=ROUTE_COLORS[i % len(ROUTE_COLORS)],
            weight=4, opacity=0.85, tooltip=label, dash_array="6 4",
        ).add_to(m)

    # ── Legend + bin gauge ────────────────────────────────────────────
    fill_pct  = min(100, bratio_f * 100)
    bar_color = ("#e74c3c" if fill_pct>=90 else "#f39c12" if fill_pct>=75
                 else "#f1c40f" if fill_pct>=50 else "#2ecc71")
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                background:white;padding:14px;border:2px solid #aaa;
                border-radius:8px;font-size:13px;line-height:2;min-width:230px;">
        <b>🗓 Trip Legend</b><br>
        {"".join(
            f'<span style="color:{ROUTE_COLORS[i%len(ROUTE_COLORS)]}">━━</span> Trip {i+1} — {t["day"]}<br>'
            for i,t in enumerate(active)
        )}
        <hr style="margin:6px 0;">
        <b>🌿 Larvae Bin Usage</b><br>
        <small>{total_bins_used} / {AVAILABLE_BINS} bins  ({fill_pct:.0f}%)</small><br>
        <div style="background:#eee;border-radius:4px;height:14px;width:180px;">
          <div style="background:{bar_color};width:{fill_pct:.0f}%;height:14px;
                      border-radius:4px;"></div>
        </div>
        <small>Total facility bins: {TOTAL_BINS}  |  In larval cycle: {BINS_IN_USE}</small>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend_html))
    m.save("waste_collection_routes.html")
    print("\n  Interactive map saved → waste_collection_routes.html")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    profile                         = compute_waste_profile()
    pairing                         = find_optimal_pairing()
    schedule, flags, total_bins_used = build_schedule(pairing, profile)
    print_report(profile, schedule, flags, total_bins_used, pairing)
    generate_map(profile, schedule, total_bins_used)
