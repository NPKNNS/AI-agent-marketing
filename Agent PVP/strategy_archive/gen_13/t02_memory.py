"""What the agent remembers between rounds: saved as JSON so restarts do not forget.

Stage 3 Upgrades (from Stage 2 56-Team Post-Mortem):
1. Market-Summary Clearing Markup Calibration (even from Practice Rounds P1-P2!):
   - Infers each buyer's real clearing markup `mkt_clearing_mk` and crowding ratio (`offers / wanted_bags`)
     from `market_summary` (`price_min`, `price_max`, `price_avg`, `wanted_kg`, `bought_kg`, `offers`).
   - Immediately caps `safe_mk` when a buyer is 100% filled and crowded (e.g. B1 Specialty in a 56-team field)
     so we never bid 1.10x into a 0.98x-1.00x market.
2. Practice-Round (P1-P2) Full Learning:
   - Learns SPEC_MISMATCH, verified specs, ABOVE_LIMIT ceilings, and market_summary clearing prices from P1-P2.
3. Fast 1-Step Per-Lot `tv_factor` Calibration:
   - Immediately drops `tv_factor` by -0.050 when a lot hits ABOVE_LIMIT at a normal markup (`<= est_ceiling`),
     compensating in 1 step when sample noise shifts true grade down a bracket.
"""
import json
from pathlib import Path

DEFAULT_PRIORS = {
    "SPECIALTY": 1.085,
    "COMMERCIAL": 1.030,
    "INSTANT": 0.935,
}

BUYER_PRIORS = {
    "2 บางกอกบลูม": 1.155,
    "1 ดอยช้างโรสเตอร์": 1.110,
    "4 ชุมพรคั่วบด": 1.055,
    "3 สยามคอมเมิร์ซ": 1.040,
    "6 ทรีอินวันฟู้ดส์": 0.955,
    "5 อินสแตนท์ไทย": 0.935,
}

STD_SPECS = {
    "SPECIALTY": {"max_defect_pct": 3.0, "max_moisture_pct": 12.5, "min_screen": 16},
    "COMMERCIAL": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None},
    "INSTANT": {"max_defect_pct": 15.0, "max_moisture_pct": 15.0, "min_screen": None},
}


def _raw_passes(sample: dict, spec: dict) -> bool:
    if not sample or not spec:
        return True
    return (
        sample.get("defect_pct", 0.0) <= spec.get("max_defect_pct", 100.0)
        and sample.get("moisture_pct", 0.0) <= spec.get("max_moisture_pct", 100.0)
        and (spec.get("min_screen") is None or sample.get("screen", 18) >= spec["min_screen"])
    )


