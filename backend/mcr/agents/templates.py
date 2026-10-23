"""Deterministic, grounded text for every agent task.

Used in two ways:
1. Offline mode: the OfflineChatClient answers agent prompts with these renderings, so the whole
   pipeline (and the demo) runs with no cloud dependency.
2. Recovery: when an LLM call times out, fails, or is rejected by the FactGuard twice, the crew
   falls back to these texts so the broadcast never goes blank.
"""

from __future__ import annotations

LANGS = ("en", "tr")
PERSONAS = ("analyst", "casual", "fan_home", "fan_away")


def num(v, lang: str) -> str:
    if isinstance(v, float):
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s.replace(".", ",") if lang == "tr" else s
    return str(v)


def _n(facts: dict, key: str, default: float) -> float:
    v = facts.get(key, default)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _f(facts: dict, lang: str) -> dict:
    return {k: num(v, lang) if isinstance(v, (int, float)) and not isinstance(v, bool) else v for k, v in facts.items()}


# --------------------------------------------------------------------------- analyst "why it matters"
def explain(mtype: str, facts: dict, lang: str = "en") -> str:
    f = _f(facts, lang)
    en = {
        "goal": (
            "{player} scores with a {xg} xG chance"
            + (", a finish well above expectation" if _n(facts, "xg", 1) < 0.1 else "")
            + ". {team} now has {team_xg} xG against {opponent_xg}"
            + (", which says the scoreline flatters them." if _n(facts, "team_xg", 0) < _n(facts, "opponent_xg", 0)
               else ", so the lead reflects the balance of chances.")
        ),
        "big_chance": "{player} misses a {xg} xG chance. Chances like this go in roughly one time in "
                      + str(max(1, round(1 / max(_n(facts, "xg", 0.5), 0.01))))
                      + ", so {team} should have been ahead of the {score_line} scoreline.",
        "pressure_surge": "{team} has lifted its press to {pressures_per_min} pressures per minute, {ratio}x its "
                          "match average of {baseline_per_min}. It has forced {turnovers_forced_5min} turnovers in "
                          "five minutes, a tactical change rather than a random spell.",
        "momentum_shift": "Momentum has swung to {team} (index {momentum}). They have built {xg_5min} xG in the last "
                          "five minutes, territory and threat are moving their way.",
        "chaos_shift": (
            "The game has turned chaotic: {turnovers_per_min} turnovers a minute and only {avg_chain_passes} passes "
            "per possession. Neither side controls the ball, which raises the odds of a mistake-driven goal."
            if facts.get("regime") == "chaos" else
            "The game has settled into control: possessions average {avg_chain_passes} passes and turnovers have "
            "dropped to {turnovers_per_min} a minute. Rhythm now favours the patient side."
        ),
        "elite_pass": "{player} found {receiver} with a pass rated {difficulty} out of 10 for difficulty: "
                      "{distance_m} m, {progression_m} m forward"
                      + (", under pressure" if facts.get("under_pressure") else "") + ".",
        "milestone": "{player} reaches {milestone} at {pass_accuracy}% accuracy, the hub of the team's build-up.",
        "rocket_shot": "{player} strikes it at {shot_speed_kmh} km/h.",
        "sprint": "{player} hits {speed_kmh} km/h.",
        "half_time": "Half-time, {score_line}.",
        "full_time": "Full-time, {score_line}.",
    }
    tr = {
        "goal": (
            "{player}, {xg} xG'lik bir pozisyondan golü buluyor"
            + (", beklentinin çok üzerinde bir bitiricilik" if _n(facts, "xg", 1) < 0.1 else "")
            + ". {team} artık {team_xg} xG üretti, rakip {opponent_xg}"
            + ("; skor onları olduğundan iyi gösteriyor." if _n(facts, "team_xg", 0) < _n(facts, "opponent_xg", 0)
               else "; skor pozisyon dengesini yansıtıyor.")
        ),
        "big_chance": "{player} {xg} xG'lik bir fırsatı kaçırıyor. Bu tür pozisyonlar kabaca "
                      + str(max(1, round(1 / max(_n(facts, "xg", 0.5), 0.01))))
                      + " denemede bir gole dönüyor; {team} {score_line} skorundan daha iyi bir yerde olmalıydı.",
        "pressure_surge": "{team} presi dakikada {pressures_per_min} baskıya çıkardı, maç ortalaması {baseline_per_min} "
                          "idi ({ratio} kat). Son beş dakikada {turnovers_forced_5min} top kaybı yaptırdılar; bu "
                          "rastgele bir dönem değil, taktik bir değişiklik.",
        "momentum_shift": "Momentum {team} tarafına döndü (endeks {momentum}). Son beş dakikada {xg_5min} xG ürettiler, "
                          "alan ve tehdit onlardan yana.",
        "chaos_shift": (
            "Maç kaosa döndü: dakikada {turnovers_per_min} top kaybı, topa sahip olma başına sadece "
            "{avg_chain_passes} pas. Kimse oyunu kontrol edemiyor, hatadan gelen gol ihtimali artıyor."
            if facts.get("regime") == "chaos" else
            "Oyun kontrole oturdu: atak başına ortalama {avg_chain_passes} pas, top kaybı dakikada "
            "{turnovers_per_min}'e düştü. Ritim sabırlı oynayan tarafın lehine."
        ),
        "elite_pass": "{player}, {receiver}'a zorluk puanı 10 üzerinden {difficulty} olan bir pas attı: "
                      "{distance_m} m, {progression_m} m ileri"
                      + (", baskı altında" if facts.get("under_pressure") else "") + ".",
        "milestone": "{player} %{pass_accuracy} isabetle {milestone} eşiğine ulaştı; oyun kurulumunun merkezi o.",
        "rocket_shot": "{player} topa {shot_speed_kmh} km/s hızla vurdu.",
        "sprint": "{player} {speed_kmh} km/s'ye ulaştı.",
        "half_time": "Devre arası, {score_line}.",
        "full_time": "Maç sonu, {score_line}.",
    }
    if mtype == "milestone" and lang == "tr":
        f = dict(f, milestone=str(facts.get("milestone", "")).replace("50 completed passes", "50 isabetli pas"))
    table = tr if lang == "tr" else en
    return table.get(mtype, "{score_line}").format(**f)


