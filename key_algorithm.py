"""
waste_collection_routing.py

Farm Boy × BSF Waste Collection — Guelph / KW Cluster
Stores   : Guelph | Kitchener | Waterloo | Cambridge
Facility : BSF Processing Plant — Woolwich Township farmland

Schedule : 2 trips/week (Monday + Thursday), 2 stores per trip
Distances: Real road distances (Rome2Rio, DistanceCalculator.net, Wanderlog)

BIN CAPACITY MODEL  (single source of truth):
  All kg capacity is derived exclusively from the bin hierarchy.
  There is NO separate FACILITY_CAPACITY_KG variable.

  FACILITY_BIN_CAPACITY              ← physical bin slots built into facility
        │                               (max potential if all slots filled)
        │
        └── BINS_OWNED               ← bins currently on-site / procured
                │                       (≤ FACILITY_BIN_CAPACITY)
                │
                ├── BINS_IN_USE      ← locked in active ~12-day larval cycle
                │                       pre-committed at week start
                └── BINS_AVAILABLE   ← ready to receive new waste this week

  Derived kg equivalents (auto-computed):
    FACILITY_BIN_CAPACITY_KG  = FACILITY_BIN_CAPACITY × WEIGHT_PER_BIN_KG
    OWNED_BIN_CAPACITY_KG     = BINS_OWNED             × WEIGHT_PER_BIN_KG
    AVAILABLE_BIN_CAPACITY_KG = BINS_AVAILABLE          × WEIGHT_PER_BIN_KG

  FACILITY_CURRENT_FILL_KG is also derived:
    = BINS_IN_USE × WEIGHT_PER_BIN_KG
  This ensures kg fill and bin count are always consistent.

  Three utilisation ratios reported:
    Facility slot util  = (BINS_IN_USE + filled_this_week) / FACILITY_BIN_CAPACITY
    Owned-bin util      = (BINS_IN_USE + filled_this_week) / BINS_OWNED
    Available consumed  = filled_this_week / BINS_AVAILABLE
"""

from math import ceil
import folium
import requests
import time

# ============================================================
# SECTION 1: OPERATIONAL PARAMETERS  ← tune all values here
# ============================================================

# ── Truck ────────────────────────────────────────────────────
TRUCK_CAPACITY_KG   = 3_000   # kg per load
TRUCK_SPEED_KPH     = 55      # semi-truck average road speed
LOADING_TIME_MIN    = 30      # load/unload time per store stop (min)
TRIPS_PER_WEEK      = 2       # collection runs per week
DAYS_PER_WEEK       = 5       # Mon–Fri
WORKING_HOURS_MIN   = 480     # 8-hr daily cap

# ── BSF Larvae Bin Hierarchy ─────────────────────────────────
#
#   FACILITY_BIN_CAPACITY          ← physical bin slots built into facility
#         │                           (ceiling — room to procure more bins)
#         │
#         └── BINS_OWNED           ← bins currently on-site / procured
#                 │                   (≤ FACILITY_BIN_CAPACITY)
#                 │
#                 ├── BINS_IN_USE  ← locked in active larval cycle
#                 │                  (~12-day BSFL processing, not available)
#                 │
#                 └── BINS_AVAILABLE ← ready to receive new waste this week
#
FACILITY_BIN_CAPACITY = 100   # max bin slots the facility infrastructure supports
BINS_OWNED            = 50    # bins currently on-site / procured
BINS_IN_USE           = 10    # bins occupied by ongoing larval cycle
WEIGHT_PER_BIN_KG     = 100   # max organic waste per bin (kg) — 200 L commercial BSFL container

# ── Derived capacity (auto-computed — do not edit) ───────────
BINS_AVAILABLE            = BINS_OWNED    - BINS_IN_USE
BIN_SLOTS_EMPTY           = FACILITY_BIN_CAPACITY - BINS_OWNED

#   kg equivalents — three levels matching the bin hierarchy above
FACILITY_BIN_CAPACITY_KG  = FACILITY_BIN_CAPACITY * WEIGHT_PER_BIN_KG  # 10,000 kg — max potential if all slots filled
OWNED_BIN_CAPACITY_KG     = BINS_OWNED    * WEIGHT_PER_BIN_KG           #  5,000 kg — capacity of bins we currently own
AVAILABLE_BIN_CAPACITY_KG = BINS_AVAILABLE * WEIGHT_PER_BIN_KG          #  4,000 kg — usable intake capacity this week

#   Pre-committed kg at week start (bins already in active cycle)
#   Derived so kg fill and bin count are always consistent.
FACILITY_CURRENT_FILL_KG  = BINS_IN_USE   * WEIGHT_PER_BIN_KG           #  1,000 kg — already committed

# ── Fill alert thresholds (fraction of OWNED_BIN_CAPACITY_KG) ─
# All alerts relative to what you own — not the theoretical max.
BINS_WARN     = 0.50   # >= 50%  of OWNED_BIN_CAPACITY_KG → WARNING   (>= 2,500 kg)
BINS_CAUTION  = 0.75   # >= 75%  of OWNED_BIN_CAPACITY_KG → CAUTION   (>= 3,750 kg)
BINS_CRITICAL = 0.90   # >= 90%  of OWNED_BIN_CAPACITY_KG → CRITICAL  (>= 4,500 kg)

