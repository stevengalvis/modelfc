"""Pure scoring of validated frozen shadow inputs, shared with research snapshots."""

import math


def forecast_report(rows):
    errors = {"production": [], "shadow": []}
    brier = {"production": [], "shadow": []}
    counts = {"production_predictions": 0, "shadow_predictions": 0, "missing_shadow_predictions": 0,
              "settled_fixtures": 0, "team_forecasts": 0, "market_line_targets": 0,
              "push_targets_excluded": 0, "later_targets_excluded": 0}
    for row in rows:
        production, shadow, outcome = row["prediction"], row["shadow"], row["outcome"]
        counts["production_predictions"] += 1
        if shadow is None:
            counts["missing_shadow_predictions"] += 1
            continue
        counts["shadow_predictions"] += 1
        if outcome is None:
            continue
        counts["settled_fixtures"] += 1
        for side in row.get("sides", ("home", "away")):
            actual = outcome["result"][side + "_corners"]
            for name, record in (("production", production), ("shadow", shadow)):
                errors[name].append(abs(actual - record["distribution"][side + "_expected_corners"]))
        targets = {target["target_id"]: target for target in row["targets"]}
        frozen_ids = {target["production_target_id"] for target in shadow["targets"]}
        counts["later_targets_excluded"] += sum(t["status"] == "SUPPORTED" and t["market_type"] == "TEAM_TOTAL"
            and t["target_id"] not in frozen_ids for t in row["targets"])
        for frozen in shadow["targets"]:
            target = targets[frozen["production_target_id"]]
            if target["team_side"].lower() not in row.get("sides", ("home", "away")):
                continue
            actual = outcome["result"][target["team_side"].lower() + "_corners"]
            if actual == target["line"]:
                counts["push_targets_excluded"] += 1
                continue
            win = int(actual > target["line"] if target["direction"] == "OVER" else actual < target["line"])
            brier["production"].append((target["decisive_model_probability"] - win) ** 2)
            brier["shadow"].append((frozen["decisive_model_probability"] - win) ** 2)
    counts["team_forecasts"] = len(errors["production"])
    counts["market_line_targets"] = len(brier["production"])
    return {**counts, **{name: {
        "team_mae": math.fsum(errors[name]) / len(errors[name]) if errors[name] else None,
        "mean_brier": math.fsum(brier[name]) / len(brier[name]) if brier[name] else None,
    } for name in ("production", "shadow")}}


def decision_report(rows):
    from modelfc.corner_prospective_read import _profit, _target_settlement
    counts = {"paired_settled_fixtures": 0, "paired_decision_snapshots": 0,
              "observation_snapshots": 0, "later_only_targets_excluded": 0,
              "missing_assessments": 0, "pushes": 0,
              "neither_qualifies": 0, "champion_only": 0, "shadow_only": 0, "both_qualify": 0}
    scores = {name: {"hypothetical_qualifying_events": 0, "wins": 0, "losses": 0,
                     "pushes": 0, "settled_hypothetical_qualifying_events": 0,
                     "standardized_realized_units": 0.0,
                     "roi_on_settled_hypothetical_events": None,
                     "unique_target_bookmaker_opportunities": 0}
              for name in ("champion", "shadow")}
    for row in rows:
        outcome = row["outcome"]
        if row["shadow"] is not None and outcome is not None:
            counts["paired_settled_fixtures"] += 1
        unique = {"champion": set(), "shadow": set()}
        for observation in row["observations"]:
            counts["observation_snapshots"] += 1
            actual = observation["assessment"]
            if actual is None:
                counts["missing_assessments"] += 1
                continue
            counts["later_only_targets_excluded"] += actual["later_only_targets_excluded"]
            for decision in actual["decisions"]:
                if decision["team_side"].lower() not in row.get("sides", ("home", "away")):
                    continue
                counts["paired_decision_snapshots"] += 1
                a, b = decision["champion"]["qualified"], decision["shadow"]["qualified"]
                key = ("both_qualify" if a and b else "champion_only" if a else
                       "shadow_only" if b else "neither_qualifies")
                counts[key] += 1
                for name, qualified in (("champion", a), ("shadow", b)):
                    if qualified:
                        scores[name]["hypothetical_qualifying_events"] += 1
                        unique[name].add((decision["target_id"], decision["bookmaker"]))
                if outcome is None:
                    continue
                result, _ = _target_settlement({"status": "SUPPORTED", "market_type": "TEAM_TOTAL",
                                                "team_side": decision["team_side"],
                                                "direction": decision["direction"],
                                                "line": decision["line"]}, outcome)
                counts["pushes"] += int(result == "PUSH")
                for name, qualified in (("champion", a), ("shadow", b)):
                    if qualified:
                        item = scores[name]
                        item["settled_hypothetical_qualifying_events"] += 1
                        item[{"WIN": "wins", "LOSS": "losses", "PUSH": "pushes"}[result]] += 1
                        item["standardized_realized_units"] += _profit(result, decision["american_odds"])
        for name in scores:
            scores[name]["unique_target_bookmaker_opportunities"] += len(unique[name])
    for item in scores.values():
        n = item["settled_hypothetical_qualifying_events"]
        item["roi_on_settled_hypothetical_events"] = item["standardized_realized_units"] / n if n else None
    return {**counts, **scores}