# --------------------------------------------------------------------------- live commentary line
def narrate(mtype: str, facts: dict, lang: str = "en") -> str:
    f = _f(facts, lang)
    en = {
        "goal": "GOAL! {player} for {team}! It's {score_line}.",
        "big_chance": "What a chance for {player}! {xg} xG and it doesn't go in.",
        "pressure_surge": "{team} are swarming now, pressing high and winning it back.",
        "momentum_shift": "The tide is turning. {team} are on top.",
        "chaos_shift": ("End to end now, nobody can keep the ball!" if facts.get("regime") == "chaos"
                        else "The tempo drops, this is a game of patience now."),
        "elite_pass": "Sublime from {player}, threading it through to {receiver}.",
        "milestone": "{player} keeps making it tick, {milestone}.",
        "rocket_shot": "{player} absolutely leathers it, {shot_speed_kmh} km/h!",
        "sprint": "{player} is flying, {speed_kmh} km/h!",
        "half_time": "The whistle goes for half-time. {score_line}.",
        "full_time": "That's full-time! {score_line}.",
    }
    tr = {
        "goal": "GOOOL! {team} adına {player}! Skor {score_line}.",
        "big_chance": "{player} için ne fırsat! {xg} xG ama top ağlara gitmiyor.",
        "pressure_surge": "{team} rakibi boğuyor, önde pres yapıp topu geri kazanıyor.",
        "momentum_shift": "Rüzgâr dönüyor. Üstünlük artık {team} tarafında.",
        "chaos_shift": ("Karşılıklı ataklar, kimse topu tutamıyor!" if facts.get("regime") == "chaos"
                        else "Tempo düştü, artık bir sabır oyunu var."),
        "elite_pass": "{player}'dan harika bir ara pas, {receiver} topla buluşuyor.",
        "milestone": "{player} oyunu döndürmeye devam ediyor: {milestone}.",
        "rocket_shot": "{player} füze gibi vurdu, {shot_speed_kmh} km/s!",
        "sprint": "{player} uçuyor, {speed_kmh} km/s!",
        "half_time": "İlk yarı sona erdi. {score_line}.",
        "full_time": "Ve maç bitti! {score_line}.",
    }
    if mtype == "milestone" and lang == "tr":
        f = dict(f, milestone=str(facts.get("milestone", "")).replace("50 completed passes", "50 isabetli pas"))
    return (tr if lang == "tr" else en).get(mtype, "{score_line}").format(**f)