# ── Waste reference ← store-manager confirmed ────────────────
REFERENCE_STORE     = "Guelph"
REFERENCE_WEEKLY_KG = 1_000   # kg/week confirmed by Guelph store manager

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# ============================================================
# SECTION 2: FACILITY & STORE DATA
# ============================================================

FACILITY = {
    "name"   : "BSF Processing Facility — Woolwich Township (Breslau area)",
    "coords" : [43.490, -80.420],
    "address": "Woolwich Township, ON  (rural / agricultural zone)",
}

STORES = {
    "Waterloo": {
        "coords": [43.4855568, -80.5274827],
        "address": "417 King Street North, Waterloo, ON",
        "size_sqft": 26_725, "color": "blue",
    },
    "Guelph": {
        "coords": [43.5161018, -80.2369272],
        "address": "370 Stone Road West, Guelph, ON",
        "size_sqft": 25_276, "color": "purple",
    },
    "Cambridge": {
        "coords": [43.3935986, -80.3206588],
        "address": "350 Hespeler Road, Cambridge, ON",
        "size_sqft": 22_800, "color": "orange",
    },
    "Kitchener": {
        "coords": [43.4209679, -80.4404296],
        "address": "385 Fairway Road South, Kitchener, ON",
        "size_sqft": 22_000, "color": "green",
    },
}

# ============================================================
# SECTION 3: REAL ROAD DISTANCE MATRIX (km)
# ============================================================
# F=Facility (Woolwich Township, Breslau [43.490, -80.420])
# F↔G  Rome2Rio Breslau–Guelph 18 km + facility offset → 20 km
# F↔C  Rome2Rio Breslau–Cambridge 16 km + offset → 18 km
# F↔W  Road via Hwy 7; Breslau–Waterloo ~18 km
# F↔K  Rural Routes "Breslau 19 km NW of Kitchener"; Fairway Rd S → 22 km
# W↔K  DistanceCalculator.net city-centre 6 km; King St N ↔ Fairway Rd S → 14 km
# W↔G  Rome2Rio confirmed 27 km
# W↔C  Via Hwy 401 corridor → 30 km
# K↔G  Rome2Rio confirmed 28 km
# K↔C  Wanderlog confirmed 21 km (13.1 mi)
# G↔C  Rome2Rio confirmed 24 km
ROAD_DIST_KM = {
    ("Facility","Waterloo") :18, ("Facility","Kitchener"):22,
    ("Facility","Guelph")   :20, ("Facility","Cambridge"):18,
    ("Waterloo","Kitchener"):14, ("Waterloo","Guelph")   :27,
    ("Waterloo","Cambridge"):30, ("Kitchener","Guelph")  :28,
    ("Kitchener","Cambridge"):21,("Guelph","Cambridge")  :24,
}

def road_km(a, b):
    if a == b: return 0
    return ROAD_DIST_KM.get((a,b)) or ROAD_DIST_KM.get((b,a))

def drive_min(a, b):
    return (road_km(a, b) / TRUCK_SPEED_KPH) * 60

# ============================================================
# SECTION 3b: OSRM ROUTING UTILITIES
# ============================================================
# Uses the public OSRM demo server — no API key required.
# Set USE_OSRM_DISTANCES = True  to auto-update ROAD_DIST_KM at startup
#     using real road distances fetched from OSRM.
# Set USE_OSRM_DISTANCES = False to keep the hardcoded matrix above.

OSRM_BASE_URL      = "http://router.project-osrm.org/route/v1/driving"
OSRM_TIMEOUT_SEC   = 10
USE_OSRM_DISTANCES = True   # ← flip to False to run fully offline


def fetch_osrm_route(waypoints):
    """
    Fetch real road geometry + distance + duration from OSRM.

    waypoints : list of (lat, lon) tuples  e.g. [(43.49, -80.42), ...]
    Returns   : (geometry, distance_km, duration_min)
                geometry = list of [lat, lon] pairs ready for folium PolyLine
                Falls back to (straight-line geometry, None, None) on failure.
    """
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url        = f"{OSRM_BASE_URL}/{coords_str}?overview=full&geometries=geojson"
    try:
        resp = requests.get(url, timeout=OSRM_TIMEOUT_SEC)
        data = resp.json()
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM code: {data.get('code')}")
        route    = data["routes"][0]
        geometry = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
        return geometry, round(route["distance"] / 1000, 1), round(route["duration"] / 60, 1)
    except Exception as e:
        print(f"  ⚠ OSRM fallback (straight line): {e}")
        return [[lat, lon] for lat, lon in waypoints], None, None


