"""Export a draft plan as JSON (machine) or Markdown (match-day sheet)."""

from esports import draft_rules as rules

from .state import game_state, series_conflicts


def plan_to_dict(plan) -> dict:
    games = []
    for g in plan.games.order_by("number"):
        st = game_state(plan, g.number)
        team_of = {plan.our_team_id: plan.our_team.short_code, plan.opponent_id: plan.opponent.short_code}
        games.append(
            {
                "number": g.number,
                "our_side": g.our_side,
                "blue": g.blue_team.short_code,
                "red": g.red_team.short_code,
                "result": g.result or None,
                "notes": g.notes,
                "complete": st.complete,
                "actions": [
                    {
                        "order": a.order,
                        "label": a.step.label,
                        "team": team_of[g.team_id_on(a.step.side)],
                        "type": a.step.action,
                        "champion": a.champion.name,
                        "role": a.role or None,
                    }
                    for a in st.actions
                ],
                "fearless_locked_at_start": sorted(
                    {c.name for c in st.champions if c.id in st.fearless_locked(plan.our_team_id)}
                ),
            }
        )
    return {
        "plan": plan.name,
        "our_team": plan.our_team.name,
        "opponent": plan.opponent.name,
        "format": plan.format,
        "fearless_mode": plan.fearless_mode,
        "notes": plan.notes,
        "games": games,
        "conflicts": series_conflicts(plan),
    }


def plan_to_markdown(plan) -> str:
    d = plan_to_dict(plan)
    lines = [
        f"# {d['plan']}",
        "",
        f"**{d['our_team']} vs {d['opponent']}** | {d['format'].upper()} | Fearless: {d['fearless_mode']}",
        "",
    ]
    if d["notes"]:
        lines += [d["notes"], ""]
    if d["conflicts"]:
        lines += ["> **Warning:** some planned picks are Fearless-locked by earlier games:"]
        lines += [f"> - G{c['game']} step {c['order']}: {c['champion']}" for c in d["conflicts"]]
        lines.append("")
    for g in d["games"]:
        lines += [f"## Game {g['number']} | {g['blue']} (blue) vs {g['red']} (red)", ""]
        if g["fearless_locked_at_start"]:
            lines += [f"Locked by Fearless: {', '.join(g['fearless_locked_at_start'])}", ""]
        if g["actions"]:
            by_team_type = {}
            for a in g["actions"]:
                by_team_type.setdefault((a["team"], a["type"]), []).append(
                    a["champion"] + (f" ({a['role']})" if a["role"] else "")
                )
            for side_code in (g["blue"], g["red"]):
                bans = ", ".join(by_team_type.get((side_code, rules.BAN), [])) or "-"
                picks = ", ".join(by_team_type.get((side_code, rules.PICK), [])) or "-"
                lines += [f"- **{side_code}** bans: {bans}", f"- **{side_code}** picks: {picks}"]
            lines.append("")
        else:
            lines += ["_Not planned yet._", ""]
        if g["notes"]:
            lines += [f"Notes: {g['notes']}", ""]
    return "\n".join(lines)