# --------------------------------------------------------------------------- persona cards
def personas(mtype: str, facts: dict, why: str, lang: str, home: str, away: str, team_id_is_home: bool | None) -> dict:
    """Return {persona: text} for one language."""
    line = narrate(mtype, facts, lang)
    analyst = why
    if lang == "tr":
        casual = {
            "goal": "{player} attı! Skor {score_line}.",
            "big_chance": "{player} çok net bir golü kaçırdı.",
            "pressure_surge": "{team} topu rakip yarı sahada çalmaya başladı, baskı arttı.",
            "momentum_shift": "Maçın havası değişti, {team} daha tehlikeli.",
            "chaos_shift": "Oyun çok hareketlendi, her an bir şey olabilir." if facts.get("regime") == "chaos"
            else "Oyun sakinleşti, takımlar topu daha çok tutuyor.",
            "elite_pass": "{player}'dan müthiş bir pas!",
            "milestone": "{player} bugün oyunun merkezinde.",
        }.get(mtype, line)
    else:
        casual = {
            "goal": "{player} scores! It's {score_line}.",
            "big_chance": "{player} misses a sitter.",
            "pressure_surge": "{team} are winning the ball back high up the pitch.",
            "momentum_shift": "The mood has changed, {team} look the more dangerous side.",
            "chaos_shift": "It's wild out there, anything could happen." if facts.get("regime") == "chaos"
            else "Things have calmed down, both teams are keeping the ball.",
            "elite_pass": "What a pass from {player}!",
            "milestone": "{player} is running the show today.",
        }.get(mtype, line)
    f = _f(facts, lang)
    casual = casual.format(**f)

    def fan(for_home: bool) -> str:
        club = home if for_home else away
        if team_id_is_home is None:
            return casual
        good = team_id_is_home == for_home
        if lang == "tr":
            return (f"{club} için harika haber: " if good else f"{club} için alarm: ") + casual
        return (f"Great news for {club}: " if good else f"Warning signs for {club}: ") + casual

    return {"analyst": analyst, "casual": casual, "fan_home": fan(True), "fan_away": fan(False)}


# --------------------------------------------------------------------------- recaps
def recap(kind: str, facts: dict, moments: list[dict], lang: str, home: str, away: str) -> dict:
    sl = facts.get("score_line", "")
    xg = facts.get("xg", {})
    poss = facts.get("possession", {})
    ids = list(xg.keys())
    hx, ax = (xg.get(ids[0], 0), xg.get(ids[1], 0)) if len(ids) == 2 else (0, 0)
    hp, ap = (poss.get(ids[0], 0), poss.get(ids[1], 0)) if len(ids) == 2 else (0, 0)
    goals = [m for m in moments if m["type"] == "goal"]
    surges = [m for m in moments if m["type"] == "pressure_surge"]
    if lang == "tr":
        headline = ("Devre arası: " if kind == "half_time" else "Maç sonu: ") + sl
        parts = [f"{home} {num(hx, lang)} xG, {away} {num(ax, lang)} xG üretti; topa sahip olma %{num(hp, lang)} - %{num(ap, lang)}."]
        if goals:
            parts.append("Goller: " + ", ".join(f"{g['facts']['player']} ({g['minute']}')" for g in goals) + ".")
        if surges:
            s = max(surges, key=lambda m: m['facts']['ratio'])
            parts.append(f"Kırılma anı: {s['minute']}. dakikada {s['facts']['team']} presi {num(s['facts']['ratio'], lang)} kat artırdı.")
        bullets = [f"Skor: {sl}", f"xG: {num(hx, lang)} - {num(ax, lang)}"] + (
            [f"Kilit an: {goals[-1]['facts']['player']} {goals[-1]['minute']}'"] if goals else ["Gol yok, pozisyon savaşı"])
    else:
        headline = ("Half-time: " if kind == "half_time" else "Full-time: ") + sl
        parts = [f"{home} created {num(hx, lang)} xG to {away}'s {num(ax, lang)}, with possession split {num(hp, lang)}% to {num(ap, lang)}%."]
        if goals:
            parts.append("Goals: " + ", ".join(f"{g['facts']['player']} ({g['minute']}')" for g in goals) + ".")
        if surges:
            s = max(surges, key=lambda m: m['facts']['ratio'])
            parts.append(f"Turning point: in minute {s['minute']} {s['facts']['team']} raised their press {num(s['facts']['ratio'], lang)}x.")
        bullets = [f"Score: {sl}", f"xG: {num(hx, lang)} - {num(ax, lang)}"] + (
            [f"Key moment: {goals[-1]['facts']['player']} {goals[-1]['minute']}'"] if goals else ["No goals, a battle of chances"])
    return {"headline": headline, "summary": " ".join(parts), "bullets": bullets}