def build_osrm_distance_matrix():
    """
    Calls OSRM for every pair in ROAD_DIST_KM and returns an updated
    distance dict.  Falls back to the hardcoded value on any failure.
    Prints a comparison so you can see drift from manually sourced values.
    """
    nodes = {"Facility": tuple(FACILITY["coords"])}
    nodes.update({s: tuple(STORES[s]["coords"]) for s in STORES})

    print("\n  Building OSRM distance matrix …")
    updated = {}
    for (a, b), manual_km in ROAD_DIST_KM.items():
        _, dist_km, dur_min = fetch_osrm_route([nodes[a], nodes[b]])
        if dist_km is not None:
            updated[(a, b)] = dist_km
            delta = dist_km - manual_km
            flag  = "  ✅" if abs(delta) <= 2 else f"  ⚠ Δ{delta:+.1f} km vs manual {manual_km} km"
            print(f"    {a:<12} ↔ {b:<12} : {dist_km:>6.1f} km  ({dur_min:.0f} min){flag}")
        else:
            updated[(a, b)] = manual_km
            print(f"    {a:<12} ↔ {b:<12} : {manual_km:>6.1f} km  (hardcoded fallback)")
        time.sleep(0.15)   # polite rate-limit on public OSRM server
    return updated


# ============================================================
# SECTION 4: WASTE PROFILE
# ============================================================

def compute_waste_profile():
    """
    Guelph 1,000 kg/week confirmed by store manager.
    All others: weekly_kg = (store_sqft / guelph_sqft) × 1,000
    """
    ref_sqft = STORES[REFERENCE_STORE]["size_sqft"]
    kps = REFERENCE_WEEKLY_KG / ref_sqft
    profile = {}
    for store, data in STORES.items():
        wk = data["size_sqft"] * kps
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
# SECTION 5: STATUS CHECKERS
# ============================================================

def bin_utilisation(bins_filled_this_week):
    """
    All three utilisation ratios — single source of truth for capacity reporting.

      facility_slot_util  = (BINS_IN_USE + bins_filled) / FACILITY_BIN_CAPACITY
      owned_bin_util      = (BINS_IN_USE + bins_filled) / BINS_OWNED
      available_consumed  = bins_filled / BINS_AVAILABLE

    Alert level driven by owned_bin_util (most operationally actionable):
      NORMAL   < 50% of OWNED_BIN_CAPACITY_KG
      WARNING  >= 50%
      CAUTION  >= 75%
      CRITICAL >= 90%
      FULL     = 100%
    """
    total_in_use   = BINS_IN_USE + bins_filled_this_week
    fac_util       = total_in_use / FACILITY_BIN_CAPACITY
    owned_util     = total_in_use / BINS_OWNED if BINS_OWNED > 0 else 1.0
    avail_consumed = bins_filled_this_week / BINS_AVAILABLE if BINS_AVAILABLE > 0 else 1.0

    if owned_util >= 1.0:             level, icon = "FULL",     "🚫"
    elif owned_util >= BINS_CRITICAL: level, icon = "CRITICAL", "🔴"
    elif owned_util >= BINS_CAUTION:  level, icon = "CAUTION",  "🟠"
    elif owned_util >= BINS_WARN:     level, icon = "WARNING",  "🟡"
    else:                             level, icon = "NORMAL",   "🟢"

    return {
        "level"              : level,
        "icon"               : icon,
        "facility_slot_util" : fac_util,
        "owned_bin_util"     : owned_util,
        "avail_consumed"     : avail_consumed,
        "total_in_use"       : total_in_use,
        "bins_free_after"    : BINS_OWNED - total_in_use,
        "slots_unprocured"   : BIN_SLOTS_EMPTY,
        "fill_kg"            : total_in_use * WEIGHT_PER_BIN_KG,
    }

# ============================================================
# SECTION 6: TRIP PAIRING OPTIMIZER
# ============================================================

def find_optimal_pairing():
    """
    Partition 4 stores into 2 pairs. C(4,2)/2 = 3 unique pairings.
    Evaluate both route orderings per pair; pick minimum combined km.
    Winner: (Waterloo+Kitchener) & (Guelph+Cambridge) = 116 km/week.
    """
    stores = list(STORES.keys())
    unique = [([stores[0], stores[i]], [s for s in stores if s not in [stores[0], stores[i]]])
              for i in range(1, len(stores))]
    best_total = float("inf"); best_result = None; comparison = []
    for p1, p2 in unique:
        trips = []; total = 0
        for pair in [p1, p2]:
            a, b = pair
            fwd = road_km("Facility",a)+road_km(a,b)+road_km(b,"Facility")
            rev = road_km("Facility",b)+road_km(b,a)+road_km(a,"Facility")
            if fwd <= rev:
                trips.append({"stores":[a,b],"distance_km":round(fwd,1),"order":f"Facility → {a} → {b} → Facility"})
            else:
                trips.append({"stores":[b,a],"distance_km":round(rev,1),"order":f"Facility → {b} → {a} → Facility"})
            total += min(fwd, rev)
        total = round(total, 1)
        comparison.append({"pairing":f"({p1[0]}+{p1[1]}) & ({p2[0]}+{p2[1]})","total_km":total})
        if total < best_total:
            best_total = total; best_result = {"trips":trips,"total_km":total}
    for row in comparison: row["chosen"] = (row["total_km"] == best_result["total_km"])
    best_result["comparison"] = comparison
    return best_result

# ============================================================
# SECTION 7: SCHEDULER  (bin-aware, Mon + Thu)
# ============================================================