class Memory:
    def __init__(self, path: str):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.sent: dict = data.get("sent", {})                 # round_id -> offers we sent
        self.rounds: list = data.get("rounds", [])             # results of cleared rounds, oldest first
        self.buyers: dict = data.get("buyers", {})             # buyer_name -> adaptive ceiling & market stats
        self.rfq_meta: dict = data.get("rfq_meta", {})         # rfq_id -> {buyer, buyer_id, buyer_type, spec}
        self.lots: dict = data.get("lots", {})                 # lot_id -> sample vs true inferred bounds & tv_factor
        self.lot_tvs: dict = data.get("lot_tvs", {})           # lot_id -> initial sample TV

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sent": self.sent,
            "rounds": self.rounds,
            "buyers": self.buyers,
            "rfq_meta": self.rfq_meta,
            "lots": self.lots,
            "lot_tvs": self.lot_tvs,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def _ensure_lot(self, lot_id: str, sample: dict | None = None, tv_val: float | None = None) -> dict:
        if lot_id not in self.lots:
            self.lots[lot_id] = {
                "lot_id": lot_id,
                "sample": sample or {},
                "tv_factor": 1.000,
                "min_true_defect": None,
                "max_true_defect": None,
                "min_true_moisture": None,
                "max_true_moisture": None,
                "max_true_screen": None,
                "min_true_screen": None,
                "failed_specs": [],
                "passed_specs": [],
                "mismatch_count": 0,
                "accepted_count": 0,
                "outbid_count": 0,
                "above_count": 0,
            }
        elif sample and not self.lots[lot_id].get("sample"):
            self.lots[lot_id]["sample"] = sample
        if tv_val is not None:
            self.lot_tvs[lot_id] = float(tv_val)
        return self.lots[lot_id]

    def observe_lots(self, lots: list[dict], tv: dict | None = None) -> None:
        for l in lots:
            lid = l["lot_id"]
            tval = tv.get(lid) if tv else self.lot_tvs.get(lid)
            self._ensure_lot(lid, l.get("sample"), tval)

    def _ensure_buyer(self, buyer: str, buyer_type: str, buyer_id: str | None = None) -> dict:
        if buyer not in self.buyers:
            prior = BUYER_PRIORS.get(buyer, DEFAULT_PRIORS.get(buyer_type, 1.00))
            self.buyers[buyer] = {
                "buyer": buyer,
                "buyer_id": buyer_id or "",
                "buyer_type": buyer_type,
                "est_ceiling": prior,
                "safe_mk": prior,
                "mkt_clearing_mk": None,      # inferred from market_summary clearing prices
                "crowding": 1.0,              # offers_count / wanted_bags in recent rounds
                "avg_fill_pct": 100.0,
                "lower_bound": None,          # highest markup that actually sold (ACCEPTED/PARTIAL)
                "outbid_cap": None,           # lowest markup that lost to competition (OUTBID)
                "upper_bound": None,          # lowest markup that failed buyer ceiling (ABOVE_LIMIT)
                "last_accepted": None,
                "last_outbid": None,
                "last_above": None,
                "consecutive_outbids": 0,
                "outbid_count": 0,
                "above_count": 0,
                "accepted_count": 0,
                "mkt_history": [],
            }
        else:
            b = self.buyers[buyer]
            if buyer_id and not b.get("buyer_id"):
                b["buyer_id"] = buyer_id
            b.setdefault("outbid_cap", None)
            b.setdefault("mkt_clearing_mk", None)
            b.setdefault("crowding", 1.0)
            b.setdefault("avg_fill_pct", 100.0)
            b.setdefault("consecutive_outbids", 0)
            b.setdefault("safe_mk", b.get("est_ceiling", DEFAULT_PRIORS.get(buyer_type, 1.00)))
        return self.buyers[buyer]

    def _eligible_tv_bounds_for_spec(self, spec: dict, buyer_type: str) -> tuple[float | None, float | None]:
        """Returns (min_eff_tv, max_eff_tv) among our lots that pass `spec`."""
        eff_tvs = []
        check_spec = spec or STD_SPECS.get(buyer_type, {})
        for lid, lrec in self.lots.items():
            base_tv = self.lot_tvs.get(lid)
            if not base_tv:
                continue
            if not self.is_spec_failed(lid, check_spec) and _raw_passes(lrec.get("sample", {}), check_spec):
                eff_tvs.append(base_tv * float(lrec.get("tv_factor", 1.0)))
        if not eff_tvs:
            return None, None
        return min(eff_tvs), max(eff_tvs)

    def _recompute_buyer_targets(self, b: dict, last_status: str | None = None, last_mk: float | None = None) -> None:
        safe = float(b.get("safe_mk") or b.get("est_ceiling") or 1.00)
        ub = b.get("upper_bound")
        oc = b.get("outbid_cap")
        lb = b.get("lower_bound")
        mkt_mk = b.get("mkt_clearing_mk")
        crowding = float(b.get("crowding", 1.0))
        fill_pct = float(b.get("avg_fill_pct", 100.0))

        if last_status == "ABOVE_LIMIT" and last_mk is not None:
            safe = min(safe - 0.012, (ub or last_mk) - 0.006)
        elif last_status == "OUTBID" and last_mk is not None:
            base_step = 0.008 if b.get("buyer_type") == "SPECIALTY" else 0.013
            step_down = base_step + 0.005 * min(2, b.get("consecutive_outbids", 1) - 1)
            safe = min(safe - step_down, last_mk - step_down)
        elif last_status == "PARTIAL" and last_mk is not None:
            safe = min(safe, last_mk - 0.003)
        elif last_status == "ACCEPTED" and last_mk is not None:
            step_up = 0.018 if fill_pct < 96.0 else 0.012
            target = max(safe, last_mk + step_up)
            if oc is not None and oc > last_mk:
                target = min(target, (last_mk + oc) / 2.0 + 0.005)
            safe = target

        # Incorporate marginal clearing markup without letting StayLowBot price_avg pull SPECIALTY down
        if mkt_mk is not None:
            if fill_pct >= 98.0 and crowding >= 1.2:
                if b.get("buyer_type") == "SPECIALTY":
                    # Blend gently upward/downward; never crush SPECIALTY below lb or prior floor just from R1-R5 crowding
                    safe = 0.65 * safe + 0.35 * max(mkt_mk + 0.015, (lb or safe))
                else:
                    margin = 0.014 if crowding < 2.0 else 0.004
                    safe = min(safe, max(mkt_mk + margin, (lb or 0.0)))
            elif fill_pct < 90.0 and last_status not in ("ABOVE_LIMIT", "OUTBID"):
                safe = max(safe, min(b.get("est_ceiling", safe) + 0.010, max(mkt_mk + 0.025, safe + 0.015)))

        # Gen 4: Release stale outbid_cap when buyer is undersupplied or uncrowded
        if oc is not None and (fill_pct < 96.0 or crowding < 1.35) and last_status != "OUTBID":
            oc = round(min(1.25, oc + 0.028), 4)
            b["outbid_cap"] = oc

        if oc is not None:
            safe = min(safe, oc - 0.005)
        if ub is not None:
            safe = min(safe, ub - 0.005)
        if lb is not None and ub is not None and ub <= lb:
            b["lower_bound"] = round(ub - 0.012, 3)

        b["safe_mk"] = round(max(0.83, min(1.30, safe)), 3)
        b["est_ceiling"] = round(min(1.30, (ub - 0.004) if ub is not None else max(b["safe_mk"] + 0.012, b.get("est_ceiling", b["safe_mk"]))), 3)

    def observe_rfqs(self, rfqs: list[dict]) -> None:
        changed = False
        for r in rfqs:
            rid = r["rfq_id"]
            meta = {
                "buyer": r["buyer"],
                "buyer_id": r.get("buyer_id", ""),
                "buyer_type": r["buyer_type"],
                "spec": r.get("spec", {}),
            }
            if self.rfq_meta.get(rid) != meta:
                self.rfq_meta[rid] = meta
                changed = True
            self._ensure_buyer(r["buyer"], r["buyer_type"], r.get("buyer_id"))
        if changed:
            self.save()

    def record_sent(self, round_id: str, offers: list[dict], state: dict, tv: dict) -> None:
        self.observe_lots(state.get("lots", []), tv)
        self.observe_rfqs(state.get("rfqs", []))
        rfq_map = {r["rfq_id"]: r for r in state.get("rfqs", [])}
        lot_map = {l["lot_id"]: l for l in state.get("lots", [])}
        self.sent[round_id] = [
            {
                **o,
                "buyer": rfq_map[o["rfq_id"]]["buyer"],
                "buyer_id": rfq_map[o["rfq_id"]].get("buyer_id", ""),
                "buyer_type": rfq_map[o["rfq_id"]]["buyer_type"],
                "spec": rfq_map[o["rfq_id"]].get("spec", {}),
                "sample": lot_map.get(o["lot_id"], {}).get("sample", {}),
                "markup": round(o["price_per_kg"] / max(1.0, tv[o["lot_id"]] * self.get_lot_tv_factor(o["lot_id"])), 4),
                "raw_markup": round(o["price_per_kg"] / tv[o["lot_id"]], 4),
            }
            for o in offers
            if o["rfq_id"] in rfq_map and o["lot_id"] in tv
        ]
        self.save()

    def _record_lot_spec_outcome(self, lot_id: str, sample: dict, spec: dict, status: str, mk: float, b: dict) -> None:
        lrec = self._ensure_lot(lot_id, sample)
        if not spec:
            return
        max_d = spec.get("max_defect_pct")
        max_m = spec.get("max_moisture_pct")
        min_s = spec.get("min_screen")

        if status == "SPEC_MISMATCH":
            lrec["mismatch_count"] += 1
            if spec not in lrec["failed_specs"]:
                lrec["failed_specs"].append(spec)

            s_d = sample.get("defect_pct", 0.0)
            s_m = sample.get("moisture_pct", 0.0)
            s_s = sample.get("screen", 18)

            d_near = max_d is not None and (max_d - s_d) <= 0.90
            m_near = max_m is not None and (max_m - s_m) <= 0.85
            s_near = min_s is not None and (s_s - min_s) <= 1

            if d_near and not m_near and not s_near:
                lrec["min_true_defect"] = max_d if lrec["min_true_defect"] is None else max(lrec["min_true_defect"], max_d)
                lrec["tv_factor"] = max(0.88, round(lrec["tv_factor"] - 0.045, 4))
            elif m_near and not d_near and not s_near:
                lrec["min_true_moisture"] = max_m if lrec["min_true_moisture"] is None else max(lrec["min_true_moisture"], max_m)
                lrec["tv_factor"] = max(0.88, round(lrec["tv_factor"] - 0.030, 4))
            elif s_near and not d_near and not m_near:
                lrec["max_true_screen"] = min_s if lrec["max_true_screen"] is None else min(lrec["max_true_screen"], min_s)
                lrec["tv_factor"] = max(0.90, round(lrec["tv_factor"] - 0.025, 4))
            else:
                lrec["tv_factor"] = max(0.88, round(lrec["tv_factor"] - 0.035, 4))
            return

        if status in ("ACCEPTED", "PARTIAL", "OUTBID", "ABOVE_LIMIT"):
            if spec not in lrec["passed_specs"]:
                lrec["passed_specs"].append(spec)
            if max_d is not None:
                lrec["max_true_defect"] = max_d if lrec["max_true_defect"] is None else min(lrec["max_true_defect"], max_d)
            if max_m is not None:
                lrec["max_true_moisture"] = max_m if lrec["max_true_moisture"] is None else min(lrec["max_true_moisture"], max_m)
            if min_s is not None:
                lrec["min_true_screen"] = min_s if lrec["min_true_screen"] is None else max(lrec["min_true_screen"], min_s)

            if status in ("ACCEPTED", "PARTIAL"):
                lrec["accepted_count"] += 1
                lrec["consec_outbid"] = 0
                if mk >= b.get("est_ceiling", 1.0):
                    lrec["tv_factor"] = min(1.03, round(lrec["tv_factor"] + 0.004, 4))
            elif status == "ABOVE_LIMIT":
                lrec["above_count"] += 1
                lrec["consec_outbid"] = 0
                # Fast 1-step calibration (-4.5%) whenever a lot gets ABOVE_LIMIT at a plausible markup (fixes sample overestimate in R7-R8)
                ceil_ref = max(b.get("est_ceiling", 1.05) + 0.015, 1.145 if b.get("buyer_type") == "SPECIALTY" else 1.065)
                if mk <= ceil_ref:
                    lrec["tv_factor"] = max(0.84, round(lrec["tv_factor"] - 0.045, 4))
            elif status == "OUTBID":
                lrec["outbid_count"] += 1
                lrec["consec_outbid"] = lrec.get("consec_outbid", 0) + 1
                if mk <= 0.88 or (lrec["consec_outbid"] >= 2 and mk <= 0.93):
                    lrec["tv_factor"] = max(0.82, round(lrec["tv_factor"] - 0.035, 4))
                elif lrec["outbid_count"] >= 2 and lrec["accepted_count"] == 0:
                    lrec["tv_factor"] = max(0.86, round(lrec["tv_factor"] - 0.020, 4))

    def is_spec_failed(self, lot_id: str, spec: dict) -> bool:
        """Returns True if previous sales proved this lot's TRUE quality fails `spec`."""
        lrec = self.lots.get(lot_id)
        if not lrec or not spec:
            return False
        max_d = spec.get("max_defect_pct", 100.0)
        max_m = spec.get("max_moisture_pct", 100.0)
        min_s = spec.get("min_screen")

        if lrec.get("min_true_defect") is not None and max_d <= lrec["min_true_defect"]:
            return True
        if lrec.get("min_true_moisture") is not None and max_m <= lrec["min_true_moisture"]:
            return True
        if min_s is not None and lrec.get("max_true_screen") is not None and min_s >= lrec["max_true_screen"]:
            return True

        if self.is_spec_verified(lot_id, spec):
            return False

        for fs in lrec.get("failed_specs", []):
            fd = fs.get("max_defect_pct", 100.0)
            fm = fs.get("max_moisture_pct", 100.0)
            fs_scr = fs.get("min_screen")
            scr_stricter_or_eq = (fs_scr is None) or (min_s is not None and min_s >= fs_scr)
            if max_d <= fd and max_m <= fm and scr_stricter_or_eq:
                return True
        return False

    def is_spec_verified(self, lot_id: str, spec: dict) -> bool:
        """Returns True if previous sales proved this lot's TRUE quality passes `spec`."""
        lrec = self.lots.get(lot_id)
        if not lrec or not spec:
            return False
        max_d = spec.get("max_defect_pct", 100.0)
        max_m = spec.get("max_moisture_pct", 100.0)
        min_s = spec.get("min_screen")

        for ps in lrec.get("passed_specs", []):
            pd = ps.get("max_defect_pct", 100.0)
            pm = ps.get("max_moisture_pct", 100.0)
            ps_scr = ps.get("min_screen")
            scr_ok = (min_s is None) or (ps_scr is not None and ps_scr >= min_s)
            if pd <= max_d and pm <= max_m and scr_ok:
                return True
        return False

    def get_lot_tv_factor(self, lot_id: str) -> float:
        lrec = self.lots.get(lot_id)
        return float(lrec.get("tv_factor", 1.000)) if lrec else 1.000

    def record_results(self, results: dict | None) -> None:
        if not results or not results.get("round_id"):
            return
        rid = results["round_id"]
        if any(r["round_id"] == rid for r in self.rounds):
            return

        self.rounds.append(results)
        sent_list = self.sent.get(rid, [])
        sent_by_key = {(o["rfq_id"], o["lot_id"]): o for o in sent_list}
        summary_by_rfq = {row["rfq_id"]: row for row in results.get("market_summary", [])}

        # 1. Extract market-wide clearing markups, fill rates, and crowding from market_summary
        buyer_round_mks: dict[str, list[float]] = {}
        buyer_round_fills: dict[str, list[float]] = {}
        buyer_round_crowd: dict[str, list[float]] = {}

        for row in results.get("market_summary", []):
            b = self._ensure_buyer(row["buyer"], row["buyer_type"])
            w_bags = max(1, row["wanted_kg"] // 60)
            b_bags = row["bought_kg"] // 60
            fill_pct = round(row["bought_kg"] * 100 / max(1, row["wanted_kg"]), 1)
            crowd = round(row["offers"] / w_bags, 2)

            meta = self.rfq_meta.get(row["rfq_id"], {})
            spec = meta.get("spec") or STD_SPECS.get(row["buyer_type"], {})
            min_tv, max_tv = self._eligible_tv_bounds_for_spec(spec, row["buyer_type"])

            inferred_mk = None
            if row["price_max"] is not None and row["price_min"] is not None and min_tv and max_tv:
                if abs(max_tv - min_tv) < 4.0:
                    inferred_mk = round((0.78 * row["price_max"] + 0.22 * (row["price_avg"] or row["price_max"])) / max_tv, 4)
                else:
                    # Focus heavily on marginal clearing price_max so StayLowBot price_min/avg does not bias targets downward
                    avg_tv = 0.65 * max_tv + 0.35 * min_tv
                    mk_hi = row["price_max"] / max_tv
                    mk_mid = (row["price_avg"] or row["price_max"]) / avg_tv
                    inferred_mk = round(0.75 * mk_hi + 0.25 * mk_mid, 4)

            b["mkt_history"].append({
                "round_id": rid,
                "wanted_bags": w_bags,
                "bought_bags": b_bags,
                "fill_pct": fill_pct,
                "offers": row["offers"],
                "crowding": crowd,
                "price_min": row["price_min"],
                "price_max": row["price_max"],
                "price_avg": row["price_avg"],
                "inferred_mk": inferred_mk,
            })
            if row["bought_kg"] > 0 or row["offers"] > 0:
                buyer_round_fills.setdefault(row["buyer"], []).append(fill_pct)
                buyer_round_crowd.setdefault(row["buyer"], []).append(crowd)
            if inferred_mk is not None and row["bought_kg"] > 0:
                buyer_round_mks.setdefault(row["buyer"], []).append(inferred_mk)

        for bname, b in self.buyers.items():
            if bname in buyer_round_fills:
                b["avg_fill_pct"] = round(sum(buyer_round_fills[bname]) / len(buyer_round_fills[bname]), 1)
            if bname in buyer_round_crowd:
                b["crowding"] = round(sum(buyer_round_crowd[bname]) / len(buyer_round_crowd[bname]), 2)
            if bname in buyer_round_mks:
                new_mkt_mk = sum(buyer_round_mks[bname]) / len(buyer_round_mks[bname])
                old_mkt_mk = b.get("mkt_clearing_mk")
                b["mkt_clearing_mk"] = round(new_mkt_mk if old_mkt_mk is None else (0.65 * new_mkt_mk + 0.35 * old_mkt_mk), 3)
                if b.get("outbid_cap") is not None and b["mkt_clearing_mk"] > b["outbid_cap"]:
                    b["outbid_cap"] = round(0.60 * b["mkt_clearing_mk"] + 0.40 * b["outbid_cap"], 4)
                self._recompute_buyer_targets(b)

        # 2. Process our own offer outcomes, aggregating per buyer so multiple offers in the same round never double-step
        buyer_outcomes: dict[str, dict] = {}
        for o in results.get("my_offers", []):
            key = (o["rfq_id"], o["lot_id"])
            s = sent_by_key.get(key, {})
            meta = summary_by_rfq.get(o["rfq_id"]) or self.rfq_meta.get(o["rfq_id"], {})
            buyer = s.get("buyer") or meta.get("buyer")
            btype = s.get("buyer_type") or meta.get("buyer_type")
            bid = s.get("buyer_id") or meta.get("buyer_id", "")
            spec = s.get("spec") or meta.get("spec", {})
            sample = s.get("sample") or self.lots.get(o["lot_id"], {}).get("sample", {})
            mk = s.get("markup")
            if not buyer or not btype or mk is None:
                continue

            b = self._ensure_buyer(buyer, btype, bid)
            st = o["status"]
            self._record_lot_spec_outcome(o["lot_id"], sample, spec, st, mk, b)

            rec = buyer_outcomes.setdefault(buyer, {"b": b, "accepted": [], "partial": [], "outbid": [], "above": []})
            if st == "ACCEPTED":
                rec["accepted"].append(mk)
            elif st == "PARTIAL":
                rec["partial"].append(mk)
            elif st == "OUTBID":
                rec["outbid"].append(mk)
            elif st == "ABOVE_LIMIT":
                rec["above"].append(mk)

        for buyer, rec in buyer_outcomes.items():
            b = rec["b"]
            if rec["accepted"] or rec["partial"]:
                best_acc = max(rec["accepted"] + rec["partial"])
                b["accepted_count"] += len(rec["accepted"]) + len(rec["partial"])
                b["last_accepted"] = best_acc
                b["lower_bound"] = best_acc if b["lower_bound"] is None else max(b["lower_bound"], best_acc)

            if rec["above"]:
                min_abv = min(rec["above"])
                b["above_count"] += len(rec["above"])
                b["last_above"] = min_abv
                b["upper_bound"] = min_abv if b["upper_bound"] is None else min(b["upper_bound"], min_abv)
                if b.get("outbid_cap") is not None and b["outbid_cap"] > b["upper_bound"]:
                    b["outbid_cap"] = b["upper_bound"]
                self._recompute_buyer_targets(b, "ABOVE_LIMIT", min_abv)
            elif rec["outbid"] and not (rec["accepted"] or rec["partial"]):
                min_out = min(rec["outbid"])
                b["outbid_count"] += len(rec["outbid"])
                b["consecutive_outbids"] = b.get("consecutive_outbids", 0) + 1
                b["last_outbid"] = min_out
                b["outbid_cap"] = min_out if b.get("outbid_cap") is None else min(b["outbid_cap"], min_out)
                self._recompute_buyer_targets(b, "OUTBID", min_out)
            elif rec["outbid"] and (rec["accepted"] or rec["partial"]):
                # Both accepted and outbid on the same buyer in the same round -> we bracketed the exact clearing price!
                best_acc = max(rec["accepted"] + rec["partial"])
                min_out = min(rec["outbid"])
                b["consecutive_outbids"] = 0
                b["last_outbid"] = min_out
                b["outbid_cap"] = max(best_acc + 0.006, min_out)
                b["safe_mk"] = round(max(0.85, min(1.30, (best_acc + min_out) / 2.0)), 3)
                self._recompute_buyer_targets(b, "PARTIAL", b["safe_mk"])
            elif rec["accepted"] or rec["partial"]:
                best_acc = max(rec["accepted"] + rec["partial"])
                b["consecutive_outbids"] = 0
                st_label = "ACCEPTED" if rec["accepted"] else "PARTIAL"
                if st_label == "ACCEPTED" and b.get("outbid_cap") is not None and b["outbid_cap"] <= best_acc + 0.015:
                    b["outbid_cap"] = round(best_acc + 0.028, 4)
                self._recompute_buyer_targets(b, st_label, best_acc)

        # Gen 6: When R5 clears (entering R6-R8 Coffee Festival), reset SPECIALTY safe_mk & outbid_cap to festival priors
        if rid in ("P2", "R5"):
            for b in self.buyers.values():
                if b.get("buyer_type") == "SPECIALTY":
                    prior = BUYER_PRIORS.get(b["buyer"], 1.095)
                    if b.get("outbid_cap") is not None:
                        b["outbid_cap"] = round(max(b["outbid_cap"], prior + 0.010), 4)
                    b["safe_mk"] = round(max(b.get("safe_mk", prior), prior - 0.010), 3)
                    b["consecutive_outbids"] = 0
                    self._recompute_buyer_targets(b)

        self.save()

    def get_buyer_estimate(self, buyer: str, buyer_type: str) -> dict:
        return self._ensure_buyer(buyer, buyer_type)

    def buyer_intelligence_summary(self, active_buyers: list[str] | None = None) -> str:
        """Compact per-buyer ceiling, market clearing mk, and crowding summary for the LLM."""
        if not self.buyers:
            return "No prior rounds yet."
        lines = []
        items = sorted(self.buyers.items(), key=lambda kv: -kv[1].get("safe_mk", kv[1]["est_ceiling"]))
        for name, b in items:
            if active_buyers is not None and name not in active_buyers:
                continue
            mkt_s = f"{b['mkt_clearing_mk']:.3f}" if b.get("mkt_clearing_mk") is not None else "?"
            ub = f"{b['upper_bound']:.3f}" if b["upper_bound"] is not None else "?"
            bid_tag = f"{b['buyer_id']} " if b.get("buyer_id") else ""
            lines.append(
                f"- {bid_tag}{name}[{b['buyer_type']}]: safe_mk={b.get('safe_mk', b['est_ceiling']):.3f} "
                f"(mkt={mkt_s}, rej>={ub}, crowd={b.get('crowding', 1.0):.1f}x, fill={b.get('avg_fill_pct', 100):.0f}%)"
            )
        return "\n".join(lines) or "none"

    def summary(self, last: int = 3) -> str:
        """Compact recent round outcomes (last 3 rounds to save tokens)."""
        lines = []
        for rnd in self.rounds[-last:]:
            sent = {(o["rfq_id"], o["lot_id"]): o for o in self.sent.get(rnd["round_id"], [])}
            mine = []
            for o in rnd.get("my_offers", []):
                s = sent.get((o["rfq_id"], o["lot_id"]), {})
                btag = s.get("buyer_id") or s.get("buyer_type", "?")
                mine.append(
                    f"{btag}:{o['lot_id']}@{s.get('markup', '?')}->{o['status']}({o['filled_kg']//60}/{o['kg']//60}b)"
                )
            lines.append(f"{rnd['round_id']}: {'; '.join(mine) or 'none'}")
        return "\n".join(lines) or "none yet"
