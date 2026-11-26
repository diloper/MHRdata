"""User-provided v16.0.0 recoil/reload skill-level lookup.

The ladders are copied from the user's table, not derived from Kiranico.  Labels
that do not exactly match a tier (for example Kiranico's weapon-level ``Some`` or
``Above Avg.``) are reported as ``unmapped`` instead of being guessed.
"""

RULE_SOURCE = "user_provided_v16_table"
GAME_VERSION = "16.0.0"

RULES = {
    "recoil": {
        "skill": "Recoil Down",
        "target_tier": "Very Low",
        "ladder": ["Very High", "High", "Average", "Low", "Very Low"],
        "required_level": {"Very High": None, "High": 3, "Average": 2, "Low": 1, "Very Low": 0},
        "best_tier_at_lv3": {"Very High": "Average", "High": "Very Low", "Average": "Very Low",
                             "Low": "Very Low", "Very Low": "Very Low"},
        "aliases": {},
    },
    "reload": {
        "skill": "Reload Speed",
        "target_tier": "Fast/Fastest",
        "ladder": ["Very Slow", "Slow", "Below Average", "Normal", "Fast/Fastest"],
        "required_level": {"Very Slow": None, "Slow": 3, "Below Average": 2, "Normal": 1,
                           "Fast/Fastest": 0},
        "best_tier_at_lv3": {"Very Slow": "Normal", "Slow": "Fast/Fastest",
                             "Below Average": "Fast/Fastest", "Normal": "Fast/Fastest",
                             "Fast/Fastest": "Fast/Fastest"},
        "aliases": {"below avg.": "Below Average", "average": "Normal", "fast": "Fast/Fastest",
                    "fastest": "Fast/Fastest"},
    },
}
UNMAPPED_NOTE = ("Label is not in the user-provided v16.0.0 ladder; no alias is approved, "
                 "so no required skill level is inferred.")


def canonical_tier(kind, label):
    rule = RULES[kind]
    if not isinstance(label, str) or not label.strip():
        return None
    cleaned = " ".join(label.split())
    for tier in rule["ladder"]:
        if cleaned.casefold() == tier.casefold():
            return tier
    return rule["aliases"].get(cleaned.casefold())


def lookup(kind, label, scope):
    """Return the skill level needed to reach the best tier for one source label."""
    rule = RULES[kind]
    tier = canonical_tier(kind, label)
    result = {"kind": kind, "skill": rule["skill"], "source_label": label, "label_scope": scope,
              "rule_source": RULE_SOURCE, "rule_version": GAME_VERSION,
              "target_tier": rule["target_tier"]}
    if tier is None:
        result.update({"status": "unmapped", "canonical_tier": None, "required_level": None,
                       "achievable": None, "best_tier_at_lv3": None, "note": UNMAPPED_NOTE})
        return result
    required = rule["required_level"][tier]
    result.update({"status": "mapped", "canonical_tier": tier, "required_level": required,
                   "achievable": required is not None,
                   "best_tier_at_lv3": rule["best_tier_at_lv3"][tier]})
    return result


def rules_document():
    return {
        "schema_version": 1, "rule_source": RULE_SOURCE, "rule_version": GAME_VERSION,
        "description": ("Skill level required for the lowest recoil tier and the fastest reload "
                        "tier, transcribed from the user-provided v16.0.0 table."),
        "label_policy": ("Only exact ladder labels and listed aliases are mapped. Kiranico weapon-level "
                         "labels such as 'Some' and 'Above Avg.' stay unmapped."),
        "rules": [
            {"kind": kind, "skill": rule["skill"], "target_tier": rule["target_tier"],
             "tiers": [{"tier": tier, "required_level": rule["required_level"][tier],
                        "achievable": rule["required_level"][tier] is not None,
                        "best_tier_at_lv3": rule["best_tier_at_lv3"][tier]}
                       for tier in rule["ladder"]],
             "aliases": dict(rule["aliases"])}
            for kind, rule in RULES.items()
        ],
    }