def build_schedule(pairing, profile):
    """
    Trip days: step=ceil(5/2)=3 → Monday(0) + Thursday(3).

    Capacity ceiling = AVAILABLE_BIN_CAPACITY_KG = BINS_AVAILABLE × WEIGHT_PER_BIN_KG.
    (Derived entirely from bins — no separate kg ceiling variable.)

    FACILITY_CURRENT_FILL_KG = BINS_IN_USE × WEIGHT_PER_BIN_KG is the
    pre-committed kg at week start; facility_fill tracks cumulative kg.

    Gates per trip (in order):
      Gate 1  NO_BINS_AVAILABLE  — BINS_AVAILABLE exhausted.
                                   Since kg is derived from bins, this is
                                   the only hard stop needed.
      Gate 2  PARTIAL_COLLECTION — bins_left × WEIGHT_PER_BIN_KG < trip waste.
                                   Collect what fits; split by store sqft.
      Gate 3  OVERTIME_RISK      — trip time > WORKING_HOURS_MIN.
    """
    step          = ceil(DAYS_PER_WEEK / TRIPS_PER_WEEK)   # = 3
    trip_days     = [min(i*step, DAYS_PER_WEEK-1) for i in range(TRIPS_PER_WEEK)]
    schedule      = {}; flags = []
    bins_filled   = 0
    facility_fill = FACILITY_CURRENT_FILL_KG   # starts at BINS_IN_USE × WEIGHT_PER_BIN_KG

    for i, trip in enumerate(pairing["trips"]):
        day    = trip_days[i]
        stores = trip["stores"]
        waste  = sum(profile[s]["weekly_kg"] for s in stores)

        bins_needed = ceil(waste / WEIGHT_PER_BIN_KG)
        bins_left   = BINS_AVAILABLE - bins_filled   # available pool remaining

        # ── Gate 1: No bins left ──────────────────────────────
        if bins_left <= 0:
            for s in stores:
                profile[s].update({"status_flag":"DEFERRED — NO BINS",
                                   "collected_kg":0.0,"deferred_kg":profile[s]["weekly_kg"]})
            flags.append({"severity":"CRITICAL","type":"NO_BINS_AVAILABLE",
                          "detail":(f"Trip {i+1} deferred — 0 of {BINS_AVAILABLE} available bins left "
                                    f"(owned capacity {OWNED_BIN_CAPACITY_KG:,} kg fully committed).")})
            schedule[i] = {**trip,"day":DAY_NAMES[day],"skipped":True,
                           "waste_kg":0,"bins_needed":0,"bins_left_after":0,
                           "fac_fill_after":facility_fill}
            continue

        # ── Gate 2: Partial collection ────────────────────────
        bin_cap_left = bins_left * WEIGHT_PER_BIN_KG
        if bin_cap_left < waste:
            flags.append({"severity":"WARNING","type":"PARTIAL_COLLECTION",
                          "detail":(f"Trip {i+1}: only {bins_left} bin(s) left "
                                    f"({bin_cap_left:,} kg) vs {bins_needed} bins needed "
                                    f"for {waste:,.0f} kg. Collecting proportionally by store sqft.")})
            total_sqft = sum(STORES[s]["size_sqft"] for s in stores)
            for s in stores:
                frac = STORES[s]["size_sqft"] / total_sqft
                profile[s]["collected_kg"] = round(bin_cap_left * frac, 1)
                profile[s]["deferred_kg"]  = round(profile[s]["weekly_kg"] - profile[s]["collected_kg"], 1)
                profile[s]["status_flag"]  = "PARTIAL COLLECTION"
            waste       = bin_cap_left
            bins_needed = bins_left

        # ── Allocate bins per store ────────────────────────────
        for s in stores:
            profile[s]["bins_allocated"] = ceil(profile[s]["collected_kg"] / WEIGHT_PER_BIN_KG)
        bins_filled   += bins_needed
        facility_fill += waste

        # ── Post-trip utilisation alerts ──────────────────────
        bu = bin_utilisation(bins_filled)
        if bu["level"] in ("CRITICAL","FULL"):
            flags.append({"severity":"WARNING","type":f"BIN_LEVEL_{bu['level']}",
                          "detail":(f"After Trip {i+1}: owned-bin util "
                                    f"{bu['owned_bin_util']*100:.1f}% "
                                    f"({bu['total_in_use']}/{BINS_OWNED} bins  |  "
                                    f"{facility_fill:,.0f}/{OWNED_BIN_CAPACITY_KG:,} kg).")})

        # ── Gate 3: Overtime ──────────────────────────────────
        drive = (drive_min("Facility",stores[0])+drive_min(stores[0],stores[1])
                 +drive_min(stores[1],"Facility"))
        load  = LOADING_TIME_MIN * len(stores)
        total = drive + load
        if total > WORKING_HOURS_MIN:
            flags.append({"severity":"INFO","type":"OVERTIME_RISK",
                          "detail":f"Trip {i+1}: {total:.0f} min ({total/60:.1f} hrs) > {WORKING_HOURS_MIN//60}-hr cap."})

        schedule[i] = {**trip,"day":DAY_NAMES[day],"skipped":False,
                       "drive_min":round(drive,1),"load_min":load,"total_min":round(total,1),
                       "waste_kg":round(waste,1),
                       "truck_util_pct":round(waste/TRUCK_CAPACITY_KG*100,1),
                       "bins_needed":bins_needed,
                       "bins_left_after":BINS_AVAILABLE-bins_filled,
                       "fac_fill_after":round(facility_fill,1),
                       "overtime":total>WORKING_HOURS_MIN}

    return schedule, flags, bins_filled, facility_fill

# ============================================================
# SECTION 8: SPLIT-LOAD UTILITY  (edge case)
# ============================================================

def compute_split_overhead(store, loads):
    """
    When loads_per_visit > 1 (only if TRUCK_CAPACITY_KG is reduced),
    each extra run: Facility → Store → Facility.
    """
    extra     = loads - 1
    extra_km  = round(extra * road_km("Facility", store) * 2, 1)
    extra_min = round(extra * (drive_min("Facility", store)*2 + LOADING_TIME_MIN), 1)
    return extra_km, extra_min

# ============================================================
# SECTION 9: CONSOLE REPORT
# ============================================================

def print_report(profile, schedule, flags, bins_filled, final_fill, pairing):
    SEP = "=" * 72
    bu  = bin_utilisation(bins_filled)
    bu0 = bin_utilisation(0)   # state before any collections this week

    print(f"\n{SEP}")
    print("  FARM BOY × BSF WASTE COLLECTION SYSTEM")
    print(SEP)

    # ── Bin capacity overview ─────────────────────────────────────────
    print(f"\n  BSF FACILITY — BIN CAPACITY & UTILISATION")
    print(f"  {'-'*67}")
    print(f"  Facility  : {FACILITY['name']}")
    print(f"  Address   : {FACILITY['address']}")

    print(f"\n  ── Bin Infrastructure ───────────────────────────────────────────")
    print(f"  Facility bin capacity   : {FACILITY_BIN_CAPACITY:>5}  bins  "
          f"(physical slots built into facility)")
    print(f"  Facility kg capacity    : {FACILITY_BIN_CAPACITY_KG:>5,}  kg    "
          f"(if all {FACILITY_BIN_CAPACITY} slots filled with bins)")
    print(f"  Bins owned / on-site    : {BINS_OWNED:>5}  bins  "
          f"= {OWNED_BIN_CAPACITY_KG:,} kg  "
          f"({BINS_OWNED/FACILITY_BIN_CAPACITY*100:.0f}% of facility capacity)  "
          f"[{BIN_SLOTS_EMPTY} slots unprocured]")
    print(f"  Weight per bin          : {WEIGHT_PER_BIN_KG:>5}  kg    "
          f"(200 L commercial BSFL container)")

    print(f"\n  ── Weekly Bin State (start of week) ─────────────────────────────")
    print(f"  Bins in active cycle    : {BINS_IN_USE:>5}  bins  "
          f"= {FACILITY_CURRENT_FILL_KG:,} kg pre-committed  "
          f"(~12-day BSFL processing, not available)")
    print(f"  Bins available          : {BINS_AVAILABLE:>5}  bins  "
          f"= {AVAILABLE_BIN_CAPACITY_KG:,} kg intake capacity this week")

    print(f"\n  ── Utilisation After This Week's Collections ────────────────────")
    print(f"  Bins filled this week   : {bins_filled:>5}  bins  "
          f"(of {BINS_AVAILABLE} available)")
    print(f"  Total bins in use after : {bu['total_in_use']:>5}  bins  "
          f"= {bu['fill_kg']:,} kg  (in-cycle + newly filled)")
    print(f"  Bins free after week    : {bu['bins_free_after']:>5}  bins")
    print()
    print(f"  Facility slot util      :  {bu['total_in_use']:>3} / {FACILITY_BIN_CAPACITY:<5}"
          f"= {bu['facility_slot_util']*100:>5.1f}%  "
          f"(total bins in use / facility capacity)")
    print(f"  Owned-bin util          :  {bu['total_in_use']:>3} / {BINS_OWNED:<5}"
          f"= {bu['owned_bin_util']*100:>5.1f}%  "
          f"(total bins in use / bins owned)  {bu['icon']} {bu['level']}")
    print(f"  Available-bin consumed  :  {bins_filled:>3} / {BINS_AVAILABLE:<5}"
          f"= {bu['avail_consumed']*100:>5.1f}%  "
          f"(filled this week / available this week)")
    print(f"\n  Owned kg utilisation    :  {bu['fill_kg']:,} / {OWNED_BIN_CAPACITY_KG:,} kg"
          f"  = {bu['owned_bin_util']*100:.1f}%  {bu['icon']} {bu['level']}")

    # ── Alerts ────────────────────────────────────────────────────────
    if flags:
        print(f"\n  ⚠  ALERTS & FLAGS  ({len(flags)} total)")
        print(f"  {'-'*67}")
        for fl in flags:
            icon = {"CRITICAL":"🔴","WARNING":"🟡","INFO":"🔵"}.get(fl["severity"],"⚪")
            print(f"  {icon} [{fl['severity']}]  {fl['type']}")
            print(f"       {fl['detail']}")

    # ── Waste profile ─────────────────────────────────────────────────
    ref_sqft = STORES[REFERENCE_STORE]["size_sqft"]
    kps = REFERENCE_WEEKLY_KG / ref_sqft
    print(f"\n\n  WASTE PROFILE  (Guelph reference — store-manager confirmed)")
    print(f"  {'-'*67}")
    print(f"  Reference : Guelph  {ref_sqft:,} sqft  →  {REFERENCE_WEEKLY_KG:,} kg/week")
    print(f"  Rate      : {kps:.6f} kg/sqft/week  (uniform across cluster)")
    print(f"  Formula   : weekly_kg = (store_sqft / {ref_sqft:,}) × {REFERENCE_WEEKLY_KG:,}\n")
    print(f"  {'Store':<12} {'sqft':>7} {'size ratio':>11} {'kg/week':>9} {'Loads':>6} {'Bins':>5}  Assigned trip")
    print("  " + "-" * 68)
    for store, info in profile.items():
        ratio = STORES[store]["size_sqft"] / ref_sqft
        trip_lbl = next((f"Trip {tid+1} ({t['day']})" for tid,t in schedule.items()
                          if store in t["stores"] and not t.get("skipped")), "DEFERRED")
        flag_tag = f"  ← {info['status_flag']}" if info["status_flag"] != "OK" else ""
        print(f"  {store:<12} {STORES[store]['size_sqft']:>7,} {ratio:>11.4f} "
              f"{info['weekly_kg']:>9.1f} {info['loads_per_visit']:>6} "
              f"{info['bins_allocated']:>5}  {trip_lbl}{flag_tag}")
    print(f"\n  Total weekly waste : {sum(p['weekly_kg'] for p in profile.values()):,.1f} kg")
    print(f"  Truck capacity     : {TRUCK_CAPACITY_KG:,} kg  (all stores = 1 load/visit)")

    # ── Pairing comparison ────────────────────────────────────────────
    print(f"\n\n  PAIRING OPTIMIZER — all 3 unique combinations evaluated")
    print(f"  {'-'*67}")
    for row in pairing["comparison"]:
        mark = "  ← SELECTED (minimum distance)" if row["chosen"] else ""
        print(f"  {row['pairing']:<48} {row['total_km']:>6.1f} km{mark}")

    # ── Weekly schedule ───────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print(f"  WEEKLY SCHEDULE  (2 trips — Monday + Thursday, 3-day gap)")
    print(SEP)
    for tid, trip in schedule.items():
        if trip.get("skipped"):
            print(f"\n  TRIP {tid+1} — {trip['day'].upper()}  🔴 SKIPPED — no bins available")
            continue
        ot = "  ⚠ OVERTIME" if trip["overtime"] else ""
        s0_, s1_ = trip["stores"]
        print(f"\n  TRIP {tid+1} — {trip['day'].upper()}{ot}")
        print("  " + "─" * 66)
        print(f"  Route          : {trip['order']}")
        print(f"  Legs           : Facility→{s0_}: {road_km('Facility',s0_)} km  |  "
              f"{s0_}→{s1_}: {road_km(s0_,s1_)} km  |  {s1_}→Facility: {road_km(s1_,'Facility')} km")
        print(f"  Distance       : {trip['distance_km']} km  (real road, semi-truck)")
        print(f"  Time           : {trip['drive_min']:.0f} min drive + {trip['load_min']} min loading "
              f"= {trip['total_min']:.0f} min ({trip['total_min']/60:.1f} hrs)")
        print(f"  Waste          : {trip['waste_kg']:,.1f} kg  |  Truck util: {trip['truck_util_pct']:.1f}%")
        print(f"  Bins used      : {trip['bins_needed']}  |  Bins left (available pool): {trip['bins_left_after']}")
        print(f"  Owned kg fill  : {trip['fac_fill_after']:,.1f} / {OWNED_BIN_CAPACITY_KG:,} kg  "
              f"({trip['fac_fill_after']/OWNED_BIN_CAPACITY_KG*100:.1f}%)")
        for s in trip["stores"]:
            print(f"    └─ {s:<12} : {profile[s]['weekly_kg']:>7.1f} kg  ({profile[s]['bins_allocated']} bin(s))")

    # ── Weekly totals ─────────────────────────────────────────────────
    total_km  = sum(t["distance_km"] for t in schedule.values())
    total_min = sum(t["total_min"]   for t in schedule.values())
    total_col = sum(p["collected_kg"] for p in profile.values())
    total_def = sum(p["deferred_kg"]  for p in profile.values())
    print(f"\n{SEP}")
    print("  WEEKLY TOTALS")
    print(f"  Distance          : {total_km:.1f} km  ({TRIPS_PER_WEEK} trips)")
    print(f"  Operational time  : {total_min:.0f} min  ({total_min/60:.1f} hrs combined)")
    print(f"  Waste collected   : {total_col:,.1f} kg")
    if total_def > 0:
        print(f"  ⚠  Waste deferred : {total_def:,.1f} kg")
    print(f"  Facility slot util:  {bu['total_in_use']:>3}/{FACILITY_BIN_CAPACITY}  "
          f"= {bu['facility_slot_util']*100:.1f}%")
    print(f"  Owned-bin util    :  {bu['total_in_use']:>3}/{BINS_OWNED}  "
          f"= {bu['owned_bin_util']*100:.1f}%  {bu['icon']} {bu['level']}")
    print(f"  Available consumed:  {bins_filled:>3}/{BINS_AVAILABLE}  "
          f"= {bu['avail_consumed']*100:.1f}%")
    print(f"  Owned kg fill     :  {bu['fill_kg']:,}/{OWNED_BIN_CAPACITY_KG:,} kg  "
          f"= {bu['owned_bin_util']*100:.1f}%  {bu['icon']}")
    print(SEP)

# ============================================================
# SECTION 10: INTERACTIVE MAP
# ============================================================

def generate_map(profile, schedule, bins_filled, final_fill):
    """
    Draws real road-following routes by calling OSRM for each trip's
    full waypoint sequence (Facility → Store1 → Store2 → Facility).
    Falls back to straight PolyLine if OSRM is unreachable.
    """
    ROUTE_COLORS = ["#e74c3c", "#3498db"]
    fac_c = FACILITY["coords"]
    bu    = bin_utilisation(bins_filled)

    m = folium.Map(location=[43.46, -80.39], zoom_start=11)

    # ── BSF Facility marker ───────────────────────────────────────────
    folium.Marker(
        location=fac_c,
        popup=folium.Popup(
            f"<b>{FACILITY['name']}</b><br>{FACILITY['address']}<br><br>"
            f"<b>Bin Infrastructure</b><br>"
            f"Facility slots: {FACILITY_BIN_CAPACITY} ({FACILITY_BIN_CAPACITY_KG:,} kg potential)<br>"
            f"Bins owned: {BINS_OWNED} ({OWNED_BIN_CAPACITY_KG:,} kg)  |  Weight/bin: {WEIGHT_PER_BIN_KG} kg<br>"
            f"Slots unprocured: {BIN_SLOTS_EMPTY}<br><br>"
            f"<b>Weekly State</b><br>"
            f"In active cycle: {BINS_IN_USE} bins = {FACILITY_CURRENT_FILL_KG:,} kg pre-committed<br>"
            f"Available this week: {BINS_AVAILABLE} bins = {AVAILABLE_BIN_CAPACITY_KG:,} kg<br>"
            f"Filled this week: {bins_filled} bins<br><br>"
            f"<b>Utilisation</b><br>"
            f"Owned-bin util: {bu['owned_bin_util']*100:.1f}%  ({bu['total_in_use']}/{BINS_OWNED})  {bu['icon']} {bu['level']}<br>"
            f"Facility slot util: {bu['facility_slot_util']*100:.1f}%  ({bu['total_in_use']}/{FACILITY_BIN_CAPACITY})<br>"
            f"Available consumed: {bu['avail_consumed']*100:.1f}%  ({bins_filled}/{BINS_AVAILABLE})<br>"
            f"Owned kg fill: {bu['fill_kg']:,} / {OWNED_BIN_CAPACITY_KG:,} kg",
            max_width=340),
        tooltip="BSF Facility — Woolwich Township",
        icon=folium.Icon(color="black", icon="leaf", prefix="fa"),
    ).add_to(m)

    # ── Store markers ─────────────────────────────────────────────────
    for store, data in STORES.items():
        info = profile[store]
        trip_lbl = next((f"Trip {tid+1} — {t['day']}" for tid, t in schedule.items()
                         if store in t["stores"] and not t.get("skipped")), "DEFERRED")
        folium.CircleMarker(
            location=data["coords"], radius=max(6, info["weekly_kg"] / 60),
            popup=folium.Popup(
                f"<b>{store}</b><br>{data['address']}<br>"
                f"Size: {data['size_sqft']:,} sqft<br>"
                f"Weekly waste: {info['weekly_kg']:,.1f} kg<br>"
                f"Loads/visit: {info['loads_per_visit']}  |  Bins: {info['bins_allocated']}<br>"
                f"Collection: {trip_lbl}", max_width=270),
            tooltip=f"{store} — {info['weekly_kg']:,.1f} kg/wk",
            color=data["color"], fill=True, fillOpacity=0.75,
        ).add_to(m)

    # ── Route polylines — real road geometry via OSRM ─────────────────
    active = [t for t in schedule.values() if not t.get("skipped")]
    for i, trip in enumerate(active):
        s0_, s1_ = trip["stores"]
        waypoints = [
            tuple(FACILITY["coords"]),
            tuple(STORES[s0_]["coords"]),
            tuple(STORES[s1_]["coords"]),
            tuple(FACILITY["coords"]),
        ]
        geometry, osrm_km, osrm_min = fetch_osrm_route(waypoints)
        dist_label = (f"{osrm_km:.1f} km (OSRM)" if osrm_km
                      else f"{trip['distance_km']} km (manual fallback)")
        time_label = (f"{osrm_min:.0f} min drive (OSRM)" if osrm_min
                      else f"{trip['drive_min']:.0f} min drive (manual fallback)")

        folium.PolyLine(
            locations=geometry,
            color=ROUTE_COLORS[i % len(ROUTE_COLORS)],
            weight=5, opacity=0.85,
            tooltip=(
                f"Trip {i+1} — {trip['day']}: {trip['order']}<br>"
                f"{dist_label}  |  {time_label}  |  {trip['bins_needed']} bins"
            ),
        ).add_to(m)

        # Direction arrow at midpoint
        mid = geometry[len(geometry) // 2]
        folium.Marker(
            location=mid,
            icon=folium.DivIcon(
                html=f'<div style="font-size:18px;color:{ROUTE_COLORS[i%len(ROUTE_COLORS)]};">➤</div>',
                icon_size=(20, 20), icon_anchor=(10, 10)
            )
        ).add_to(m)

    # ── Legend with 3 utilisation bars ───────────────────────────────
    bin_pct   = min(100.0, bu["owned_bin_util"]     * 100)
    slot_pct  = min(100.0, bu["facility_slot_util"] * 100)
    avail_pct = min(100.0, bu["avail_consumed"]     * 100)

    def _bar_color(pct):
        if pct >= 90: return "#e74c3c"
        if pct >= 75: return "#f39c12"
        if pct >= 50: return "#f1c40f"
        return "#2ecc71"

    def _gauge(label, pct, num, den, sub):
        c   = _bar_color(pct)
        p   = f"{pct:.0f}"
        w   = f"{pct:.1f}"
        return (
            f'<div style="margin:5px 0 1px;font-size:11px;color:#444;">'
            f'{label} &nbsp;<b>({num}/{den} = {p}%)</b></div>'
            f'<div style="background:#e0e0e0;border-radius:4px;height:14px;width:220px;">'
            f'<div style="background:{c};width:{w}%;height:14px;border-radius:4px;'
            f'display:flex;align-items:center;justify-content:flex-end;padding-right:4px;">'
            f'<span style="font-size:10px;color:white;font-weight:bold;">{p}%</span>'
            f'</div></div>'
            f'<div style="font-size:10px;color:#777;margin-bottom:6px;">{sub}</div>'
        )

    trip_rows = "".join(
        f'<span style="color:{ROUTE_COLORS[i % len(ROUTE_COLORS)]};font-size:15px;">━━</span>'
        f'&nbsp;<b>Trip {i + 1}</b> — {t["day"]}<br>'
        for i, t in enumerate(active)
    )

    legend_html = (
        '<div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;'
        'padding:14px 16px;border:2px solid #aaa;border-radius:8px;'
        'font-size:12px;line-height:1.8;min-width:265px;'
        'box-shadow:2px 2px 6px rgba(0,0,0,0.15);">'
        '<div style="font-weight:bold;font-size:13px;margin-bottom:4px;">🗓 Trip Schedule</div>'
        + trip_rows
        + '<hr style="margin:8px 0;border-color:#ddd;">'
        + '<div style="font-weight:bold;font-size:13px;margin-bottom:2px;">🌿 Bin Utilisation</div>'
        + _gauge("Owned-bin util",         bin_pct,
                 bu["total_in_use"], BINS_OWNED,
                 f"{bu['fill_kg']:,} / {OWNED_BIN_CAPACITY_KG:,} kg &nbsp;|&nbsp; {bu['icon']} {bu['level']}")
        + _gauge("Facility slot util",     slot_pct,
                 bu["total_in_use"], FACILITY_BIN_CAPACITY,
                 f"{BIN_SLOTS_EMPTY} slots unprocured &nbsp;|&nbsp; {BINS_OWNED}/{FACILITY_BIN_CAPACITY} slots filled with bins")
        + _gauge("Available bins consumed", avail_pct,
                 bins_filled, BINS_AVAILABLE,
                 f"{bins_filled} filled this week &nbsp;|&nbsp; {BINS_IN_USE} in active larval cycle")
        + '<hr style="margin:8px 0;border-color:#ddd;">'
        + '<div style="font-size:10px;color:#888;">🛣 Routes: OSRM real road geometry<br>'
        + 'Fallback: straight line if OSRM unreachable</div>'
        + '</div>'
    )

    m.get_root().html.add_child(folium.Element(legend_html))
    m.save("waste_collection_routes.html")
    print("  Interactive map saved → waste_collection_routes.html")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    # ── Optionally refresh distance matrix from OSRM ─────────────────
    if USE_OSRM_DISTANCES:
        try:
            osrm_matrix = build_osrm_distance_matrix()
            ROAD_DIST_KM.update(osrm_matrix)
            print("  ✅ ROAD_DIST_KM updated from OSRM")
        except Exception as e:
            print(f"  ⚠ OSRM distance matrix failed, using hardcoded values: {e}")

    profile                                    = compute_waste_profile()
    pairing                                    = find_optimal_pairing()
    schedule, flags, bins_filled, final_fill   = build_schedule(pairing, profile)
    print_report(profile, schedule, flags, bins_filled, final_fill, pairing)
    generate_map(profile, schedule, bins_filled, final_fill)
