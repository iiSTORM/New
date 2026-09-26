try {

const { useState, useEffect, useMemo, useContext, createContext, Fragment } = React;


/* ============================================================
   THEME — a small, deliberate token system rather than scattered
   hex codes. The accent shifts automatically with the selected
   game (a muted hextech gold for League, Valorant's red for
   Valorant) since the chrome itself should signal which broadcast
   you're watching. A user override replaces that automatic accent
   when set. Corner style ("angular" cut corners vs classic
   "rounded") is a separate, persisted preference — angular is the
   new default, evoking scoreboard/lower-third graphics rather than
   a generic rounded SaaS card.
   ============================================================ */

const BASE_TOKENS = {
  void: "#14181F",
  graphite: "#1B212B",
  graphiteLight: "#222A38",
  steel: "#2C3444",
  steelSoft: "#232A38",
  text: "#EDEFF4",
  textDim: "#A8B0C0",
  textFaint: "#5C6478",
  good: "#6EBF8B",
  bad: "#DA7A6D",
};

const GAME_ACCENTS = {
  lol: { accent: "#C9A86A", accentSoft: "#C9A86A18", accentBorder: "#C9A86A55", name: "Hextech Gold" },
  valorant: { accent: "#FF4655", accentSoft: "#FF465518", accentBorder: "#FF465555", name: "Valorant Red" },
  cs2: { accent: "#4FC3F7", accentSoft: "#4FC3F718", accentBorder: "#4FC3F755", name: "Tactical Cyan" },
};

const ACCENT_SWATCHES = [
  { name: "Hextech Gold", hex: "#C9A86A" },
  { name: "Valorant Red", hex: "#FF4655" },
  { name: "Signal Cyan", hex: "#4FD8E8" },
  { name: "Reticle Green", hex: "#7FE07A" },
  { name: "Violet", hex: "#B07FE0" },
  { name: "Ember", hex: "#E08A4F" },
];

const CORNER_CUT = 12; // px, used by both the clip-path and the SVG corner accents

function cardShape(cornerStyle) {
  if (cornerStyle === "rounded") return { borderRadius: 12 };
  const c = CORNER_CUT;
  return {
    borderRadius: 0,
    clipPath: `polygon(0 0, calc(100% - ${c}px) 0, 100% ${c}px, 100% 100%, ${c}px 100%, 0 calc(100% - ${c}px))`,
  };
}

// Drop shadows barely register on a near-black background — the trick dark
// UIs actually use for a "raised surface" feeling is a faint highlight on
// the top edge (simulating light catching a raised panel) combined with a
// real shadow for separation from whatever's behind it. Two tiers: "card"
// for normal content surfaces, "sunken" for the opposite effect (a recessed
// well, used for the page's nav/control surfaces to differentiate them from
// content cards rather than making everything look identical).
function elevation(tier = "card") {
  if (tier === "sunken") {
    return { boxShadow: "inset 0 1px 3px rgba(0,0,0,0.5), inset 0 0 0 1px rgba(255,255,255,0.02)" };
  }
  return { boxShadow: "0 3px 10px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.05)" };
}

// Targeting-bracket accents (see the .kp-bracket CSS rule) only make sense
// paired with the angular corner style — in rounded mode there's no "sharp
// corner" for them to sit in, so they're skipped entirely rather than
// looking out of place. bracketStyle's CSS custom property lets the accent
// color track the current theme/game without a separate CSS rule per color.
function bracketClass(theme) {
  return theme.cornerStyle === "angular" ? "kp-bracket" : "";
}
function bracketStyle(theme, color) {
  return theme.cornerStyle === "angular" ? { "--kp-bracket-color": color || theme.accent } : {};
}

// Initials for the player-badge circle — first 2 alphanumeric characters,
// uppercased. Handles the handle-style names common in esports (e.g.
// "sh1ro" -> "SH", "ZywOo" -> "ZY") rather than assuming space-separated
// first/last names, which most pro handles don't have.
function initialsFor(name) {
  const clean = (name || "").replace(/[^a-zA-Z0-9]/g, "");
  return (clean.slice(0, 2) || "?").toUpperCase();
}

/* ---------- Shared presentational primitives ----------
   Team identity used to be carried by colouring the team's NAME in its
   own brand colour. With two teams per card and six saturated brand
   colours on screen at once that reads as noise, and the colours were
   doing double duty as both identity and emphasis, so nothing could be
   emphasised. The standings table already had the better answer — a thin
   colour bar next to neutral text — so that treatment is shared here and
   the accent is freed up to mean "this is the number that matters". */
function TeamTag({ name, color, size = 14, dim = false, region = null }) {
  const theme = useTheme();
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 7, minWidth: 0 }}>
      <span aria-hidden="true" style={{
        width: 3, height: size + 2, borderRadius: 2, background: color, flexShrink: 0,
        boxShadow: `0 0 8px ${color}55`,
      }} />
      <span style={{
        fontFamily: "'Fraunces', serif", fontWeight: 600, fontSize: size,
        color: dim ? theme.textDim : theme.text, whiteSpace: "nowrap",
        overflow: "hidden", textOverflow: "ellipsis",
      }}>{name}</span>
      {/* Muted and secondary on purpose: it qualifies the name rather
          than competing with it, and it is only ever present on a board
          where the league differs from the tab. Labelled for a screen
          reader so it is not read as a loose word after the team. */}
      {region && (
        <span style={{
          fontFamily: "'IBM Plex Mono', monospace", fontSize: Math.max(9, size - 5),
          fontWeight: 600, letterSpacing: 0.3, color: theme.textFaint,
          border: `1px solid ${theme.steel}`, borderRadius: 4,
          padding: "1px 5px", flexShrink: 0, whiteSpace: "nowrap",
        }} aria-label={`from ${region}`}>{region}</span>
      )}
    </span>
  );
}

function Chevron({ open, color, size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true"
         style={{ flexShrink: 0, transition: "transform 0.2s ease", transform: open ? "rotate(180deg)" : "none" }}>
      <path d="M6 9l6 6 6-6" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* A number with its label underneath — the app's primary readout. Sizes
   are deliberately far apart: the old cards set the projection at 11px,
   the same size as the hint text beside it, so the one figure a visitor
   comes for had no more weight than "tap to expand". */
/* The posted line and the model's disagreement with it.

   Edge is projection minus line: positive means the model is above the
   line. It is deliberately shown only when the prop's map window matches
   the projection on screen and the line is fresh — a stale or
   wrong-window edge is worse than none, because it reads as actionable. */
function mapWindowLabel(maps) {
  return maps === 1 ? "map 1" : `maps 1-${maps}`;
}

function PropReadout({ prop, projection, fresh, ageMinutes }) {
  const theme = useTheme();
  if (!prop) return null;
  const mapWindow = mapWindowLabel(prop.maps);

  /* Several lines posted for the same player, stat and fixture, with
     nothing naming which is the market one. They are alternate payouts,
     and an edge against the wrong one is not a rounding error: 10.5, 8.5
     and 6.5 against a projection of 9 point in opposite directions. The
     line is still worth showing; the edge is not. */
  if (prop.lineCount > 1) {
    return (
      <div style={{ textAlign: "right", flexShrink: 0, minWidth: 74 }}
           title={`${prop.lineCount} lines are posted for this player over ${mapWindow}, and which is the market line is not stated. No edge is shown rather than guessing one.`}>
        <div className="kp-num" style={{ fontSize: 15, fontWeight: 600, color: theme.textFaint }}>{prop.line}</div>
        <div className="kp-num" style={{ fontSize: 11, fontWeight: 700, color: theme.textFaint, marginTop: 2 }}>
          {prop.lineCount} lines
        </div>
        <div style={{ fontSize: 9, letterSpacing: 0.6, textTransform: "uppercase", color: theme.textFaint, marginTop: 3 }}>
          {mapWindow}
        </div>
      </div>
    );
  }

  const edge = projection - prop.line;
  // Live until the FIXTURE starts, not until the payload gets old. An
  // old line on a match that has not been played is still the line.
  const live = propIsLive(prop);
  const aged = live && !fresh ? ageLabel(ageMinutes) : null;
  const tone = !live ? theme.textFaint : edge > 0 ? theme.good : edge < 0 ? theme.bad : theme.textDim;
  return (
    <div style={{ textAlign: "right", flexShrink: 0, minWidth: 74 }}
         title={`Line ${prop.line} over ${mapWindow}. Projection over the same ${prop.maps} map${prop.maps === 1 ? "" : "s"}: ${projection.toFixed(1)}.`
                + (aged ? ` Posted line is ${aged} — lines move, so check it before acting.` : "")
                + (live ? "" : " This fixture has already started.")}>
      <div className="kp-num" style={{ fontSize: 15, fontWeight: 600, color: live ? theme.text : theme.textFaint }}>
        {prop.line}
      </div>
      <div className="kp-num" style={{ fontSize: 11, fontWeight: 700, color: tone, marginTop: 2 }}>
        {live ? `${edge > 0 ? "+" : ""}${edge.toFixed(1)}` : "started"}
      </div>
      {/* The window is named rather than left implicit. The edge above is
          computed over it, and it is not necessarily the number of games
          the selector shows: a Bo5 line can sit directly beneath a Bo3 one
          in the same list, and the reader has to know which they are
          reading. */}
      <div style={{ fontSize: 9, letterSpacing: 0.6, textTransform: "uppercase", color: theme.textFaint, marginTop: 3 }}>
        {!live ? `${mapWindow} · started` : aged ? `${mapWindow} · ${aged}` : mapWindow}
      </div>
    </div>
  );
}

function StatReadout({ value, label, size = 30, color, align = "right" }) {
  const theme = useTheme();
  return (
    <div style={{ textAlign: align, flexShrink: 0 }}>
      <div className="kp-num" style={{ fontSize: size, fontWeight: 700, lineHeight: 1, color: color || theme.text }}>{value}</div>
      <div style={{ fontSize: 9.5, letterSpacing: 0.9, textTransform: "uppercase", color: theme.textFaint, marginTop: 5, fontWeight: 600 }}>{label}</div>
    </div>
  );
}

/* Signed delta shown as a badge. Accuracy is this product's real claim,
   so "how far off was the model" deserves to be legible at a glance
   rather than buried mid-sentence. */
function DeltaBadge({ value, digits = 1, goodBelow = 2.5 }) {
  const theme = useTheme();
  const magnitude = Math.abs(value);
  const tone = magnitude <= goodBelow ? theme.good : magnitude <= goodBelow * 2 ? theme.accent : theme.bad;
  return (
    <span className="kp-chip kp-num" style={{ background: tone + "1A", color: tone, border: `1px solid ${tone}33` }}>
      ±{magnitude.toFixed(digits)}
    </span>
  );
}

/* Shared, larger player row used under both upcoming and past matches —
   previously each card hand-rolled its own cramped 12px/7px-padding grid
   row independently, which was the main source of the "too small to
   read" feeling. One component now, sized for real readability, with a
   flexible stats array so it works for both the single-number "PROJ"
   case (future matches) and the three-number "PROJ / ACT / DIFF" case
   (past matches) without duplicating markup. */
/* `props` is a list, one entry per posted map window, with
   `propProjections` holding this model's number over each of those
   same windows at the matching index. A player can hold a map-1 line
   and a maps-1-2 line in one fixture -- two markets, two lines, two
   edges -- and showing one of them would leave the other invisible
   with nothing on screen saying so. */
function MatchPlayerRow({ theme, teamColor, name, role, extraChip, stats, r, p, cfg, games, pastMatches, team, props, propProjections, propsFresh, propsAge }) {
  const [open, setOpen] = useState(false);
  const canExpand = !!(r && p && cfg); // callers that don't pass the full breakdown just get the plain row, same as before
  return (
    <div
      className={canExpand ? "kp-clickable" : undefined}
      onClick={canExpand ? () => setOpen(!open) : undefined}
      style={{
        padding: "15px 18px", borderTop: `1px solid ${theme.steelSoft}`,
        cursor: canExpand ? "pointer" : "default",
        background: open ? theme.graphiteLight : "transparent",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11, minWidth: 0 }}>
          <span
            style={{
              width: 34, height: 34, borderRadius: "50%", flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: `${teamColor}22`, border: `1.5px solid ${teamColor}`,
              color: teamColor, fontFamily: "'Fraunces', serif", fontWeight: 700, fontSize: 12,
            }}
          >
            {initialsFor(name)}
          </span>
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 15, fontWeight: 600, color: theme.text, fontFamily: "'Fraunces', serif",
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}
            >
              {name}
            </div>
            {(role || extraChip) && (
              <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
                {role && <span className="kp-chip" style={{ background: theme.steelSoft, color: theme.textFaint }}>{role}</span>}
                {r && <EvidenceChip games={r.evidenceGames} compact />}
                {extraChip}
              </div>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 16, flexShrink: 0 }}>
          {(props || []).map((posted, i) => (
            <PropReadout key={posted.maps} prop={posted}
                         projection={(propProjections || [])[i]}
                         fresh={propsFresh} ageMinutes={propsAge} />
          ))}
          {stats.map((s, i) => (
            <div key={i} style={{ textAlign: "right", minWidth: s.big ? 52 : 38 }}>
              <div style={{ fontSize: 9, letterSpacing: 0.6, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace" }}>{s.label}</div>
              <div
                style={{
                  fontSize: s.big ? 24 : 14, fontWeight: s.big ? 600 : 700,
                  fontFamily: s.big ? "'Fraunces', serif" : "'IBM Plex Mono', monospace",
                  color: s.big ? theme.accent : (s.color || theme.text), marginTop: 2,
                }}
              >
                {s.value}
              </div>
            </div>
          ))}
        </div>
      </div>
      {open && canExpand && (
        <div style={{ marginTop: 12 }}>
          <ProjectionDetail r={r} p={p} cfg={cfg} games={games} pastMatches={pastMatches} team={team} />
        </div>
      )}
    </div>
  );
}

const ThemeContext = createContext(null);
function useTheme() {
  return useContext(ThemeContext);
}
const ChampionStatsContext = createContext(null);
const PropsContext = createContext(null);
function useProps() {
  return useContext(PropsContext);
}

/* Which league a team actually plays in, for the tabs where that is not
   the tab you are looking at.

   An international event draws its field out of four regional leagues,
   so "Paper Rex vs Team Liquid" on the Champions board is two teams
   whose form, and whose opponents all season, came from opposite sides
   of the world. The region is the single most useful thing to know
   about a fixture there and it was the one thing the card did not say.

   Returns a lookup rather than a map so callers stay simple, and null
   for the ordinary case: in a regional tab every team's home league IS
   that tab, so nothing is tagged and no rule about "is this an
   international event" has to be maintained anywhere. The tag appears
   exactly where it is informative, by construction. */
const HomeRegionContext = createContext(null);
function useHomeRegion() {
  return useContext(HomeRegionContext) || (() => null);
}

/* "VCT Americas" is most of a card's width. The league prefix is the
   part every team on an international board shares, so it carries no
   information there -- what distinguishes them is what follows it. */
function shortRegionLabel(key) {
  return String(key || "").replace(/^(VCT|LTA)\s+/i, "").trim() || String(key || "");
}

function homeRegionLookup(regionsData, regionKey) {
  if (!regionsData || !regionsData[regionKey]) return () => null;
  const here = regionsData[regionKey].teams || {};
  const cache = new Map();
  return (teamName) => {
    if (!teamName) return null;
    if (cache.has(teamName)) return cache.get(teamName);
    let home = null;
    // What the scraper recorded when it lent this roster across. It knows
    // which region it copied from, so it beats re-deriving it.
    const entry = here[teamName];
    if (entry && entry.from_home_region) {
      home = entry.from_home_region;
    } else {
      // Otherwise the region where they have actually PLAYED the most --
      // the same rule the scraper uses to pick a donor, so a team that
      // has since played at the event resolves to the same league as one
      // that has not.
      //
      // THE TAB ITSELF IS IN THE COMPARISON, which is the whole
      // correctness of this. Excluding it looks right and is not: a
      // Pacific team also appears under Champions, so from the Pacific
      // tab the only other candidate is Champions and the team gets
      // tagged as a visitor in its own league. Counting the tab too
      // makes Pacific win on matches and the tag disappear, which is
      // the same comparison answering both questions.
      let best = -1;
      for (const [key, rd] of Object.entries(regionsData)) {
        if (!rd || !(rd.teams || {})[teamName]) continue;
        let played = 0;
        for (const m of rd.past_matches || []) {
          if (m.teamA === teamName || m.teamB === teamName) played += 1;
        }
        // Ties go to the tab being viewed, so a team with no matches
        // anywhere is never labelled a visitor on its own board.
        if (played > best || (played === best && key === regionKey)) {
          best = played;
          home = key;
        }
      }
    }
    const label = home && home !== regionKey ? shortRegionLabel(home) : null;
    cache.set(teamName, label);
    return label;
  };
}

/* How far a posted line's start time may sit from a match's own before it
   is taken to be a different match. Providers round start times and this
   app's schedule carries its own, so they rarely agree to the minute; six
   hours is measured rather than chosen: across a real board, 24 of the 25
   legitimate pairings were under an hour apart, and the only thing a wider
   window added was a 10:00 line attaching itself to a 16:00 match six
   hours away. CS2 runs fixtures every couple of hours, so anything looser
   separates nothing. */
const PROP_MATCH_WINDOW_HOURS = 2;

/* Find the posted line for this player, in THIS match.

   The map window is not a setting to be matched — it is a fact about the
   fixture. A Bo1 is posted as "Map 1", a Bo3 as "Maps 1-2", a Bo5 as
   "Maps 1-3", and which one a given match is varies by game, by split and
   by round. So the line states the window and the projection is computed
   over that same window, rather than the line being hidden whenever it
   disagrees with a control the reader has to set by hand.

   Two things still have to be resolved, and getting either wrong produces
   a confident, wrong edge rather than a missing one:

   the match  — a player can hold lines in two matches on the same day, so
                the nearest start time wins and anything outside the window
                above is a different match, not this one.
   the line   — the provider posts alternate lines at other payouts beside
                the market line. A real payload has three kills lines for
                one LoL player in one match: 10.5, 8.5 and 6.5. Against a
                projection of 9 those give opposite verdicts, so picking
                one arbitrarily invents an edge out of a payout structure.
                `odds_type` names the market line where the provider sends
                it; where it does not, the ambiguity is reported rather
                than resolved, and no edge is drawn. */
function propsFor(propsData, game, playerName, statType, matchDate) {
  if (!propsData || !propsData.props) return [];
  const forGame = propsData.props[game];
  if (!forGame) return [];
  const list = (forGame[playerName] || []).filter((p) => p.stat === statType);
  if (!list.length) return [];

  let candidates = list;
  const when = String(matchDate || "");
  // Not every source states a kickoff time. gol.gg and bo3.gg give a full
  // timestamp; vlr.gg gives a bare date, and comparing a line posted for
  // 09:00 against a fixture read as midnight puts every one of them nine
  // hours out and rejects the lot. A date carries no time, so it gets
  // matched at the resolution it actually has: the day.
  const hasClock = /\d{1,2}:\d{2}/.test(when);
  const matchMs = when ? new Date(when).getTime() : NaN;

  if (!isNaN(matchMs) && hasClock) {
    const timed = list
      .map((p) => ({ p, delta: Math.abs(new Date(p.start_time).getTime() - matchMs) }))
      .filter((x) => !isNaN(x.delta) && x.delta <= PROP_MATCH_WINDOW_HOURS * 3600000);
    // Lines exist for this player, but none for this fixture. Showing
    // another match's line here would be worse than showing none.
    if (!timed.length) return [];
    const nearest = Math.min(...timed.map((x) => x.delta));
    candidates = timed.filter((x) => x.delta === nearest).map((x) => x.p);
  } else if (!isNaN(matchMs)) {
    const day = when.slice(0, 10);
    const sameDay = list.filter((p) => {
      const at = new Date(p.start_time);
      return !isNaN(at) && at.toISOString().slice(0, 10) === day;
    });
    if (!sameDay.length) return [];
    candidates = sameDay;
  }

  /* The window is a grouping key, not a tiebreak.

     Pooling the windows together and then counting distinct values, as
     this used to, reads a map-1 line of 12.5 and a maps-1-2 line of 24.5
     as two alternate payouts with no market line named -- and suppresses
     the edge on both. They are not alternates. They are two markets over
     two different sets of maps, and each has its own line, its own
     projection and its own edge.

     So alternate-payout ambiguity is resolved INSIDE a window, where it
     is a real question, and never across windows, where it is not. */
  const byWindow = new Map();
  for (const p of candidates) {
    const key = typeof p.maps === "number" ? p.maps : -1;
    if (!byWindow.has(key)) byWindow.set(key, []);
    byWindow.get(key).push(p);
  }

  const out = [];
  for (const maps of [...byWindow.keys()].sort((a, b) => a - b)) {
    let group = byWindow.get(maps);
    const market = group.filter(
      (p) => String(p.odds_type || "").toLowerCase() === "standard"
    );
    if (market.length) group = market;
    const distinct = [...new Set(group.map((p) => p.line))];
    out.push({ ...group[0], lineCount: distinct.length });
  }
  return out;
}

/* The projection to put beside a posted line.

   Not the one the selector is showing: the line covers a stated number of
   maps, so the projection has to cover the same ones. breakdown.perGame is
   a per-map rate, which makes the conversion a multiplication — the reason
   this is a function at all is that doing it at the call site left it
   untestable, and it is the step that decides whether an edge is real. */
function projectionOverWindow(breakdown, prop) {
  if (!breakdown || !prop || typeof breakdown.perGame !== "number") return null;
  if (typeof prop.maps !== "number" || prop.maps <= 0) return null;
  return breakdown.perGame * prop.maps;
}

/* ---------- How far wrong is this model, in its own units? -----------------

   A +3 kills edge and a +3 headshots edge are not the same bet and the board
   ranked them as though they were. The model's own error is half again as wide
   on CS2 kills as on CS2 headshots, so +3 clears the line about as often on
   headshots as +4.7 does on kills, and sorting by raw magnitude mixed the two
   units together on one list.

   These are measured, not assumed: the robust spread (1.4826 * MAD, so one
   forty-kill map cannot set the scale) of actual minus point-in-time
   projection, per game, stat and map window, over every scoreable row in the
   committed data. Regenerate with:

     python scripts/dev/hit_probability.py

   MAD rather than a standard deviation because the tail of this distribution
   is exactly where an sd would be fitted to the outliers instead of to the
   body it has to describe.

   Nothing here is a probability. It is a unit, and a unit is all the board
   needs to stop comparing kills against headshots. What a probability would
   additionally need -- that the resulting ranking sorts by OUTCOME -- is
   measured in ParlaysTab and is not currently true. */
const RESIDUAL_SCALE = {
  cs2: {
    kills:     { 1: 4.66, 2: 7.70 },
    deaths:    { 1: 3.27, 2: 5.48 },
    assists:   { 1: 2.51, 2: 3.61 },
    headshots: { 1: 3.13, 2: 4.93 },
  },
  lol: {
    kills:   { 1: 1.84, 2: 2.89, 3: 3.69 },
    deaths:  { 1: 1.94, 2: 2.78, 3: 3.45 },
    assists: { 1: 4.00, 2: 5.96, 3: 7.38 },
  },
  valorant: {
    kills:   { 2: 7.49 },
    deaths:  { 2: 5.17 },
    assists: { 2: 4.31 },
  },
};

/* How the scale grows with the window, for a window never observed.

   Not linear and not its square root. Over LoL kills the measured scale runs
   1.84 / 2.89 / 3.69 for one, two and three maps, where linear would predict
   1.84 / 3.68 / 5.52 and independent maps would give 1.84 / 2.60 / 3.19. Maps
   are positively correlated -- a team winning fast plays short ones -- so the
   truth sits between, at an exponent of 0.63 across the seven game/stat pairs
   where both ends are observed (0.52 to 0.75). Used only to reach a window
   with no measurement of its own: CS2 and Valorant have never had a maps-1-3
   line settled, and LoL's are measured directly. */
const RESIDUAL_SCALE_EXPONENT = 0.63;

function residualScale(game, statType, maps) {
  const byWindow = (RESIDUAL_SCALE[game] || {})[statType];
  if (!byWindow || typeof maps !== "number" || maps <= 0) return null;
  const exact = byWindow[maps];
  if (typeof exact === "number") return exact;
  // The nearest measured window, stretched. Nearest rather than always the
  // one-map anchor, because two maps is what almost everything is measured at.
  const windows = Object.keys(byWindow).map(Number).filter((w) => w > 0);
  if (!windows.length) return null;
  const near = windows.reduce((a, b) => (Math.abs(b - maps) < Math.abs(a - maps) ? b : a));
  return byWindow[near] * Math.pow(maps / near, RESIDUAL_SCALE_EXPONENT);
}

/* The edge in units of the model's own error.

   adjustedEdge first, because the evidence shrink and the unit conversion
   answer different questions and the board wants both: how much of this
   disagreement should we believe (evidence), and how big is it compared with
   how wrong we usually are (scale). Falls back to the raw edge where no scale
   has been measured, so an unmeasured game still ranks rather than vanishing. */
function standardisedEdge(row) {
  if (!row) return null;
  const edge = row.adjustedEdge !== null && row.adjustedEdge !== undefined
    ? row.adjustedEdge : row.edge;
  if (typeof edge !== "number") return null;
  const scale = residualScale(row.game || (row.prop && row.prop.game), row.statType, row.maps);
  return scale ? edge / scale : edge;
}

/* ---------- Combining legs into a parlay -----------------------------------

   Two legs on one match are not two independent bets. They share the map's
   rounds, its pace and how long it ran, and the graded record says so:

     two legs in the SAME match land the same way    55.2%  (12,578 pairs)
     two legs in DIFFERENT matches                   50.0%  (200,000 sampled)
     independence would predict                      50.0%

   and the per-match spread of outcomes runs 3.1x the variance independence
   implies. Multiplying leg probabilities together, which is what a parlay
   calculator normally does, therefore understates a same-match parlay's
   chance of winning AND its chance of losing outright -- it throws away the
   part where the whole map goes one way and takes every leg with it.

   For two-outcome legs at about even money, P(agree) = 1 - 2p(1-p)(1 - rho),
   so 55.2% is rho = 0.10. Measured, on the rows this app has graded.

   The combining rule is the standard one-factor model: each leg clears if a
   latent normal beats its own threshold, and within a match the latents share
   a common factor with loading sqrt(rho). Independent legs fall out of the
   same formula at rho = 0, where it reduces exactly to the product -- so
   there is one code path and not two. */
const SAME_MATCH_CORRELATION = 0.104;

// Simpson's rule over the shared factor. Fixed grid rather than an adaptive
// one so the Python port in scripts/dev/parlay_math.py produces the same
// number to the last place and tests/parlay_parity.test.mjs can say so.
const FACTOR_INTEGRATION_LIMIT = 8;     // standard deviations either side
const FACTOR_INTEGRATION_STEPS = 400;   // even, for Simpson

function standardNormalPdf(z) {
  return Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
}

function standardNormalCdf(z) {
  // Abramowitz & Stegun 7.1.26 via erf is not available in JS, so this is the
  // Zelen & Severo rational approximation, |error| < 7.5e-8 -- three orders
  // finer than any probability this displays.
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
    + t * (-1.821255978 + t * 1.330274429))));
  const upper = standardNormalPdf(z) * poly;
  return z >= 0 ? 1 - upper : upper;
}

/* The inverse, by Acklam's rational approximation (|relative error| < 1.15e-9).
   Needed to turn a leg's probability into the threshold its latent has to
   clear, which is the only way the shared factor can be applied to it. */
const ACKLAM_A = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
                  1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
const ACKLAM_B = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
                  6.680131188771972e+01, -1.328068155288572e+01];
const ACKLAM_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
                  -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
const ACKLAM_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
                  3.754408661907416e+00];

function standardNormalQuantile(p) {
  if (!(p > 0 && p < 1)) return p <= 0 ? -Infinity : Infinity;
  const low = 0.02425;
  if (p < low) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((ACKLAM_C[0] * q + ACKLAM_C[1]) * q + ACKLAM_C[2]) * q + ACKLAM_C[3]) * q
      + ACKLAM_C[4]) * q + ACKLAM_C[5])
      / ((((ACKLAM_D[0] * q + ACKLAM_D[1]) * q + ACKLAM_D[2]) * q + ACKLAM_D[3]) * q + 1);
  }
  if (p > 1 - low) return -standardNormalQuantile(1 - p);
  const q = p - 0.5, r = q * q;
  return (((((ACKLAM_A[0] * r + ACKLAM_A[1]) * r + ACKLAM_A[2]) * r + ACKLAM_A[3]) * r
    + ACKLAM_A[4]) * r + ACKLAM_A[5]) * q
    / (((((ACKLAM_B[0] * r + ACKLAM_B[1]) * r + ACKLAM_B[2]) * r + ACKLAM_B[3]) * r
    + ACKLAM_B[4]) * r + 1);
}

/* P(every leg in one match lands), given each leg's own probability.

   At rho = 0 this is the product. Above it, the shared factor makes the group
   more likely to go all-one-way than independence would have it -- which is
   the behaviour the 55.2% figure describes. */
function groupHitProbability(probabilities, rho) {
  const ps = probabilities.filter((p) => typeof p === "number" && p > 0 && p < 1);
  if (ps.length !== probabilities.length) return null;
  if (!ps.length) return 1;
  if (!rho) return ps.reduce((a, b) => a * b, 1);
  if (ps.length === 1) return ps[0];

  const thresholds = ps.map((p) => standardNormalQuantile(1 - p));
  const root = Math.sqrt(rho), rest = Math.sqrt(1 - rho);
  const lo = -FACTOR_INTEGRATION_LIMIT, hi = FACTOR_INTEGRATION_LIMIT;
  const h = (hi - lo) / FACTOR_INTEGRATION_STEPS;
  const at = (z) => {
    let product = 1;
    for (const t of thresholds) product *= 1 - standardNormalCdf((t - root * z) / rest);
    return standardNormalPdf(z) * product;
  };
  let total = at(lo) + at(hi);
  for (let i = 1; i < FACTOR_INTEGRATION_STEPS; i++) {
    total += at(lo + i * h) * (i % 2 ? 4 : 2);
  }
  return Math.min(1, Math.max(0, (h / 3) * total));
}

/* P(the whole parlay lands). Legs group by match; groups are independent,
   which the 50.0% cross-match figure is the measurement of. */
function jointHitProbability(legs, rho = SAME_MATCH_CORRELATION) {
  if (!legs || !legs.length) return null;
  const byMatch = new Map();
  for (const leg of legs) {
    // Anything without a match key is its own group, which is the
    // conservative reading: it cannot be assumed to share a map with another.
    const key = leg.matchKey || `__${byMatch.size}`;
    if (!byMatch.has(key)) byMatch.set(key, []);
    byMatch.get(key).push(leg.p);
  }
  let joint = 1;
  for (const probabilities of byMatch.values()) {
    const group = groupHitProbability(probabilities, rho);
    if (group === null) return null;
    joint *= group;
  }
  return joint;
}

/* ---------- What the board pays ------------------------------------------

   props.json carries no price. The provider posts a line and an odds_type and
   nothing about the payout, so these are the published PrizePicks Power Play
   multipliers and they are NOT measured from anything this app fetches. They
   move, they differ by entry type, and they differ by jurisdiction.

   TREAT THEM AS A DEFAULT TO CHECK, not as a fact. The number beside them
   that IS a fact is the break-even below, which is arithmetic on whatever
   multiplier is in force: a payout of M over n legs needs each leg to land
   (1/M)^(1/n) of the time before the bet is worth making. That is the figure
   worth reading, and it does not depend on this table being current -- put
   your own board's multiplier in and it recomputes. */
const PAYOUT_MULTIPLIERS = { 2: 3, 3: 5, 4: 10, 5: 20, 6: 37.5 };

function breakEvenPerLeg(multiplier, legs) {
  if (!multiplier || multiplier <= 1 || !legs || legs < 1) return null;
  return Math.pow(1 / multiplier, 1 / legs);
}

/* Expected value per unit staked: pays M when every leg lands, nothing
   otherwise. Positive means the bet is worth making, and at the hit rates
   this app has actually measured nothing here is. */
function parlayExpectedValue(joint, multiplier) {
  if (typeof joint !== "number" || !multiplier) return null;
  return joint * multiplier - 1;
}

/* ---------- Is any of this worth showing yet? ------------------------------

   The parlay view is built and its probabilities are WITHHELD, on purpose, and
   this is the function that decides when they stop being.

   A parlay's chance of landing is the product (adjusted for correlation) of
   its legs' chances, so it inherits everything wrong with a leg's chance. And
   measured on 1,194 graded props the model's confidence does not sort by
   outcome at all: deciles of its own confidence realise 44.5% to 58.0% with
   every interval straddling 50%, the top fifth beats the bottom fifth by 0.6
   points (z = +0.14), and the Brier score is 0.2589 against the 0.25 you get
   by saying "50%" to everything. The 70-80% band realised 39.5%.

   Labelling a parlay "safe" off that would be worse than not building one. The
   tier with the best label measured the worst.

   So the test is the one rankingIsInformative already applies to the record:
   the top band's clustered interval has to clear the bottom band's point
   estimate, on at least 30 rows a side, clustered on the MATCH because ten
   props off one map are not ten observations. Applied here to the quantity the
   ladder actually ranks by -- the standardised edge -- rather than to raw
   kills.

   It clears itself. No code change, no flag to remember: the day the record
   separates, the numbers appear. */
const PARLAY_TIER_THRESHOLD = 0.5;   // standardised edge; about half the model's own error
const PARLAY_MIN_ROWS_PER_BAND = 30;

function parlayEvidence(recordRows) {
  const decided = (recordRows || []).filter(
    (r) => r.result !== "push" && typeof r.edge === "number" && typeof r.won === "boolean");
  const withScale = decided.map((r) => ({ ...r, z: Math.abs(standardisedEdge(r)) }))
                           .filter((r) => typeof r.z === "number" && isFinite(r.z));
  const big = withScale.filter((r) => r.z >= PARLAY_TIER_THRESHOLD);
  const small = withScale.filter((r) => r.z < PARLAY_TIER_THRESHOLD);
  const rate = (set) => clusteredMean(
    groupByMatch(set).map((m) => m.filter((r) => r.won).length / m.length));
  const overall = rate(withScale);

  if (big.length < PARLAY_MIN_ROWS_PER_BAND || small.length < PARLAY_MIN_ROWS_PER_BAND) {
    return {
      validated: false, overall, big: null, small: null,
      reason: `only ${big.length} graded prop(s) above the confidence threshold and `
        + `${small.length} below it; ${PARLAY_MIN_ROWS_PER_BAND} a side is the floor`,
    };
  }
  const bigRate = rate(big), smallRate = rate(small);
  if (!bigRate || !smallRate) {
    return { validated: false, overall, big: bigRate, small: smallRate,
             reason: "not enough distinct matches to cluster on" };
  }
  const validated = bigRate.lo > smallRate.mean;
  return {
    validated, overall, big: bigRate, small: smallRate,
    reason: validated
      ? `the more confident half realises ${(bigRate.mean * 100).toFixed(1)}% `
        + `(interval from ${(bigRate.lo * 100).toFixed(1)}%), clear of the less confident `
        + `half's ${(smallRate.mean * 100).toFixed(1)}%`
      : `the more confident half realises ${(bigRate.mean * 100).toFixed(1)}% `
        + `(${(bigRate.lo * 100).toFixed(1)}-${(bigRate.hi * 100).toFixed(1)}%) against the less `
        + `confident half's ${(smallRate.mean * 100).toFixed(1)}% — ordered, but not separated`,
  };
}

/* ---------- The ladder ----------------------------------------------------

   Safe to dangerous is entry SIZE: more legs pays more and lands less, and
   that is the axis a board actually offers. Each rung takes the
   highest-confidence legs available, and:

   - never two legs on one player. Two lines on the same player in the same
     match are near the same bet, and a provider will not pair them anyway.
   - prefers legs from DIFFERENT matches, because same-match legs are
     correlated (rho 0.10 measured) and a rung built inside one map is a bet on
     that map rather than on five reads. Where the board cannot supply enough
     matches the rung says so instead of quietly stacking one fixture.
   - refuses a rung it cannot fill, rather than padding it with the next
     unranked thing on the list. */
function buildParlays(rows, { sizes = [2, 3, 4, 5, 6], multipliers = PAYOUT_MULTIPLIERS,
                              correlation = SAME_MATCH_CORRELATION } = {}) {
  const usable = (rows || []).filter(
    (r) => typeof r.edge === "number" && r.prop && typeof standardisedEdge(r) === "number");
  const ranked = [...usable].sort(
    (a, b) => Math.abs(standardisedEdge(b)) - Math.abs(standardisedEdge(a)));

  const out = [];
  for (const size of sizes) {
    const legs = [];
    const takenPlayers = new Set();
    const takenMatches = new Set();
    // First pass takes one leg per match, which is the shape worth having.
    for (const row of ranked) {
      if (legs.length >= size) break;
      const matchKey = parlayMatchKey(row);
      if (takenPlayers.has(row.name) || takenMatches.has(matchKey)) continue;
      legs.push(row);
      takenPlayers.add(row.name);
      takenMatches.add(matchKey);
    }
    let spread = legs.length;
    // Second pass fills from matches already used, still one leg per player.
    for (const row of ranked) {
      if (legs.length >= size) break;
      if (takenPlayers.has(row.name)) continue;
      legs.push(row);
      takenPlayers.add(row.name);
    }
    if (legs.length < size) continue;

    const multiplier = multipliers[size] || null;
    const legInputs = legs.map((row) => ({ p: null, matchKey: parlayMatchKey(row) }));
    const matches = new Set(legInputs.map((l) => l.matchKey)).size;
    out.push({
      size, legs, multiplier,
      breakEven: breakEvenPerLeg(multiplier, size),
      matches,
      sharesAMatch: matches < size,
      // Filled in by the view only when parlayEvidence says the numbers may
      // be shown; the ladder itself is computable without them and is what
      // makes the view useful while they are withheld.
      legInputs,
      spread,
      weakestLeg: Math.min(...legs.map((r) => Math.abs(standardisedEdge(r)))),
    });
  }
  return out;
}

/* One key per fixture, so two legs on the same match group together whichever
   side they are on. Same construction as groupByMatch uses on the record. */
function parlayMatchKey(row) {
  const pair = [row.team, row.opponent].map((t) => String(t || "")).sort().join(" vs ");
  return `${row.game || ""}|${String(row.when || "").slice(0, 16)}|${pair}`;
}

/* The parlay's chance of landing, given a per-leg probability for each leg.
   Separate from buildParlays because the ladder is shown either way and this
   is the part that waits for evidence. */
function parlayProbability(rung, probabilityOf, correlation = SAME_MATCH_CORRELATION) {
  if (!rung || !rung.legs) return null;
  const legs = rung.legs.map((row) => ({ p: probabilityOf(row), matchKey: parlayMatchKey(row) }));
  if (legs.some((l) => typeof l.p !== "number")) return null;
  return jointHitProbability(legs, correlation);
}

/* ---------- Fixtures the posted board implies ------------------------------

   The read-time half of scripts/board_fixtures.py, and the reason there are
   two halves at all: the pipeline runs twice a day, the board is refreshed
   every half hour, and a line posted at noon for a 16:00 game would have
   waited for the 21:00 scrape to become visible — which is to say it would
   have been invisible for the only hours it mattered.

   So the same rule runs here, over whatever board the page just fetched.
   The two are held identical by tests/board_parity.test.mjs, which runs both
   against the committed data files and a set of synthetic shapes and fails on
   any disagreement. Change one and the other has to follow; the docstring in
   board_fixtures.py is the reasoning, not repeated here.

   Both are idempotent, which is what makes running both safe: an inferred
   `X vs TBD` already in the data marks X as having a fixture, so the second
   pass finds nothing to add.                                            */

// Mirrors UPCOMING_MAX_AGE_HOURS in scrape_cs2.py: a match already under way
// is still worth showing, so a kickoff is "past" only well after it passed.
const BOARD_MAX_AGE_HOURS = 12;
// Mirrors MAX_BOARD_AGE_DAYS in board_fixtures.py. Far longer than
// PROPS_MAX_AGE_MINUTES, which governs whether a LINE is worth comparing
// against; a kickoff stays evidence about the fixture list long after the
// number beside it has gone stale.
const MAX_BOARD_AGE_DAYS = 3;
const PLACEHOLDER_TEAMS = new Set(["", "tbd", "?"]);

function isPlaceholderTeam(name) {
  return PLACEHOLDER_TEAMS.has(String(name === null || name === undefined ? "" : name).trim().toLowerCase());
}

/* new Date(), narrowed to what Python's fromisoformat would also accept.

   Not a nicety: `new Date("Sep 21")` is a VALID date in 2001, so the bare
   permissive parse turns a display string into a timestamp eight hundred
   years out instead of announcing itself — the same trap formatUpcoming's
   _sortKey exists to avoid. A naive stamp is read as UTC here because that
   is what the Python side does with it; left to the platform it would be
   read as the viewer's local time and the two would disagree by the
   viewer's offset. */
const ISO_STAMP = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

function parseStamp(raw) {
  if (typeof raw !== "string" || !raw.trim()) return null;
  const text = raw.trim();
  if (!ISO_STAMP.test(text)) return null;
  const normalized = text.replace(" ", "T");
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(normalized) || !normalized.includes("T")
    ? normalized : normalized + "Z";
  const at = new Date(zoned);
  return isNaN(at.getTime()) ? null : at;
}

/* Lowercased player handle -> the roster teams carrying it. A Set, not a
   name: 232 of CS2's 1396 rostered handles are on more than one team. */
function boardTeamIndex(regionData) {
  const index = new Map();
  const teams = (regionData && regionData.teams) || {};
  for (const teamName of Object.keys(teams)) {
    for (const player of (teams[teamName] && teams[teamName].players) || []) {
      const name = player && player.name;
      if (typeof name !== "string" || !name.trim()) continue;
      const key = name.trim().toLowerCase();
      if (!index.has(key)) index.set(key, new Set());
      index.get(key).add(teamName);
    }
  }
  return index;
}

/* The roster team a board row belongs to, or null. Keyed on the PLAYER —
   see resolve_team in board_fixtures.py for why, and why the row's own team
   name is only the tiebreak, matched on equality rather than containment. */
function resolveBoardTeam(row, index) {
  const handle = String((row && row.player) || "").trim().toLowerCase();
  if (!handle) return null;
  const candidates = index.get(handle);
  if (!candidates || candidates.size === 0) return null;
  if (candidates.size === 1) return [...candidates][0];
  const stated = String((row && row.team) || "").trim().toLowerCase();
  const exact = [...candidates].filter((team) => team.toLowerCase() === stated);
  return exact.length === 1 ? exact[0] : null;
}

/* The kickoffs this board implies, per region. Walks the board once and
   assigns each row to the regions it could belong to. */
function boardSlots(regionsData, propsData, game, now) {
  const regions = regionsData || {};
  const regionKeys = Object.keys(regions);
  const byRegion = new Map(regionKeys.map((key) => [key, new Map()]));
  const forGame = (propsData && propsData.props && propsData.props[game]) || null;
  if (!forGame) return byRegion;

  const captured = parseStamp(propsData && propsData.fetched_at);
  if (captured && (now - captured) / 86400000 > MAX_BOARD_AGE_DAYS) return byRegion;

  const indexes = new Map(regionKeys.map((key) => [key, boardTeamIndex(regions[key])]));
  const known = new Set(regionKeys.map((key) => String(key).trim().toLowerCase()));
  const horizon = now.getTime() - BOARD_MAX_AGE_HOURS * 3600000;

  for (const rows of Object.values(forGame)) {
    for (const row of rows || []) {
      // A row's stated league scopes it only where this game HAS a region by
      // that name. Valorant labels its lines with the players' home region
      // while the fixture is filed under 'VCT Champions'.
      const stated = String((row && row.region) || "").trim().toLowerCase();
      const when = parseStamp(row && row.start_time);
      if (when === null || when.getTime() < horizon) continue;
      for (const key of regionKeys) {
        if (known.has(stated) && String(key).trim().toLowerCase() !== stated) continue;
        const team = resolveBoardTeam(row, indexes.get(key));
        if (team === null) continue;
        const slots = byRegion.get(key);
        const stamp = when.getTime();
        if (!slots.has(stamp)) slots.set(stamp, new Set());
        slots.get(stamp).add(team);
      }
    }
  }
  return byRegion;
}

/* Is this fixture the game the board put at `slotWhen`?

   Mirrors propsFor's asymmetry above, for the same reason: bo3.gg and the
   LoL Esports API state a kickoff, vlr.gg states a bare date, and a bare
   date read as midnight is nine hours from its own board — which called
   every real Valorant fixture missing. */
function fixtureMatchesSlot(fixtureDate, slotWhen, windowMs) {
  const when = parseStamp(fixtureDate);
  if (when === null) return false;
  if (String(fixtureDate).includes(":")) {
    return Math.abs(when.getTime() - slotWhen) <= windowMs;
  }
  return when.toISOString().slice(0, 10) === new Date(slotWhen).toISOString().slice(0, 10);
}

/* regionsData with the board's fixtures folded in.

   Returns the input UNCHANGED, by reference, when there is nothing to add:
   historyPool caches on that identity and a fresh object every render would
   rescan every region's whole season. */
function withBoardFixtures(regionsData, propsData, game, now) {
  const at = now || new Date();
  const slotsByRegion = boardSlots(regionsData, propsData, game, at);
  const windowMs = PROP_MATCH_WINDOW_HOURS * 3600000;

  // Flat (region, slot) list in one deterministic order, so the result does
  // not depend on which region happened to be iterated first.
  const ordered = [];
  for (const [regionKey, slots] of slotsByRegion) {
    for (const [stamp, teams] of slots) ordered.push({ regionKey, stamp, teams });
  }
  if (!ordered.length) return regionsData;
  ordered.sort((a, b) => (a.stamp - b.stamp) || (a.regionKey < b.regionKey ? -1
    : a.regionKey > b.regionKey ? 1 : 0));

  // Coverage is judged across every region, because "does this team already
  // have a fixture then" is a question about the game, not about the bucket
  // the fixture is filed under.
  const lists = new Map();
  const existing = [];
  for (const regionKey of Object.keys(regionsData || {})) {
    const list = ((regionsData[regionKey] || {}).upcoming_matches) || [];
    const copy = list.map((m) => ({ ...m }));
    lists.set(regionKey, copy);
    for (const fixture of copy) existing.push(fixture);
  }

  const sides = (f) => [f.teamA, f.teamB];
  let touched = false;
  for (const { regionKey, stamp, teams } of ordered) {
    const near = existing.filter((f) => fixtureMatchesSlot(f.date, stamp, windowMs));
    const committed = new Set();
    for (const f of near) {
      for (const name of sides(f)) if (!isPlaceholderTeam(name)) committed.add(name);
    }
    // A fixture near this slot with one side named and the other a
    // placeholder. The named side must be one the board also puts here, or
    // this is a different game that merely kicks off nearby.
    const holes = near.filter((f) =>
      sides(f).filter(isPlaceholderTeam).length === 1
      && sides(f).some((n) => !isPlaceholderTeam(n) && teams.has(n)));
    const leftover = [...teams].filter((t) => !committed.has(t)).sort();
    if (!leftover.length) continue;

    if (holes.length) {
      // The leftovers belong to these holes. Fill one only where the board
      // leaves no choice; otherwise leave the holes alone AND add nothing,
      // because a new fixture here would publish the same game twice.
      if (holes.length === 1 && leftover.length === 1) {
        const hole = holes[0];
        hole[isPlaceholderTeam(hole.teamA) ? "teamA" : "teamB"] = leftover[0];
        hole.inferred = "board:opponent";
        touched = true;
      }
      continue;
    }

    // No fixture at all for these teams. One entry each, opponent left
    // undecided: who they play is exactly what the board cannot say.
    const target = lists.get(regionKey);
    if (!target) continue;
    for (const team of leftover) {
      // Trimmed to whole seconds with a Z: the exact text stamp_text() in
      // board_fixtures.py produces, so the two sides are comparable as
      // strings rather than only as instants.
      const fixture = { date: new Date(stamp).toISOString().replace(/\.\d{3}Z$/, "Z"), teamA: team,
                        teamB: "TBD", block: "", inferred: "board:team" };
      target.push(fixture);
      existing.push(fixture);
      touched = true;
    }
  }
  if (!touched) return regionsData;

  const out = {};
  for (const regionKey of Object.keys(regionsData)) {
    out[regionKey] = { ...regionsData[regionKey], upcoming_matches: lists.get(regionKey) };
  }
  return out;
}

/* Every posted line for this game, with the projection that belongs beside
   it, across every region at once.

   The match cards are the wrong shape for the question this app exists to
   answer. A real board had 48 CS2 fixtures of which 12 rendered and 5 held
   a line, so finding the bets meant opening cards one at a time and mostly
   finding nothing. The lines are the scarce thing, not the fixtures, so
   this lists those instead and lets the fixtures follow from them.

   Ranked by the SIZE of the edge, not its sign: a line four kills below the
   projection and one four above are equally interesting, they just point in
   opposite directions, and burying the unders at the bottom of a list would
   hide half the board. */
/* The matches a region's players actually have on record, which is not
   always the same list as the matches that region has played.

   An event like VCT Champions arrives before it starts: fixtures and
   rosters, zero completed matches. The rosters are already borrowed from
   the teams' home regions (scrape_valorant.py's
   lend_rosters_to_eventless_regions, which sets rosters_from_home_regions),
   so the players are real and their history is real — it is just filed
   one region over. Without it every Champions player rendered with no
   form chart, no consistency score, and a projection that fell back to a
   flat season average because recencyWeightedRate found nothing to weight.

   Borrowing is deliberately narrow, because the failure mode on the other
   side is silently double-counting a league:

     - only when the region declares rosters_from_home_regions, and
     - only when it has no completed matches of its own. The moment the
       event plays its first match this returns the region's own list and
       the borrowed history drops out, rather than the two being mixed.

   Matches are keyed on date + teams so a meeting between two teams that
   share a home region cannot arrive twice.

   This is history, not results. Standings and the Past Results tab keep
   reading the region's own past_matches, because "what has happened at
   Champions" is genuinely nothing yet and showing four other leagues
   under that heading would be a lie rather than a gap. */
function historyPoolFor(regionsData, regionKey) {
  const rd = regionsData && regionsData[regionKey];
  if (!rd) return [];
  /* `own` is everything this region's players have on record: the
     current event's matches plus the ones carried over from before it.
     The two are stored apart because past_matches answers "what has
     happened at THIS event" for standings and the Past Results tab,
     where a previous split's games would be wrong. For a projection the
     distinction does not exist -- a map a player played is a map they
     played, and the scraper used to throw the older ones away, capping
     every Valorant player at a median of 8 maps and resetting them to
     zero the day an event rolled over. */
  const carried = rd.history_matches || [];
  const own = carried.length ? (rd.past_matches || []).concat(carried)
                             : (rd.past_matches || []);

  /* Which teams here are borrowed, rather than whether the region is.
     Both this and the scraper's lending used to be all-or-nothing on the
     region, and both collapsed together the moment the event played its
     first map: Champions had one match, so two teams counted as "the
     event has started" and the other fourteen — every one of them with a
     real season one region over — lost their history in the same tick
     they lost their roster. An event fills up one match at a time. */
  const borrowedTeams = new Set();
  for (const [name, t] of Object.entries(rd.teams || {})) {
    if (t && t.from_home_region) borrowedTeams.add(name);
  }
  /* Files written before the scraper marked provenance per team carry
     only the region-wide flag, which meant every roster or none. */
  if (borrowedTeams.size === 0) {
    if ((rd.past_matches || []).length > 0 || !rd.rosters_from_home_regions) return own;
    for (const name of Object.keys(rd.teams || {})) borrowedTeams.add(name);
  }
  if (borrowedTeams.size === 0) return own;

  /* The region's OWN matches stay in. A borrowed team cannot appear in
     them — it would have a roster of its own if it had played here — so
     mixing them is not the double-count the old all-or-nothing rule was
     guarding against; it is a board where the teams that have played at
     the event use that, and the teams that have not use their season. */
  const seen = new Set();
  const pool = [];
  const add = (m) => {
    const id = `${m.date || ""}|${m.teamA}|${m.teamB}`;
    if (seen.has(id)) return;
    seen.add(id);
    pool.push(m);
  };
  for (const m of own) add(m);
  for (const [key, other] of Object.entries(regionsData)) {
    if (key === regionKey) continue;
    for (const m of (other && other.past_matches) || []) {
      if (!borrowedTeams.has(m.teamA) && !borrowedTeams.has(m.teamB)) continue;
      add(m);
    }
  }
  return pool;
}

/* Recomputing the pool per player per render would rescan every region's
   whole season each time. Keyed on the regionsData object, which is
   replaced wholesale when a fetch lands, so a stale entry cannot outlive
   the data it was built from. */
const historyPoolCache = new WeakMap();
function historyPool(regionsData, regionKey) {
  if (!regionsData || typeof regionsData !== "object") return [];
  let byRegion = historyPoolCache.get(regionsData);
  if (!byRegion) { byRegion = new Map(); historyPoolCache.set(regionsData, byRegion); }
  if (!byRegion.has(regionKey)) byRegion.set(regionKey, historyPoolFor(regionsData, regionKey));
  return byRegion.get(regionKey);
}

function collectEdges(regionsData, regionList, propsData, weights, statType, game) {
  const rows = [];
  for (const regionKey of regionList || []) {
    const rd = regionsData && regionsData[regionKey];
    if (!rd || !rd.teams) continue;
    // The pool, not rd.past_matches: a borrowed-roster region would
    // otherwise project every player off a flat season average here while
    // the match card beside it used their real history, and two different
    // numbers for one player is worse than either number alone.
    const pastMatches = historyPool(regionsData, regionKey);
    // Computed once per region, not per row: it is the same figure for
    // every player in it, and leaguePacePerMap walks the whole history.
    const leagueRate = leaguePlayerRate(pastMatches, rd.teams, STAT_TYPES[statType].key, null);
    for (const match of rd.upcoming_matches || []) {
      // Only the player's OWN team has to be rostered. The opponent is
      // needed for one term, which now falls back to neutral, and a line
      // is worth showing with that caveat rather than being dropped: a
      // real CS2 board stranded five of them this way, because its roster
      // tracks fifty teams against a hundred in the fixture list.
      if (!rd.teams[match.teamA] && !rd.teams[match.teamB]) continue;
      // _sortKey first, for the same reason the match card needs it: `date`
      // may already have been replaced by a display string.
      const when = match._sortKey || match.date;
      for (const team of [match.teamA, match.teamB]) {
        if (!rd.teams[team]) continue;
        const opponent = team === match.teamA ? match.teamB : match.teamA;
        // Per team, not per region: at an event that has started, some
        // rosters here are its own and some are their home region's.
        const borrowed = historyIsBorrowed(rd, team);
        const oppKnown = !!rd.teams[opponent];
        for (const player of likelyStarters(rd.teams[team].players || [])) {
          // One row per POSTED WINDOW, not one per player. A map-1 line
          // and a maps-1-2 line on the same fixture are two markets with
          // two lines and two edges, and collapsing them to one row
          // meant whichever window the provider happened to list second
          // never reached the board at all.
          for (const prop of propsFor(propsData, game, player.name, statType, when)) {
            // Projected over the LINE's window, which is the fixture's, and
            // not over whatever the games-in-series control happens to show.
            const breakdown = project(rd.teams, pastMatches, player, team,
                                      opponent, prop.maps, weights, statType);
            const projection = projectionOverWindow(breakdown, prop);
            if (projection === null) continue;
            rows.push({
              region: regionKey, name: player.name, role: player.role,
              team, opponent, when, prop, projection, breakdown, oppKnown,
              // Carried so a row can be put in units of the model's own error
              // (residualScale) without the caller's arguments coming with it.
              // The parlay builder mixes rows from every game on one list and
              // has nothing else to ask.
              game, statType,
              // The window this row is about, lifted out of the prop so the
              // board can section on it without reaching back through.
              maps: prop.maps,
              // Carried so a row can expand into the same ProjectionDetail
              // the match cards use. The alternative was rebuilding the
              // projection inside the row, which would have been a second
              // call site free to drift from this one -- and the detail
              // panel's whole job is to explain THIS number.
              player, pastMatches,
              // The raw count stays on the breakdown, because it is a true
              // statement about how many maps were read. This is the count
              // that should be TRUSTED, which is a different question.
              borrowedContext: borrowed,
              evidence: effectiveEvidence(breakdown.evidenceGames, borrowed),
              // No edge where the provider posted several lines and named
              // none of them the market one — same refusal as the readout.
              edge: prop.lineCount > 1 ? null : projection - prop.line,
              // What the board ranks on. The raw edge is kept beside it
              // because it is the number anyone can recompute from the two
              // printed above it, and a ranking that cannot be checked
              // against them is worth less than one that can.
              adjustedEdge: prop.lineCount > 1 ? null
                : adjustEdge(projection - prop.line,
                             effectiveEvidence(breakdown.evidenceGames, borrowed),
                             projection, prop.line,
                             // The league rate over this line's own window,
                             // which is what "a typical player" means here.
                             leagueRate === null ? null : leagueRate * prop.maps),
            });
          }
        }
      }
    }
  }
  return rankEdges(rows);
}

/* Ranked by the edge that survives its own evidence, not the raw one.

   A +6 off three games and a +4 off forty are not the same bet, and the
   raw sort put the first above the second every time -- which is to say
   it promoted the rows the model was least sure of, precisely because
   being unsure produces bigger disagreements. */
function rankEdges(rows) {
  /* In units of the model's own error, not raw kills.
     The raw sort put a +3 CS2 kills edge above a +2.8 headshots one, and the
     model's error is 7.7 wide on the first and 4.9 on the second -- so the
     second is the larger disagreement by the only measure that compares them.
     A whole column of the board was sorted by which stat happened to have the
     bigger numbers. */
  const key = (r) => standardisedEdge(r);
  return [...rows].sort((a, b) => {
    const ka = key(a), kb = key(b);
    if ((ka === null) !== (kb === null)) return ka === null ? 1 : -1;
    if (ka === null) return 0;
    return Math.abs(kb) - Math.abs(ka);
  });
}

/* The only claim that matters commercially: when the projection disagreed
   with the line, which one was right.

   The grading — which match a line belonged to, what the player did over
   exactly its maps, whether it landed over — is done once, in Python, and
   arrives already decided in props_results.json. What only this side can
   supply is the projection as it stood BEFORE the match, which is why this
   lives here: projectPointInTime rebuilds it from data that predated the
   fixture, exactly as the backtest headline does.

   A projection sitting exactly on the line is not a disagreement and is
   dropped, as is a push. Neither is a bet, and counting either would pad
   the sample with outcomes nobody could have acted on. */
function modelRecord(regionsData, regionList, results, weights, statType) {
  const rows = [];
  for (const row of (results && results.graded) || []) {
    if (row.stat !== statType) continue;

    let teams = null, pastMatches = null;
    for (const key of regionList || []) {
      const rd = regionsData && regionsData[key];
      if (rd && rd.teams && rd.teams[row.team]) {
        teams = rd.teams;
        pastMatches = historyPool(regionsData, key);  // same pool the Edges tab projects from
        break;
      }
    }
    if (!teams) continue;
    const player = (teams[row.team].players || []).find((p) => p.name === row.player);
    if (!player) continue;   // rosters move; a departed player cannot be re-projected

    const breakdown = projectPointInTime(pastMatches, teams, player, row.team,
                                         row.opponent, row.maps, weights,
                                         row.match_date, statType, null);
    if (!breakdown || typeof breakdown.perGame !== "number") continue;
    const projection = breakdown.perGame * row.maps;
    const side = projection > row.line ? "over" : projection < row.line ? "under" : null;
    if (side === null || row.result === "push") continue;

    rows.push({
      ...row, projection, side,
      won: side === row.result,
      edge: Math.round((projection - row.line) * 100) / 100,
    });
  }
  return rows;
}

/* The same record, split by the market each bet was made in.

   A map-1 bet and a maps-1-2 bet are priced separately and settled
   separately, and there is no reason the model should be equally good at
   both: a per-map rate scaled to a single map rests entirely on that
   rate, while the same rate over two maps has a map's worth of variance
   averaged out of it. Pooling the windows lets a strong record on one
   carry a losing record on the other, which is precisely the claim this
   screen exists to stop anyone making. */
function recordByWindow(rows) {
  const byWindow = new Map();
  for (const r of rows) {
    const maps = typeof r.maps === "number" ? r.maps : -1;
    if (!byWindow.has(maps)) byWindow.set(maps, []);
    byWindow.get(maps).push(r);
  }
  return [...byWindow.keys()].sort((a, b) => a - b).map((maps) => {
    const inWindow = byWindow.get(maps);
    const won = inWindow.filter((r) => r.won).length;
    return { maps, n: inWindow.length, won,
             rate: inWindow.length ? won / inWindow.length : null };
  });
}

/* ============================================================
   THE RECORD, CLUSTERED. Props inside one match are not
   independent observations.

   Ten props from one CS2 map share its rounds, its pace and how
   one-sided it was. Treating them as ten independent rows makes every
   interval about three times too narrow, which is how a 43.7% win rate
   over EIGHT matches once got reported here as if it meant something.
   The match is the unit: each contributes one number, and the spread
   ACROSS matches is what the interval is built from.
   ============================================================ */

function groupByMatch(rows) {
  const out = new Map();
  for (const r of rows) {
    // The PAIR, sorted -- not this row's team. Both sides of a map
    // share its rounds and its pace, so they are one dependent unit,
    // and keying on `team` alone splits every match into two clusters.
    // That is the same under-counting this whole function exists to
    // prevent, just one level in: it read 75 matches where there were
    // 49, and every interval came out too narrow again. Caught by
    // cross-checking against scripts/dev/model_vs_market.mjs, which is
    // why the two are compared in the tests rather than trusted to
    // agree.
    const pair = [r.team, r.opponent].slice().sort().join("|");
    const key = `${r.game}|${r.match_date}|${pair}`;
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(r);
  }
  return [...out.values()];
}

/* Mean of per-match values with a 95% interval. Returns null below two
   matches, where a spread cannot be computed at all -- and a single
   match reported with no interval is exactly the overclaim this file
   exists to stop. */
function clusteredMean(perMatch) {
  const n = perMatch.length;
  if (n < 2) return null;
  const mean = perMatch.reduce((a, b) => a + b, 0) / n;
  const variance = perMatch.reduce((s, x) => s + (x - mean) ** 2, 0) / (n - 1);
  const se = Math.sqrt(variance / n);
  return { mean, lo: mean - 1.96 * se, hi: mean + 1.96 * se, n };
}

/* Win rate and accuracy against the posted line, both clustered.

   The accuracy figure is the one that decides whether any of this is
   worth paying for, and it is deliberately signed so that POSITIVE
   means the line was closer. There is no version of this that flatters
   the model by accident. */
function recordVsLine(rows) {
  const decided = rows.filter((r) => r.result !== "push" && typeof r.actual === "number"
                                     && typeof r.line === "number");
  const matches = groupByMatch(decided);
  const winRate = clusteredMean(
    matches.map((m) => m.filter((r) => r.won).length / m.length));
  const maeGap = clusteredMean(matches.map((m) => {
    const ours = m.reduce((s, r) => s + Math.abs(r.projection - r.actual), 0) / m.length;
    const line = m.reduce((s, r) => s + Math.abs(r.line - r.actual), 0) / m.length;
    return ours - line;
  }));
  return { winRate, maeGap, matches: matches.length, props: decided.length };
}

/* Is the model's number centred, or systematically high or low?

   MAE cannot tell you: a model 8% low on every row and one off by 8% in
   random directions score identically. Next to a posted line a constant
   offset turns into a constant "under" on every player, which looks
   like a signal and is a ruler with the wrong zero. */
function calibration(rows) {
  const usable = rows.filter((r) => typeof r.actual === "number");
  if (!usable.length) return null;
  const errors = usable.map((r) => r.projection - r.actual);
  const mean = errors.reduce((a, b) => a + b, 0) / errors.length;
  const closer = usable.filter((r) => typeof r.line === "number"
    && Math.abs(r.projection - r.actual) < Math.abs(r.line - r.actual)).length;
  const withLine = usable.filter((r) => typeof r.line === "number").length;
  return { mean, n: usable.length, closer, withLine };
}

/* Does a bigger disagreement win more often? If the model is worth
   anything that curve slopes upward, and if it does not, a confident edge
   is worth no more than a marginal one — which is the single most useful
   thing this whole record can tell anyone. */
const EDGE_BUCKETS = [[0, 1], [1, 2], [2, 3], [3, Infinity]];

function recordByEdge(rows) {
  return EDGE_BUCKETS.map(([lo, hi]) => {
    const inBucket = rows.filter((r) => {
      const size = Math.abs(r.edge);
      return size >= lo && size < hi;
    });
    const won = inBucket.filter((r) => r.won).length;
    return { lo, hi, n: inBucket.length, won,
             rate: inBucket.length ? won / inBucket.length : null };
  });
}

/* A team's colour, for teams that may not be rostered.

   Fixtures can name a side this app has never scraped — CS2 rosters about
   fifty teams against a hundred in its fixture list — and a card that
   renders such a fixture still has to print both names. Reaching straight
   into `.color` on the missing one throws inside render, which React
   turns into a blank page rather than a missing colour. */
function teamColorOf(teams, name, fallback) {
  const team = teams && teams[name];
  return (team && team.color) || fallback;
}

function propsAgeMinutes(propsData) {
  if (!propsData || !propsData.fetched_at) return null;
  const then = new Date(propsData.fetched_at);
  if (isNaN(then)) return null;
  return (Date.now() - then.getTime()) / 60000;
}

function propsAreFresh(propsData) {
  const age = propsAgeMinutes(propsData);
  return age !== null && age <= PROPS_MAX_AGE_MINUTES;
}

/* Is this line still about a match that has not happened?

   The prop carries the provider's own start_time for the fixture it was
   posted on, which is a better clock than how old the payload is: what
   makes a line worthless is the match being played, not the file being
   fetched a while ago.

   A line with no readable start time stays live. The alternative is
   discarding a usable line over a missing field, and the map window and
   payload age are both still on screen for anyone deciding. */
function propIsLive(prop, now = Date.now()) {
  if (!prop || !prop.start_time) return true;
  const start = new Date(prop.start_time).getTime();
  return isNaN(start) ? true : now < start;
}

/* Minutes, said the way someone reads them. "312m old" is arithmetic
   homework; "5h old" is the same fact. */
function ageLabel(minutes) {
  if (minutes === null || minutes === undefined || isNaN(minutes)) return null;
  const m = Math.round(minutes);
  if (m < 90) return `${m}m old`;
  const h = minutes / 60;
  return h < 48 ? `${Math.round(h)}h old` : `${Math.round(h / 24)}d old`;
}
function useChampionStats() {
  return useContext(ChampionStatsContext);
}

// Live viewport-width detection rather than user-agent sniffing — user
// agents lie, and this also adapts correctly if someone just resizes a
// browser window rather than needing a reload. 860px is roughly where a
// single ~640px mobile column plus a real sidebar stops feeling cramped.
const DESKTOP_BREAKPOINT = 860;
function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(
    typeof window !== "undefined" ? window.innerWidth >= DESKTOP_BREAKPOINT : false
  );
  useEffect(() => {
    const onResize = () => setIsDesktop(window.innerWidth >= DESKTOP_BREAKPOINT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return isDesktop;
}

function loadStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw !== null ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}
function saveStored(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    /* private browsing / storage disabled — silently no-op, not worth surfacing */
  }
}

/* Small reticle mark — the app's one recurring signature graphic. Used in
   the wordmark and as the live-data status dot, nowhere else. */
function Reticle({ size = 16, color, active = false }) {
  const s = size;
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" style={{ flexShrink: 0 }}>
      <circle cx="12" cy="12" r="7" stroke={color} strokeWidth="1.6" opacity={active ? 1 : 0.55} />
      <line x1="12" y1="1" x2="12" y2="6" stroke={color} strokeWidth="1.6" />
      <line x1="12" y1="18" x2="12" y2="23" stroke={color} strokeWidth="1.6" />
      <line x1="1" y1="12" x2="6" y2="12" stroke={color} strokeWidth="1.6" />
      <line x1="18" y1="12" x2="23" y2="12" stroke={color} strokeWidth="1.6" />
      <circle cx="12" cy="12" r="1.6" fill={color} />
    </svg>
  );
}

/* ============================================================
   LIVE DATA SOURCES — one per game. Each points at a raw JSON file
   with the same shape: { generated_at, regions: { REGION_KEY: {
   teams, past_matches, upcoming_matches } } }. Leave a URL blank to
   always use that game's embedded fallback snapshot below.
   ============================================================ */
const DATA_URL_LOL = "https://raw.githubusercontent.com/iiSTORM/New/refs/heads/main/data.json";
const DATA_URL_VALORANT = "https://raw.githubusercontent.com/iiSTORM/New/refs/heads/main/valorant_data.json";
const DATA_URL_CS2 = "https://raw.githubusercontent.com/iiSTORM/New/refs/heads/main/cs2_data.json";
// Champion-level stat baselines (scripts/aggregate_champion_stats.py) —
// LoL-only for now, and not tied to the per-game GAMES fetch loop below
// since it's a standalone reference table, not one game's live snapshot.
const DATA_URL_CHAMPION_STATS = "https://raw.githubusercontent.com/iiSTORM/New/refs/heads/main/champion_stats.json";
const DATA_URL_PROPS = "https://raw.githubusercontent.com/iiSTORM/New/refs/heads/main/props.json";
const DATA_URL_RESULTS = "https://raw.githubusercontent.com/iiSTORM/New/refs/heads/main/props_results.json";

// Lines move continuously and get pulled when news breaks, so an old one
// can be misleading in the expensive direction — it still looks
// actionable. That used to SUPPRESS the edge entirely past this age.
//
// It no longer does. A line is now live until its own fixture starts,
// because that is the question actually being asked: a line posted six
// hours before a match that has not been played is still the line, and
// refusing to price it made the board go blank exactly when it was
// being looked at. Past this age the age is PRINTED beside the window
// instead, so the caveat survives without the edge disappearing.
//
// The judgement that an old line is risky has not changed, only who
// gets to act on it. If this app is ever pointed at anyone else, the
// suppressing behaviour is propIsLive() returning false past this age
// as well — one condition, not a rewrite.
const PROPS_MAX_AGE_MINUTES = 90;

/* ============================================================
   FALLBACK DATA — used if a game's DATA_URL is blank or the fetch
   fails. This is a snapshot as of Aug 17, 2026 (through LCS Week 4).
   Two splits per player: "cur" (current split) and "hist" (prior).
   ============================================================ */

const FALLBACK_TEAMS = {
  LYON: {
    color: "#e0c341",
    players: [
      { name: "Dhokla", role: "TOP", cur: { g: 7, k: 3.4, d: 2.7, a: 5.3, kp: 43.4 }, hist: { g: 15, k: 2.9, d: 2.4, a: 6.3, kp: 56.5 } },
      { name: "Armao", role: "JNG", cur: { g: 7, k: 3.1, d: 2.6, a: 11.4, kp: 73.2 }, hist: null },
      { name: "Saint", role: "MID", cur: { g: 7, k: 4.9, d: 2.1, a: 7.7, kp: 63.6 }, hist: { g: 18, k: 4.7, d: 2.6, a: 5.2, kp: 66.0 } },
      { name: "Berserker", role: "BOT", cur: { g: 7, k: 7.6, d: 1.7, a: 5.1, kp: 60.6 }, hist: { g: 18, k: 4.4, d: 2.2, a: 6.1, kp: 70.6 } },
      { name: "Isles", role: "SUP", cur: { g: 7, k: 1.0, d: 2.4, a: 13.1, kp: 69.8 }, hist: { g: 18, k: 0.4, d: 1.9, a: 11.3, kp: 77.4 } },
    ],
  },
  Sentinels: {
    color: "#8a9bb5",
    players: [
      { name: "Impact", role: "TOP", cur: { g: 9, k: 2.1, d: 3.3, a: 5.9, kp: 44.0 }, hist: { g: 18, k: 2.4, d: 2.9, a: 3.9, kp: 51.4 } },
      { name: "HamBak", role: "JNG", cur: { g: 9, k: 5.6, d: 2.9, a: 6.1, kp: 71.9 }, hist: { g: 18, k: 3.3, d: 2.4, a: 6.1, kp: 76.9 } },
      { name: "DarkWings", role: "MID", cur: { g: 9, k: 3.0, d: 3.7, a: 7.3, kp: 70.0 }, hist: { g: 18, k: 1.7, d: 2.6, a: 5.5, kp: 55.8 } },
      { name: "Rahel", role: "BOT", cur: { g: 9, k: 4.7, d: 1.7, a: 5.7, kp: 68.5 }, hist: { g: 18, k: 3.7, d: 2.1, a: 4.4, kp: 72.4 } },
      { name: "Huhi", role: "SUP", cur: { g: 9, k: 0.7, d: 3.1, a: 11.2, kp: 79.5 }, hist: { g: 18, k: 0.8, d: 2.4, a: 8.8, kp: 81.8 } },
    ],
  },
  Cloud9: {
    color: "#4fa8e0",
    players: [
      { name: "Thanatos", role: "TOP", cur: { g: 10, k: 4.2, d: 2.2, a: 4.4, kp: 48.3 }, hist: { g: 18, k: 2.8, d: 2.0, a: 5.2, kp: 50.5 } },
      { name: "Blaber", role: "JNG", cur: { g: 10, k: 2.6, d: 2.9, a: 9.9, kp: 73.6 }, hist: { g: 18, k: 3.6, d: 1.8, a: 8.4, kp: 72.8 } },
      { name: "Loki", role: "MID", cur: { g: 10, k: 5.0, d: 2.9, a: 7.8, kp: 75.2 }, hist: null },
      { name: "Tactical", role: "BOT", cur: { g: 10, k: 4.1, d: 2.3, a: 6.3, kp: 61.3 }, hist: null },
      { name: "Vulcan", role: "SUP", cur: { g: 10, k: 1.0, d: 2.4, a: 11.9, kp: 75.1 }, hist: { g: 18, k: 1.1, d: 1.3, a: 12.1, kp: 79.1 } },
    ],
  },
  "Shopify Rebellion": {
    color: "#7ed957",
    players: [
      { name: "Fudge", role: "TOP", cur: { g: 10, k: 3.5, d: 2.0, a: 5.1, kp: 58.2 }, hist: { g: 15, k: 1.9, d: 3.3, a: 3.7, kp: 44.8 } },
      { name: "Contractz", role: "JNG", cur: { g: 10, k: 2.5, d: 2.4, a: 7.3, kp: 73.1 }, hist: { g: 15, k: 3.5, d: 3.2, a: 6.2, kp: 78.8 } },
      { name: "Zinie", role: "MID", cur: { g: 10, k: 2.0, d: 3.0, a: 6.3, kp: 61.0 }, hist: { g: 15, k: 2.5, d: 3.1, a: 5.7, kp: 61.8 } },
      { name: "Bvoy", role: "BOT", cur: { g: 10, k: 4.2, d: 2.2, a: 5.0, kp: 70.5 }, hist: { g: 15, k: 4.0, d: 2.3, a: 4.7, kp: 67.1 } },
      { name: "Zeyzal", role: "SUP", cur: { g: 10, k: 1.2, d: 2.8, a: 8.4, kp: 68.9 }, hist: null },
    ],
  },
  Dignitas: {
    color: "#c95050",
    players: [
      { name: "Denathor", role: "TOP", cur: { g: 11, k: 1.8, d: 3.5, a: 4.2, kp: 54.9 }, hist: null },
      { name: "eXyu", role: "JNG", cur: { g: 6, k: 2.7, d: 5.7, a: 8.5, kp: 79.2 }, hist: { g: 17, k: 1.8, d: 3.8, a: 4.8, kp: 76.1 } },
      { name: "Dardoch", role: "JNG", cur: { g: 5, k: 1.4, d: 3.4, a: 4.2, kp: 96.7 }, hist: null },
      { name: "Palafox", role: "MID", cur: { g: 11, k: 2.7, d: 2.9, a: 4.5, kp: 65.4 }, hist: { g: 17, k: 1.9, d: 3.7, a: 3.5, kp: 57.8 } },
      { name: "FBI", role: "BOT", cur: { g: 11, k: 3.1, d: 3.5, a: 4.0, kp: 65.5 }, hist: { g: 17, k: 2.8, d: 2.8, a: 3.6, kp: 61.1 } },
      { name: "IgNar", role: "SUP", cur: { g: 11, k: 0.5, d: 3.1, a: 7.5, kp: 71.8 }, hist: { g: 17, k: 0.4, d: 2.4, a: 6.4, kp: 77.1 } },
    ],
  },
  FlyQuest: {
    color: "#3fbf7f",
    players: [
      { name: "Gakgos", role: "TOP", cur: { g: 8, k: 1.6, d: 3.1, a: 4.1, kp: 33.8 }, hist: { g: 19, k: 2.4, d: 1.9, a: 6.4, kp: 57.6 } },
      { name: "Gryffinn", role: "JNG", cur: { g: 8, k: 4.8, d: 3.9, a: 5.1, kp: 72.4 }, hist: { g: 19, k: 4.2, d: 2.8, a: 7.2, kp: 75.0 } },
      { name: "Quad", role: "MID", cur: { g: 8, k: 3.4, d: 3.1, a: 6.4, kp: 74.2 }, hist: { g: 19, k: 3.2, d: 2.4, a: 7.5, kp: 70.4 } },
      { name: "Massu", role: "BOT", cur: { g: 8, k: 3.4, d: 4.0, a: 6.0, kp: 65.0 }, hist: { g: 19, k: 4.6, d: 3.0, a: 5.9, kp: 63.3 } },
      { name: "Cryogen", role: "SUP", cur: { g: 8, k: 0.9, d: 4.5, a: 8.8, kp: 66.4 }, hist: { g: 19, k: 0.7, d: 3.1, a: 11.3, kp: 81.1 } },
    ],
  },
  "Team Liquid": {
    color: "#1e90c8",
    players: [
      { name: "Morgan", role: "TOP", cur: { g: 8, k: 3.1, d: 1.6, a: 8.3, kp: 54.8 }, hist: { g: 18, k: 2.1, d: 2.7, a: 4.9, kp: 45.4 } },
      { name: "Josedeodo", role: "JNG", cur: { g: 8, k: 4.1, d: 1.9, a: 9.8, kp: 68.3 }, hist: { g: 18, k: 4.8, d: 2.2, a: 7.1, kp: 77.9 } },
      { name: "Quid", role: "MID", cur: { g: 8, k: 5.8, d: 1.4, a: 8.8, kp: 71.3 }, hist: { g: 18, k: 3.3, d: 2.7, a: 6.4, kp: 63.3 } },
      { name: "Yeon", role: "BOT", cur: { g: 8, k: 6.5, d: 1.6, a: 6.9, kp: 66.3 }, hist: { g: 18, k: 3.7, d: 2.4, a: 6.2, kp: 63.0 } },
      { name: "CoreJJ", role: "SUP", cur: { g: 8, k: 1.1, d: 1.6, a: 14.9, kp: 77.5 }, hist: { g: 18, k: 1.3, d: 2.1, a: 11.0, kp: 80.9 } },
    ],
  },
  Disguised: {
    color: "#b06fd1",
    players: [
      { name: "Srtty", role: "TOP", cur: { g: 9, k: 2.7, d: 3.8, a: 2.4, kp: 37.9 }, hist: { g: 4, k: 2.5, d: 2.3, a: 6.3, kp: 61.2 } },
      { name: "KryRa", role: "JNG", cur: { g: 9, k: 2.1, d: 4.3, a: 5.4, kp: 70.8 }, hist: { g: 15, k: 2.3, d: 3.9, a: 5.5, kp: 71.8 } },
      { name: "Callme", role: "MID", cur: { g: 9, k: 2.1, d: 3.9, a: 4.2, kp: 61.1 }, hist: { g: 15, k: 2.8, d: 3.7, a: 3.8, kp: 60.4 } },
      { name: "sajed", role: "BOT", cur: { g: 9, k: 2.1, d: 4.6, a: 4.7, kp: 64.0 }, hist: { g: 15, k: 2.3, d: 3.6, a: 3.1, kp: 49.4 } },
      { name: "Lyonz", role: "SUP", cur: { g: 9, k: 0.2, d: 4.2, a: 7.0, kp: 68.4 }, hist: { g: 15, k: 0.5, d: 2.9, a: 6.7, kp: 69.8 } },
    ],
  },
};

// Real, confirmed issue: a team's players list isn't a clean "5
// starters" roster the way LoL's explicit roster fetch gives — CS2 in
// particular has no roster endpoint at all, so teams_payload is built by
// unioning every player name who's appeared for that team ANYWHERE in
// the discovery window, which can genuinely exceed 5 once a roster
// change happens mid-window (a sub, a stand-in, an actual swap). Used
// consistently everywhere a team's "active lineup" matters — both for
// display (don't show a departed player's row with a real projected
// number next to current starters) and for team-strength math below
// (teamStatPerGame previously summed EVERY player unconditionally while
// only capping the divisor at 5, silently inflating any team with more
// than 5 tracked players). Heuristic: the 5 with the most recorded
// games are the most likely current starters — uses data already on
// hand (cur.g), no new scraping needed. Not a perfect signal (a
// recently-benched starter with a big early-season game count could
// still edge out a genuine new starter), but far more honest than
// treating every historical name as equally live right now.
function likelyStarters(players) {
  if (players.length <= 5) return players;
  return [...players].sort((a, b) => (b.cur?.g || 0) - (a.cur?.g || 0)).slice(0, 5);
}

const teamStatPerGame = (teams, teamName, statKey) => {
  // An unrostered team is a real and common case, not a bug: CS2's roster
  // tracks ~50 teams while its upcoming fixtures reference ~100, so most
  // boards contain matches whose opponent this app has never scraped.
  // Returning null lets the caller fall back to a neutral adjustment;
  // reaching into `.players` threw, which is why those fixtures used to be
  // refused outright along with any line posted on them.
  if (!teams[teamName] || !Array.isArray(teams[teamName].players)) return null;
  const allPlayers = teams[teamName].players;
  // Distinct-role count handles LoL roster swaps correctly (e.g. two
  // players sharing "JNG" after a mid-split change still count as one
  // active slot) — computed against the FULL roster history, not just
  // likely starters, since a departed player's role still legitimately
  // counts toward "how many distinct roles has this team fielded."
  const distinctRoles = new Set(allPlayers.map((p) => p.role)).size;
  if (distinctRoles > 1) {
    return allPlayers.reduce((sum, p) => sum + p.cur[statKey], 0) / distinctRoles;
  }
  // No role data (CS2, or Valorant players without one) — both the sum
  // AND the divisor now consistently scope to the same likely-starters
  // subset, instead of summing everyone while only capping the divisor.
  const players = likelyStarters(allPlayers);
  return players.reduce((sum, p) => sum + p.cur[statKey], 0) / (players.length || 1);
};

const leagueAvgStat = (teams, statKey) => {
  const names = Object.keys(teams);
  return names.reduce((s, t) => s + teamStatPerGame(teams, t, statKey), 0) / names.length;
};

/* ============================================================
   FALLBACK SCHEDULE
   ============================================================ */

const FALLBACK_PAST_MATCHES = [
  { week: "Week 1", date: "2026-07-25", teamA: "FlyQuest", teamB: "LYON", winner: "LYON", score: "0-2",
    actual: { FlyQuest: { Gakgos: 5, Gryffinn: 10, Quad: 5, Massu: 7, Cryogen: 1 }, LYON: { Dhokla: 11, Armao: 5, Saint: 9, Berserker: 16, Isles: 4 } } },
  { week: "Week 1", date: "2026-07-25", teamA: "Dignitas", teamB: "Sentinels", winner: "Sentinels", score: "1-2",
    actual: { Dignitas: { Denathor: 4, eXyu: 3, Palafox: 9, FBI: 8, IgNar: 0 }, Sentinels: { Impact: 4, HamBak: 19, DarkWings: 9, Rahel: 3, Huhi: 2 } } },
  { week: "Week 1", date: "2026-07-26", teamA: "Team Liquid", teamB: "Cloud9", winner: "Team Liquid", score: "2-0",
    actual: { "Team Liquid": { Morgan: 2, Josedeodo: 6, Quid: 14, Yeon: 13, CoreJJ: 3 }, Cloud9: { Thanatos: 8, Blaber: 2, Loki: 4, Tactical: 5, Vulcan: 1 } } },
  { week: "Week 1", date: "2026-07-26", teamA: "Shopify Rebellion", teamB: "Disguised", winner: "Shopify Rebellion", score: "2-0",
    actual: { "Shopify Rebellion": { Fudge: 13, Contractz: 5, Zinie: 4, Bvoy: 10, Zeyzal: 4 }, Disguised: { Srtty: 5, KryRa: 3, Callme: 4, sajed: 3, Lyonz: 1 } } },
  { week: "Week 2", date: "2026-08-01", teamA: "Cloud9", teamB: "Dignitas", winner: "Cloud9", score: "2-1",
    actual: { Dignitas: { Denathor: 5, eXyu: 8, Palafox: 10, FBI: 10, IgNar: 1 }, Cloud9: { Thanatos: 10, Blaber: 10, Loki: 9, Tactical: 4, Vulcan: 4 } } },
  { week: "Week 2", date: "2026-08-01", teamA: "Disguised", teamB: "FlyQuest", winner: "FlyQuest", score: "0-2",
    actual: { Disguised: { Srtty: 5, KryRa: 8, Callme: 5, sajed: 5, Lyonz: 0 }, FlyQuest: { Gakgos: 5, Gryffinn: 17, Quad: 10, Massu: 16, Cryogen: 5 } } },
  { week: "Week 2", date: "2026-08-02", teamA: "LYON", teamB: "Shopify Rebellion", winner: "LYON", score: "2-0",
    actual: { "Shopify Rebellion": { Fudge: 5, Contractz: 4, Zinie: 1, Bvoy: 7, Zeyzal: 4 }, LYON: { Dhokla: 6, Armao: 7, Saint: 12, Berserker: 20, Isles: 2 } } },
  { week: "Week 2", date: "2026-08-02", teamA: "Team Liquid", teamB: "Sentinels", winner: "Team Liquid", score: "2-0",
    actual: { "Team Liquid": { Morgan: 4, Josedeodo: 3, Quid: 6, Yeon: 17, CoreJJ: 2 }, Sentinels: { Impact: 2, HamBak: 6, DarkWings: 2, Rahel: 4, Huhi: 0 } } },
  { week: "Week 3", date: "2026-08-08", teamA: "Cloud9", teamB: "Disguised", winner: "Cloud9", score: "2-1",
    actual: { Disguised: { Srtty: 11, KryRa: 5, Callme: 5, sajed: 6, Lyonz: 1 }, Cloud9: { Thanatos: 4, Blaber: 2, Loki: 12, Tactical: 12, Vulcan: 1 } } },
  { week: "Week 3", date: "2026-08-08", teamA: "Sentinels", teamB: "Shopify Rebellion", winner: "Shopify Rebellion", score: "1-2",
    actual: { "Shopify Rebellion": { Fudge: 13, Contractz: 6, Zinie: 2, Bvoy: 6, Zeyzal: 2 }, Sentinels: { Impact: 5, HamBak: 9, DarkWings: 3, Rahel: 13, Huhi: 4 } } },
  { week: "Week 3", date: "2026-08-09", teamA: "Dignitas", teamB: "LYON", winner: "LYON", score: "0-2",
    actual: { Dignitas: { Denathor: 1, Dardoch: 2, Palafox: 4, FBI: 3, IgNar: 0 }, LYON: { Dhokla: 2, Armao: 6, Saint: 5, Berserker: 11, Isles: 1 } } },
  { week: "Week 3", date: "2026-08-09", teamA: "FlyQuest", teamB: "Team Liquid", winner: "Team Liquid", score: "0-2",
    actual: { "Team Liquid": { Morgan: 10, Josedeodo: 4, Quid: 14, Yeon: 10, CoreJJ: 4 }, FlyQuest: { Gakgos: 1, Gryffinn: 6, Quad: 7, Massu: 1, Cryogen: 0 } } },
  { week: "Week 4", date: "2026-08-15", teamA: "Disguised", teamB: "Team Liquid", winner: "Team Liquid", score: "0-2",
    actual: { "Team Liquid": { Morgan: 9, Josedeodo: 20, Quid: 12, Yeon: 12, CoreJJ: 0 }, Disguised: { Srtty: 3, KryRa: 3, Callme: 5, sajed: 5, Lyonz: 0 } } },
  { week: "Week 4", date: "2026-08-16", teamA: "Cloud9", teamB: "FlyQuest", winner: "Cloud9", score: "2-0",
    actual: { FlyQuest: { Gakgos: 2, Gryffinn: 5, Quad: 5, Massu: 3, Cryogen: 1 }, Cloud9: { Thanatos: 10, Blaber: 3, Loki: 12, Tactical: 11, Vulcan: 3 } } },
];

// Confirmed Week 5 slate (Liquipedia regular-season schedule) — used only
// if the live fetch is unavailable.
const FALLBACK_UPCOMING_MATCHES = [
  { week: "Week 5", date: "Aug 22", time: "1:00 PM PT", teamA: "Team Liquid", teamB: "Shopify Rebellion" },
  { week: "Week 5", date: "Aug 22", time: "4:00 PM PT", teamA: "Cloud9", teamB: "LYON" },
  { week: "Week 5", date: "Aug 23", time: "1:00 PM PT", teamA: "Disguised", teamB: "Sentinels" },
  { week: "Week 5", date: "Aug 23", time: "4:00 PM PT", teamA: "Dignitas", teamB: "FlyQuest" },
];

// Only LCS has a hand-built offline snapshot (from the original manual data
// pull). Every other region/game relies entirely on the live fetch — if it
// fails, they'll show an empty state rather than wrong/stale data.
const FALLBACK_REGIONS_LOL = {
  LCS: { teams: FALLBACK_TEAMS, past_matches: FALLBACK_PAST_MATCHES, upcoming_matches: FALLBACK_UPCOMING_MATCHES },
  LEC: { teams: {}, past_matches: [], upcoming_matches: [] },
  LCK: { teams: {}, past_matches: [], upcoming_matches: [] },
  LPL: { teams: {}, past_matches: [], upcoming_matches: [] },
  LCP: { teams: {}, past_matches: [], upcoming_matches: [] },
  CBLOL: { teams: {}, past_matches: [], upcoming_matches: [] },
  TCL: { teams: {}, past_matches: [], upcoming_matches: [] },
};
const FALLBACK_REGIONS_VALORANT = {
  "VCT Americas": { teams: {}, past_matches: [], upcoming_matches: [] },
  "VCT EMEA": { teams: {}, past_matches: [], upcoming_matches: [] },
  "VCT Pacific": { teams: {}, past_matches: [], upcoming_matches: [] },
  "VCT China": { teams: {}, past_matches: [], upcoming_matches: [] },
};
const FALLBACK_REGIONS_CS2 = {
  CS2: { teams: {}, past_matches: [], upcoming_matches: [] },
};

/* ============================================================
   GAMES — everything that differs between League of Legends and
   Valorant lives in this one config. Every component below this
   point (tabs, model, cards) is written generically against
   whatever `teams`/`pastMatches` it's handed, with zero LoL- or
   Valorant-specific logic — adding a third game later should only
   mean adding an entry here plus a scraper, not touching the UI.
   ============================================================ */
const GAMES = {
  lol: {
    label: "League of Legends",
    dataUrl: DATA_URL_LOL,
    regionList: ["LCS", "LEC", "LCK", "LPL", "LCP", "CBLOL", "TCL"],
    regionLabels: { LCS: "LCS", LEC: "LEC", LCK: "LCK", LPL: "LPL", LCP: "LCP", CBLOL: "CBLOL", TCL: "TCL" },
    fallbackRegions: FALLBACK_REGIONS_LOL,
  },
  valorant: {
    label: "Valorant",
    dataUrl: DATA_URL_VALORANT,
    // Champions is last deliberately: switching game selects regionList[0],
    // and that entry is empty until the scraper has run against the event,
    // so leading with it would land people on a blank view.
    regionList: ["VCT Americas", "VCT EMEA", "VCT Pacific", "VCT China", "VCT Champions"],
    regionLabels: { "VCT Champions": "Champions", "VCT Americas": "Americas", "VCT EMEA": "EMEA", "VCT Pacific": "Pacific", "VCT China": "China" },
    fallbackRegions: FALLBACK_REGIONS_VALORANT,
  },
  cs2: {
    label: "CS2",
    dataUrl: DATA_URL_CS2,
    // CS2 doesn't have franchised regions the way LCS/VCT do — it's an
    // individually-ranked global scene, tournament-based rather than
    // league-based. One pseudo-region tracking a curated list of
    // currently top-ranked teams (see scrape_cs2.py's TRACKED_TEAMS)
    // stands in for a real region here.
    regionList: ["CS2"],
    regionLabels: { CS2: "Top Teams" },
    fallbackRegions: FALLBACK_REGIONS_CS2,
  },
};
const GAME_LIST = Object.keys(GAMES);

/* ============================================================
   STAT TYPES — the model can project kills, deaths, or assists.
   Each has a different natural "opponent adjustment" direction:
   a player's KILLS scale with the opponent's leakiness (their
   deaths/game); a player's DEATHS scale with the opponent's own
   kill power (their kills/game) — the opposite basis. ASSISTS
   follow kills' direction, since they come from the same team
   kill-events. Kill-participation is a meaningful multiplier for
   kills/assists (both are "kill events") but doesn't mean much
   for deaths, so it's skipped there.
   ============================================================ */

const STAT_TYPES = {
  // `singular` exists for prose ("weighted kill projections"), where the
  // plural label reads as a typo.
  //
  // `games` lists the games that RECORD the stat. Headshots are a CS2
  // field and nothing else carries them, so offering the tab everywhere
  // would produce a page of blanks — every projection would find no rate,
  // return null, and render as an absent number with no explanation. The
  // selector reads this and only shows a stat the current game can
  // actually answer.
  kills: { key: "k", label: "Kills", singular: "kill", oppBasis: "d", useKP: true, laneSpecific: true },
  deaths: { key: "d", label: "Deaths", singular: "death", oppBasis: "k", useKP: false, laneSpecific: true },
  assists: { key: "a", label: "Assists", singular: "assist", oppBasis: "d", useKP: true, laneSpecific: false },
  headshots: { key: "hs", label: "Headshots", singular: "headshot", oppBasis: "d",
               useKP: false, laneSpecific: false, games: ["cs2"] },
};

function statsForGame(game) {
  return Object.entries(STAT_TYPES).filter(([, cfg]) => !cfg.games || cfg.games.includes(game));
}

/* How many lines the provider is posting on each stat, right now.

   The selector offered four stats equally and the market is nothing
   like that. Across every line this app has recorded: CS2 kills 736,
   CS2 headshots 592, Valorant kills 151, LoL kills 35 -- and deaths and
   assists, both first-class in the model, four lines between them,
   ever. So two of the four tabs led to an empty board every time, and
   headshots -- 39% of the market -- sat behind a tab nobody had a
   reason to press.

   Counted from the live board rather than hardcoded, so it follows the
   provider instead of a comment that goes stale. */
function postedLineCounts(propsData, game) {
  const out = {};
  const byPlayer = (propsData && propsData.props && propsData.props[game]) || {};
  for (const lines of Object.values(byPlayer)) {
    for (const line of lines || []) {
      if (line && line.stat) out[line.stat] = (out[line.stat] || 0) + 1;
    }
  }
  return out;
}

/* The stats worth offering for this game: the ones with lines on the
   board, plus whichever is selected.

   The selected one is always kept so the tab a reader is standing on
   never disappears underneath them. With no board loaded at all this
   returns everything, which is the old behaviour and the right
   fallback -- an absent props.json is not evidence that a stat has no
   market. */
function offeredStats(game, propsData, selected) {
  const all = statsForGame(game);
  const counts = postedLineCounts(propsData, game);
  if (!Object.keys(counts).length) return all.map(([key, cfg]) => [key, cfg, null]);
  const offered = all.filter(([key]) => counts[key] > 0 || key === selected);
  return (offered.length ? offered : all).map(([key, cfg]) => [key, cfg, counts[key] || 0]);
}

/* ============================================================
   MODEL — all functions take `teams` explicitly since it can now
   come from a live fetch instead of the module-level fallback.
   ============================================================ */

/* The kill-participation baseline this multiplier measures a player
   against, derived from the players actually in the data rather than
   hardcoded.

   It used to be the literal 66, which is a LEAGUE OF LEGENDS number: LoL
   kill participation runs ~66% because assists are plentiful and most
   kills involve two or three players. CS2 and Valorant score the same
   field on a completely different scale — their rostered medians are
   26.4% and 27.7%, and their HIGHEST players reach 39.7% and 35.1%.
   Against a baseline of 66, therefore, no CS2 or Valorant player could
   ever score above 1.0: `relative` topped out around 0.6, and what was
   meant as a two-sided "is this player above or below typical" became a
   one-sided haircut applied to literally every projection in both games.

   That is not a rounding error. Measured on the point-in-time backtest
   (scripts/dev/diagnose_calibration.py), CS2 kills predicted 0.939x the
   actual and CS2/Valorant assists 0.944x/0.953x, while LoL — the game
   the constant was right for — sat at 0.999x. A flat ~6% under-prediction
   on one game is invisible in MAE, which is what every weight here was
   tuned against, and it does not stay invisible once a projection is
   placed next to a posted line: it recommends the under on every single
   player, which looks like a signal and is really a ruler with the wrong
   zero. That is exactly how it was found.

   Deriving it from the roster fixes all three games at once and cannot
   go stale the way a literal does. Using the MEAN (not the median) is
   the deliberate choice: it is what makes the average player's
   multiplier land on 1.0, which is precisely the property that keeps the
   layer calibration-neutral.

   Note on leakage, stated rather than hidden: cur.kp is a whole-season
   snapshot with no point-in-time filtering, so a league average over it
   is a season-wide figure too. That is not a NEW leak — kpMultiplier
   already read the same unfiltered field for the player's own rate — and
   a league-wide aggregate is far more dilute than a per-player one. It
   should still be rebuilt point-in-time when cur.kp is. */
const LEAGUE_AVG_KP_FALLBACK = 66; // only reached when no player carries a kp at all
const leagueAvgKPCache = new WeakMap();
function leagueAvgKP(teams) {
  if (!teams || typeof teams !== "object") return LEAGUE_AVG_KP_FALLBACK;
  if (leagueAvgKPCache.has(teams)) return leagueAvgKPCache.get(teams);
  let sum = 0, n = 0;
  for (const entry of Object.values(teams)) {
    for (const p of (entry && entry.players) || []) {
      const kp = p && p.cur && p.cur.kp;
      if (typeof kp === "number" && kp > 0) { sum += kp; n++; }  // 0 is the not-computed sentinel, same as below
    }
  }
  const avg = n > 0 ? sum / n : LEAGUE_AVG_KP_FALLBACK;
  leagueAvgKPCache.set(teams, avg);
  return avg;
}

const KP_SHRINK = 4; // prior matches at which a player's own KP is worth as
                     // much as the league's. Their per-match sample is thin
                     // (median 2 in CS2) and unshrunk it errs by 3.86pp
                     // against 3.32pp shrunk.

/* One match's kill participation, as a percentage.

   kp_numerator is exactly k + a on all 1414 CS2 rows that carry it, so the
   same figure is derivable for LoL and Valorant, which record neither.
   kp_denominator is preferred where present because it is the team's kills
   WHILE THAT PLAYER PLAYED — it genuinely differs between players on a side
   that used a substitute — and falls back to the side's own total. */
function matchKP(match, team, playerName) {
  const raw = ((match.actual || {})[team] || {})[playerName];
  if (!raw || typeof raw !== "object") return null;
  const den = raw.kp_denominator || teamTotal(match, team, "k");
  if (!den) return null;
  let num = raw.kp_numerator;
  if (num === undefined || num === null) {
    if (raw.k === undefined || raw.k === null || raw.a === undefined || raw.a === null) return null;
    num = raw.k + raw.a;
  }
  return (100 * num) / den;
}

/* Recency-weighted KP from matches before the cutoff, shrunk to the league.

   This used to read player.cur.kp, a WHOLE-SEASON figure with no cutoff
   awareness, so a backtest of a match in May was handed a kill
   participation partly built from games played in August. Measured against
   its own leave-one-out version, that leak was worth 0.66pp — about a
   quarter of the estimator's apparent accuracy, and all of the kp layer's
   apparent value. See the weight table for what happened when it was
   removed. */
function pointInTimeKP(pastMatches, teams, team, playerName, cutoffDate) {
  const values = [];
  for (const m of priorMatches(pastMatches, team, cutoffDate)) {
    const v = matchKP(m, team, playerName);
    if (v !== null) values.push(v);
  }
  if (!values.length) return null;
  let rate = decayedMean(values);
  const league = leagueAvgKP(teams);
  if (league) {
    const w = values.length / (values.length + KP_SHRINK);
    rate = w * rate + (1 - w) * league;
  }
  return rate;
}

function kpMultiplier(player, historyWeight, kpStrength, teams, pastMatches, team, cutoffDate) {
  if (!kpStrength) return 1;
  const league = leagueAvgKP(teams);
  let kp = null;
  if (pastMatches && team) kp = pointInTimeKP(pastMatches, teams, team, player.name, cutoffDate);
  if (kp === null) {
    // No prior appearances to build one from. The season figure is all
    // that is left, and in the live path (cutoffDate null) it is
    // legitimately everything-so-far rather than a look at the future.
    const curKP = player.cur.kp;
    if (!curKP) return 1;
    const histKP = player.hist ? player.hist.kp : curKP;
    kp = historyWeight * histKP + (1 - historyWeight) * curKP;
  }
  return 1 + kpStrength * (kp / league - 1);
}

function opponentMultiplier(teams, opponentTeam, oppStrength, oppBasisKey) {
  const oppStat = teamStatPerGame(teams, opponentTeam, oppBasisKey);
  // Neutral when the opponent is unknown, on the same reasoning the
  // lane-specific path already uses above: no adjustment beats an invented
  // one. The projection is then a neutral-opponent estimate, and anything
  // showing it says so rather than passing it off as fully adjusted.
  if (oppStat === null) return 1;
  const ratio = oppStat / leagueAvgStat(teams, oppBasisKey);
  return 1 + oppStrength * (ratio - 1);
}

/* How many maps a stored match's `actual` totals actually cover.

   Every reader of this used to default to 2, and for CS2 that default
   was simply wrong: the scraper has never written maps_counted -- it
   writes `games` -- so a Bo1 was read as two maps everywhere. 50 of 699
   committed CS2 matches are Bo1s, and each one was compared against a
   two-map projection in the backtest and folded into its players' rates
   at half its real per-map value. It is the single largest source of the
   "model runs high on CS2" reading: the days that spike hardest are the
   days with the most Bo1s, and the spike shows up on kills and deaths
   together (correlation 0.87), which is the signature of a map-count
   mismatch rather than a level bias.

   LoL records maps_counted properly and is unaffected. Valorant's
   `games` is fixed at 2 by construction, so it reads the same either
   way. */
function mapsCountedFor(match) {
  const recorded = (match && typeof match.maps_counted === "number" && match.maps_counted > 0)
    ? match.maps_counted
    : (match && typeof match.games === "number" && match.games > 0 ? match.games : null);
  return recorded === null ? 2 : recorded;
}

function getActualStat(match, team, playerName, statKey) {
  const raw = match.actual && match.actual[team] && match.actual[team][playerName];
  if (raw === undefined) return undefined;
  if (typeof raw === "number") return statKey === "k" ? raw : null; // legacy kills-only format
  return raw[statKey];
}

/* ============================================================
   PATCH AWARENESS — an off-patch match gets an extra weight
   discount on top of recency decay, since a kill rate from two
   patches ago may reflect a meta that no longer exists. Patch
   strings look like "16.16" — compared numerically (major, minor),
   not as plain strings, since "16.9" > "16.10" alphabetically but
   isn't chronologically.
   ============================================================ */

function parsePatch(patchStr) {
  if (!patchStr) return null;
  const m = /^(\d+)\.(\d+)/.exec(patchStr);
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10)];
}

function comparePatch(a, b) {
  if (!a || !b) return 0;
  return a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1];
}

function latestPatch(pastMatches, cutoffDate) {
  let best = null;
  for (const m of pastMatches) {
    if (cutoffDate !== null && (!m.date || m.date >= cutoffDate)) continue;
    const p = parsePatch(m.patch);
    if (p && (!best || comparePatch(p, best) > 0)) best = p;
  }
  return best;
}

/* ============================================================
   RECENCY-WEIGHTED RATE — replaces a flat season average with an
   exponential decay by match recency, so a hot streak or a roster
   swap shows up faster than waiting for a whole season of games to
   dilute it. cutoffDate=null means "use every completed match in
   the split" (for live Future-tab projections); a real date means
   "only matches strictly before this one" (for point-in-time Past
   Results backtesting) — same function, same decay logic, either way.

   halfLife is in MATCHES (not individual games) since that's the
   granularity pastMatches stores — each entry is already a game-1
   + game-2 combined total, not two separate rows. A halfLife of 20+
   is treated as "off" (flat average) since decay is negligible over
   a realistic split length at that point.

   referencePatch + patchDiscount layer an additional weight penalty
   on top of recency decay for any match not on the reference patch.
   referencePatch is passed in rather than always computed as "the
   true latest patch" because for point-in-time backtesting, the
   relevant reference is the patch the match being predicted was
   played on — not today's real-world patch, which would leak
   future information into a backtest of an old match.
   ============================================================ */

function recencyWeightedRate(pastMatches, team, playerName, statKey, cutoffDate, halfLife, referencePatch, patchDiscount) {
  const entries = [];
  for (const m of pastMatches) {
    if (cutoffDate !== null && (!m.date || m.date >= cutoffDate)) continue;
    if (!m.actual || !(m.teamA === team || m.teamB === team) || !m.actual[team]) continue;
    const val = getActualStat(m, team, playerName, statKey);
    if (val === undefined || val === null) continue; // didn't play, or legacy data missing this stat
    // maps carries the series' real map count so the weighted per-game
    // rate divides by what the total actually covers. A Bo5 sums 3 maps
    // but a fixed 2 would divide it by 2, inflating that player's rate.
    entries.push({ date: m.date || "", val, patch: parsePatch(m.patch), maps: mapsCountedFor(m) });
  }
  if (entries.length === 0) return { rate: null, games: 0 };
  entries.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0)); // oldest first
  const n = entries.length;
  const flat = halfLife >= 20;
  let weightedSum = 0, weightedGames = 0, totalMaps = 0;
  entries.forEach((e, idx) => {
    const matchesAgo = (n - 1) - idx; // 0 = most recent match
    let weight = flat ? 1 : Math.pow(0.5, matchesAgo / halfLife);
    if (referencePatch && e.patch && comparePatch(e.patch, referencePatch) !== 0) {
      weight *= (1 - patchDiscount);
    }
    weightedSum += e.val * weight;
    weightedGames += e.maps * weight;
    totalMaps += e.maps;
  });
  // games is the prior-map count used for cold-start detection, so it
  // has to reflect real maps too rather than assuming 2 per match.
  return { rate: weightedGames > 0 ? weightedSum / weightedGames : null, games: totalMaps };
}

/* ============================================================
   TEAM-SHARE TIER — a second opinion built from a different quantity
   ============================================================
   Everything above predicts a player's kills per map directly. This
   predicts their SHARE of their own team's kills, and multiplies it by
   how many kills a map in this league tends to produce. The two are
   blended by the `share` weight, exactly as `career` is blended.

   They really are two quantities. In CS2 the coefficient of variation of
   a player's raw series kills averages 0.235; of their share of the team
   total, 0.160 — 32% steadier, and steadier for 111 of the 131 players
   with enough series to measure.

   The pace half is where the surprise is, and it decides the design.
   Correlation between a team's own recency-weighted history and its next
   match's per-map total (scripts/dev/experiment_kill_share.py):

       CS2  r = -0.037      Valorant  r = +0.016      LoL  r = +0.208

   CS2 and Valorant team pace is NOISE — a team's own history predicts its
   next pace no better than a coin. The likely mechanism in CS2 is that
   being better shortens the map rather than raising the kill count: a
   13-4 has fewer rounds, so fewer kills to go round, and the two effects
   cancel. Hence the pace term here is the LEAGUE average and not the
   team's own; measured as a replacement, using the team's own realised
   history was 16-26% WORSE than the shipped model where the league
   version was better. It is also why LoL takes none of this tier — at
   r = +0.208 its pace is a real signal, and its opponent weight already
   captures it.

   An oracle variant fed the real team total it is predicting scores -19%
   on CS2 kills and -51% on CS2 deaths. That headroom is NOT reachable —
   it is the value of knowing the noise — but it does say where the
   remaining error lives, and that deaths are much the most pace-driven
   of the three stats. The adopted weights follow exactly that shape. */
const SHARE_HALF_LIFE = 6; // matches. Swept 2..999: moves OOS MAE by under
                           // 0.4pp on every adopted combination and never
                           // changes a fold count, so it is pinned rather
                           // than tuned — one more slider would be inviting
                           // an overfit to nothing.

function teamTotal(match, team, statKey) {
  // Returns null below five players so a partially-recorded side cannot
  // masquerade as a low-scoring team. Reproduces CS2's own
  // kp_denominator exactly on all 282 team-sides that carry one.
  const side = (match.actual || {})[team];
  if (!side) return null;
  let total = 0, seen = 0;
  for (const raw of Object.values(side)) {
    if (raw && typeof raw === "object") {
      if (raw[statKey] !== undefined && raw[statKey] !== null) { total += raw[statKey]; seen++; }
    } else if (typeof raw === "number" && statKey === "k") { total += raw; seen++; }
  }
  return seen >= 5 ? total : null;
}

function priorMatches(pastMatches, team, cutoffDate) {
  const out = [];
  for (const m of pastMatches) {
    if (!m.actual) continue;
    if (m.teamA !== team && m.teamB !== team) continue;
    if (cutoffDate !== null && cutoffDate !== undefined && (!m.date || m.date >= cutoffDate)) continue;
    out.push(m);
  }
  out.sort((a, b) => ((a.date || "") < (b.date || "") ? -1 : (a.date || "") > (b.date || "") ? 1 : 0));
  return out;
}

function decayedMean(values) {
  const n = values.length;
  if (!n) return null;
  let sum = 0, weight = 0;
  values.forEach((v, i) => {
    const w = Math.pow(0.5, (n - 1 - i) / SHARE_HALF_LIFE);
    sum += v * w; weight += w;
  });
  return weight > 0 ? sum / weight : null;
}

/* Scanning every region team's whole season, per player, per render is
   the one genuinely expensive thing here, so both halves are cached on
   the pastMatches object — replaced wholesale when a fetch lands, so a
   stale entry cannot outlive the data it came from. */
const shareTierCache = new WeakMap();
function tierCache(pastMatches, bucket) {
  let byBucket = shareTierCache.get(pastMatches);
  if (!byBucket) { byBucket = {}; shareTierCache.set(pastMatches, byBucket); }
  if (!byBucket[bucket]) byBucket[bucket] = new Map();
  return byBucket[bucket];
}

function shareRate(pastMatches, team, playerName, statKey, cutoffDate) {
  const cache = tierCache(pastMatches, "share");
  const key = `${team}|${playerName}|${statKey}|${cutoffDate || ""}`;
  if (cache.has(key)) return cache.get(key);
  const values = [];
  for (const m of priorMatches(pastMatches, team, cutoffDate)) {
    const got = getActualStat(m, team, playerName, statKey);
    if (got === undefined || got === null) continue;
    const total = teamTotal(m, team, statKey);
    if (!total) continue;
    values.push(got / total);
  }
  const rate = decayedMean(values);
  cache.set(key, rate);
  return rate;
}

// Averaged over TEAMS rather than over match-sides, so a team that plays
// more often does not drag the league figure toward its own pace.
function leaguePacePerMap(pastMatches, teams, statKey, cutoffDate) {
  const cache = tierCache(pastMatches, "pace");
  const key = `${statKey}|${cutoffDate || ""}`;
  if (cache.has(key)) return cache.get(key);
  const perTeam = [];
  for (const team of Object.keys(teams || {})) {
    const values = [];
    for (const m of priorMatches(pastMatches, team, cutoffDate)) {
      const total = teamTotal(m, team, statKey);
      if (!total) continue;
      values.push(total / mapsCountedFor(m));
    }
    const rate = decayedMean(values);
    if (rate !== null) perTeam.push(rate);
  }
  const avg = perTeam.length ? perTeam.reduce((s, v) => s + v, 0) / perTeam.length : null;
  cache.set(key, avg);
  return avg;
}

/* Blends at the FINAL per-map figure, not into `base`. That is where it
   was measured: the tier is a second opinion on the whole prediction,
   opponent and kp multipliers included, rather than another input to one
   of them. Falls back to the unblended figure whenever the tier cannot be
   computed — a player with no prior appearances, a league with no
   completed matches — so a thin region degrades to today's behaviour
   instead of losing its projection. That fallback is not rare: it covers
   13% of Valorant rows and 24% of CS2's. */
function blendShareTier(perGame, weights, pastMatches, teams, team, playerName, statKey, cutoffDate) {
  const weight = weights.share || 0;
  if (!weight) return { perGame, shareTier: null };
  const share = shareRate(pastMatches, team, playerName, statKey, cutoffDate);
  if (share === null) return { perGame, shareTier: null };
  const pace = leaguePacePerMap(pastMatches, teams, statKey, cutoffDate);
  if (!pace) return { perGame, shareTier: null };
  const shareTier = share * pace;
  return { perGame: (1 - weight) * perGame + weight * shareTier, shareTier, sharePct: share };
}

/* ============================================================
   THIN-SAMPLE SHRINKAGE
   ============================================================
   A player with three maps on record got a rate computed from three
   maps, trusted exactly as much as one built from sixty. Bucketing every
   backtest prediction by how many prior maps the player had says what
   that costs:

       Valorant kills   0-5 maps: MAE 6.77   5-15: 5.89   15-30: 5.95
       CS2 kills        0-5 maps: MAE 6.67   5-15: 5.89   15-30: 5.34

   and those thin buckets are not a fringe — 34% of Valorant rows and 50%
   of CS2's. LoL never shows it, because a LoL player arrives with a
   career baseline and a previous split behind them. Valorant has
   neither: no career scraper exists for it and hist is null, so a new
   player's rate there is three maps and nothing else.

   So the estimate is pulled toward the league's average player by
   n / (n + k) — the standard empirical-Bayes weight. No pull once a
   player has a real sample; most of the way to the prior when they have
   none. k is per game and stat because it is the sample size at which a
   player's own rate becomes worth as much as the league's, and that is
   not the same number in a game that has career data as in one that does
   not. Measured out-of-sample; HARMFUL in LoL at every k tried (+0.45%
   to +11.22% on kills), which is why LoL ships 0. */
const ROSTER_SIZE = 5; // players per side, in all three games. Asserted
                       // against every recorded side in every dataset by
                       // tests/model_tiers.test.mjs, rather than assumed
                       // to stay true.

function leaguePlayerRate(pastMatches, teams, statKey, cutoffDate) {
  const pace = leaguePacePerMap(pastMatches, teams, statKey, cutoffDate);
  return pace ? pace / ROSTER_SIZE : null;
}

/* Applied to the FINAL per-map figure, after the opponent, kp and share
   layers, because that is the number actually being trusted and where
   this was measured. Falls through unchanged when there is no league to
   compare against. */
function shrinkToPrior(perGame, weights, priorGames, pastMatches, teams, statKey, cutoffDate) {
  const k = weights.shrink || 0;
  if (!k) return { perGame, shrunkTo: null };
  const prior = leaguePlayerRate(pastMatches, teams, statKey, cutoffDate);
  if (!prior) return { perGame, shrunkTo: null };
  const w = priorGames / (priorGames + k);
  return { perGame: w * perGame + (1 - w) * prior, shrunkTo: prior, shrinkPull: 1 - w };
}

function project(teams, pastMatches, player, team, opponentTeam, games, weights, statType) {
  const cfg = STAT_TYPES[statType];
  const refPatch = latestPatch(pastMatches, null);
  const weighted = recencyWeightedRate(pastMatches, team, player.name, cfg.key, null, weights.recencyHalfLife, refPatch, weights.patchDiscount);
  // A player can have no rate for this stat at all — headshots are
  // recorded only for CS2, and only from the run that started capturing
  // them. Reading a missing key here produced undefined, which then made
  // every downstream multiplication NaN and rendered as a blank number
  // rather than an absent one. Null propagates instead, and callers
  // already know how to skip a null projection.
  const curFallback = player.cur ? player.cur[cfg.key] : undefined;
  const curRate = weighted.rate !== null ? weighted.rate
                : (typeof curFallback === "number" ? curFallback : null);
  if (curRate === null) return null;
  const recentFormRate = player.hist ? weights.history * player.hist[cfg.key] + (1 - weights.history) * curRate : curRate;
  const { base, careerRate } = applyCareerTier(recentFormRate, player, weights, cfg, null);
  const oppMult = resolveOpponentMultiplier(teams, pastMatches, player, opponentTeam, weights.opponent, cfg, null);
  const kpMult = cfg.useKP ? kpMultiplier(player, weights.history, weights.kp, teams, pastMatches, team, null) : 1;
  const blended = blendShareTier(base * oppMult * kpMult, weights, pastMatches, teams,
                                 team, player.name, cfg.key, null);
  const shrunk = shrinkToPrior(blended.perGame, weights, weighted.games || 0, pastMatches,
                               teams, cfg.key, null);
  const perGame = shrunk.perGame;
  const priorGames = weighted.games || 0;
  return { base, recentFormRate, careerRate, careerWeight: weights.career, oppMult, kpMult,
           shareTier: blended.shareTier, sharePct: blended.sharePct, shareWeight: weights.share || 0,
           shrunkTo: shrunk.shrunkTo, shrinkPull: shrunk.shrinkPull, priorGames,
           evidenceGames: evidenceGamesFor(player, weights, careerRate, priorGames),
           perGame, total: perGame * games };
}

// Career tier — a prior derived from scripts/scrape_career.py, blended
// on TOP of the existing recent-form/split-history base, using its own
// independent weight rather than forcing a 3-way sum-to-1 average —
// consistent with how patchDiscount/kp are already separate
// multiplicative layers rather than folded into one blend. Shared by
// both project() (live) and projectPointInTime() (backtesting) so they
// never drift apart, mirroring scripts/optimize_weights.py's Python
// version of the same logic exactly. Falls back cleanly (base
// unchanged) when a player has no career data yet — rookies, or any
// game/region career history hasn't been built for (Valorant/CS2 don't
// have their own career scraper yet).
//
// IMPORTANT, measured finding — this is NOT genuine multi-season
// history in practice: a real backtest (scripts/sweep_season_half_life.py)
// found the optimal season-decay rate so aggressive (half_life=0.05)
// that "career" ends up nearly identical to the player's CURRENT season
// alone (confirmed via direct sample comparison, diffs ~0.00-0.03).
// The real, honest reason this weight still measurably helps is most
// likely that it's an independently-sourced current-season measurement
// (gol.gg's own canonical player pages) layered on top of the app's
// separate in-house current-season tracking — noise reduction /
// coverage improvement, not real cross-season trend-capturing. Worth
// knowing before assuming this reflects a player's multi-year career.
// CS2's career data is point-in-time computed here, from raw per-game
// history (player.career_games), not a static pre-decayed number — a
// real, confirmed leakage bug found that a single "career" snapshot
// computed once at scrape time ("most recent N games as of right now")
// let a historical backtest prediction be fed a career number partly
// built from games that hadn't happened yet as of that prediction's own
// date, producing a misleadingly perfect-looking career:1.0 in a real
// backtest. LoL still uses a static player.career field for now (its own
// leakage risk is smaller in practice — a whole-season aggregate dilutes
// any single prediction's overlap — but isn't architecturally immune to
// the same issue; worth applying this same fix there once CS2's is
// validated).
/* 180, matching scrape_cs2_career.py's DAY_HALF_LIFE and the Python port
   in optimize_weights.py. This read 60 — the value that constant held
   BEFORE scripts/sweep_cs2_day_half_life.py measured it — while both of
   the others moved to 180 and this one did not, each carrying a comment
   claiming it matched the scraper. Only two of the three did.

   The accuracy cost was small, as the sweep predicted it would be: that
   note records MAE moving under 0.3% across every candidate from 3 days
   to 36500, because MATCHES_PER_PLAYER already caps career history at a
   median of 44 games and over a window that short any half-life past ~45
   days is nearly a flat average. The parity cost was not small. Career
   carries 15-25% of CS2's accuracy, so for as long as these disagreed,
   every CS2 weight tuned in Python was tuned against a decay curve the
   app did not use. Found by diffing the two ports row by row, which is
   the only way this kind of drift ever shows up. */
const CS2_CAREER_DAY_HALF_LIFE = 180;
function pointInTimeCS2CareerRate(player, statKey, cutoffDate) {
  const games = player.career_games || [];
  const cutoffMs = cutoffDate ? new Date(cutoffDate).getTime() : Date.now();
  const eligible = games.filter((g) => g.date && new Date(g.date).getTime() < cutoffMs);
  if (!eligible.length) return null;
  let totalWeight = 0;
  let weighted = 0;
  for (const g of eligible) {
    // Whole days, floored. career_games carry a full timestamp
    // (2026-09-20T17:09:20+00:00) while match dates are bare days, so
    // this difference is real rather than theoretical — and both
    // scrape_cs2_career.py's decayed_baseline() and the Python port use
    // timedelta.days, which truncates. Fractional days here made this
    // the only remaining disagreement between the two ports once the
    // half-life was fixed.
    // A game that does not RECORD this stat is skipped, not counted as
    // zero. `|| 0` folded "bo3.gg has no headshot figure for this map"
    // into "this player got no headshots", which is the same fake-zero
    // failure scrape_cs2_career.py documents at length for k/d/a -- a
    // handful of them in a player's most recent games, where the decay
    // weight is heaviest, dragged whole projections down and reached a
    // user as systematic under-prediction.
    //
    // It never bit for k/d/a because the scraper drops a game missing
    // any of them. It bites the moment a stat is captured for SOME
    // games and not others, which is exactly what a newly added field
    // looks like while the career cache fills.
    const value = g[statKey];
    if (typeof value !== "number") continue;
    const daysAgo = Math.max(0, Math.floor((cutoffMs - new Date(g.date).getTime()) / 86400000));
    const weight = Math.pow(0.5, daysAgo / CS2_CAREER_DAY_HALF_LIFE);
    totalWeight += weight;
    weighted += value * weight;
  }
  return totalWeight > 0 ? weighted / totalWeight : null;
}

/* ============================================================
   HOW MUCH EVIDENCE IS BEHIND A NUMBER

   A projection off 3 games and one off 40 print identically, and for
   anyone deciding what to bet that difference matters more than the
   last decimal place. These do not change any projection -- they
   describe one.

   The thresholds are measured, not chosen. scripts/dev/
   evidence_vs_accuracy.py buckets out-of-sample error by the evidence
   each row had when it was predicted, and the model's own accuracy
   turns sharply at eight games:

     evidence    valorant kills    cs2 headshots   (vs that stat's own MAE)
       0-1g          +12.1%              --
       2-3g           +5.3%           +10.9%
       4-6g           +3.6%            +7.8%
       6-8g           +0.0%            +0.4%
      8-12g           -5.2%            -7.0%
     12-20g           -3.2%           -13.4%

   So below four games the model is materially worse than its own
   average, four to eight is about average, and eight or more is
   consistently better. Those are the boundaries below. Re-run that
   script if the weights move -- a threshold inherited from a weight
   table it no longer matches is worse than none.
   ============================================================ */
const EVIDENCE_THIN = 4;
const EVIDENCE_SOLID = 8;

function careerGameCount(player) {
  // CS2 stores a per-game log; LoL stores an aggregate that carries its
  // own game count. Valorant has no career scraper, so it has neither
  // and falls through to match history alone -- which is correct, since
  // its career weight is zero everywhere.
  if (player.career_games) return player.career_games.length;
  if (player.career && typeof player.career.g === "number") return player.career.g;
  return 0;
}

/* Games of evidence behind one projection, weighted by the tier it
   actually leans on. CS2 kills is career 1.0, so its evidence is the
   career log rather than the match list -- a CS2 player with 6 matches
   on file and 46 career games is not a thin sample for kills, and
   counting only matches would understate it as badly as ignoring
   sample size altogether. Only counts the career log when the career
   tier actually fired: a player with no career data gets none of it. */
function evidenceGamesFor(player, weights, careerRate, priorGames) {
  const careerFired = careerRate != null && weights.career > 0;
  if (!careerFired) return priorGames;
  return weights.career * careerGameCount(player) + (1 - weights.career) * priorGames;
}

/* How much of a claimed edge actually materialises, by evidence.

   Ranking the board by raw edge size put the least-evidenced rows at the
   top, because a model with little to go on produces wilder numbers and
   wilder numbers are bigger disagreements with the line. The top of the
   list was therefore selecting for ignorance.

   These are measured by scripts/dev/edge_realization.py, which regresses
   how far a player actually landed from a typical player against how far
   the model said they would, inside each evidence bucket:

       slope = sum(claim * realised) / sum(claim^2)

   That slope is the fraction of a claimed deviation that shows up. Pooled
   over every game and stat, n-weighted, on 36,707 point-in-time rows:

       0-4g   0.52     8-12g   0.93
       4-8g   0.72      12+g   0.98

   Re-measured after CS2's shrink weights were corrected, which is what
   a calibration table has to be: the first reading here was 0.34 / 0.94,
   taken while CS2 projections were overstating their spread. Fixing the
   model absorbed most of that on its own, and a table left at the old
   numbers would have gone on discounting a fault that no longer existed.
   Re-run scripts/dev/edge_realization.py whenever the weights move.

   Smoothed to be non-decreasing (the raw 12-20g reading dips below 8-12g
   on sample composition) and capped at 1, because this may discount a
   claim and must never inflate one. */
const EDGE_REALIZATION = [
  { upTo: 4, factor: 0.52 },
  { upTo: 8, factor: 0.72 },
  { upTo: 12, factor: 0.93 },
  { upTo: Infinity, factor: 0.98 },
];

function edgeMultiplier(games) {
  // An unknown evidence count is treated as the worst case rather than
  // the best: a row that cannot say what it is built on should not
  // outrank one that can.
  if (typeof games !== "number" || !isFinite(games)) return EDGE_REALIZATION[0].factor;
  for (const band of EDGE_REALIZATION) {
    if (games < band.upTo) return band.factor;
  }
  return EDGE_REALIZATION[EDGE_REALIZATION.length - 1].factor;
}

/* The calibrated projection, then the edge from it -- not the edge
   scaled directly.

   Those are only the same when the line sits at the league average. The
   slope describes how far the model overstates a player's distance from
   a TYPICAL player, so the correction belongs to the projection; the
   edge is whatever that calibrated projection disagrees with the line
   by. Scaling the edge instead put the correction on the wrong
   quantity, and on the committed board it disagreed in sign with this
   on 5% of rows.

   `mean` is the league per-window rate. Without one there is nothing to
   calibrate toward, so the edge is returned untouched rather than
   guessed at. */
function adjustEdge(edge, games, projection, line, mean) {
  if (edge === null || typeof edge !== "number" || !isFinite(edge)) return null;
  if (typeof mean !== "number" || !isFinite(mean)
      || typeof projection !== "number" || typeof line !== "number") {
    return edge * edgeMultiplier(games);
  }
  return (mean + edgeMultiplier(games) * (projection - mean)) - line;
}

/* Evidence borrowed from another competition is not evidence about this
   one.

   A not-yet-started international event carries no completed matches of
   its own, so historyPool lends it the teams' home-region matches. That
   makes a projection possible, and the map count behind it looks large
   -- but every one of those maps was played somewhere else, against a
   different field.

   Measured on the board this was found on: all 78 Valorant lines were
   VCT Champions fixtures, our projections sat 0.48 kills below each
   player's own regional rate, and the market's lines sat 1.48 below.
   Something about a 16-team international field is priced in that
   regional form does not contain, and 73% of the board read OVER as a
   result.

   What that something is worth cannot be measured here: there are zero
   cross-region matches in any of the three games' data, so there is no
   international form to fit against. Inventing a step-up discount would
   be a number with nothing behind it. What IS defensible is declining to
   call these projections well-evidenced, which is what this does.

   The cap is a judgement, not a measurement -- one band below solid, so
   the board stops leading with them and the chip says so. It lifts on
   its own: the moment that event has completed matches of its own,
   borrowed() is false and the real count applies again. */
const EVIDENCE_BORROWED_CAP = EVIDENCE_SOLID - 1;

function historyIsBorrowed(regionData, teamName) {
  if (!regionData) return false;
  const teams = regionData.teams || {};
  // Per-team provenance, written by the scraper since lending became per
  // team. Authoritative for the whole region as soon as ANY team carries
  // it, because then the region-wide flag only means "some are".
  const anyPerTeam = Object.values(teams).some((t) => t && t.from_home_region);
  if (anyPerTeam) return !!(teamName && teams[teamName] && teams[teamName].from_home_region);
  // Older files carry only the region-wide flag, which was all-or-nothing.
  return !!(regionData.rosters_from_home_regions
            && !(regionData.past_matches || []).length);
}

function effectiveEvidence(games, borrowed) {
  if (typeof games !== "number" || !isFinite(games)) return games;
  return borrowed ? Math.min(games, EVIDENCE_BORROWED_CAP) : games;
}

function evidenceTier(games) {
  if (typeof games !== "number" || !isFinite(games)) return null;
  if (games < EVIDENCE_THIN) return "thin";
  if (games < EVIDENCE_SOLID) return "limited";
  return "solid";
}

function applyCareerTier(base, player, weights, cfg, cutoffDate) {
  let careerRate;
  if (player.career_games) {
    careerRate = pointInTimeCS2CareerRate(player, cfg.key, cutoffDate);
  } else {
    careerRate = player.career ? player.career[cfg.key] : null;
  }
  if (careerRate != null && weights.career > 0) {
    return { base: weights.career * careerRate + (1 - weights.career) * base, careerRate };
  }
  return { base, careerRate };
}

/* ============================================================
   POINT-IN-TIME MODEL — used only for Past Results, so the
   "projection" shown for a match reflects only data that existed
   before that match was played, not the final season averages.
   Derived entirely from pastMatches (which already has dated,
   per-player K/D/A for every completed match) — no extra
   scraping needed. Uses the same recencyWeightedRate() as the
   live model above, just with cutoffDate set to the match's own
   date instead of null.

   Known simplifications: kill-participation multiplier still uses
   full-season KP (per-match KP isn't captured in pastMatches), and
   the opponent-strength side (below) is a flat average rather than
   recency-weighted — base rate is the biggest lever and the one
   this feature targets; opponent-side recency would be a further
   refinement, not implemented here.
   ============================================================ */

function pointInTimeTeamStat(pastMatches, team, statKey, cutoffDate) {
  // "opponent's kills" and "opponent's deaths" are two different vantage
  // points on the same match: a team's deaths = the *other* team's kills
  // in that game, so statKey="d" sums the opponent's actual k values, and
  // statKey="k" sums the team's own actual k values.
  let total = 0, games = 0;
  for (const m of pastMatches) {
    if (cutoffDate !== null && (!m.date || m.date >= cutoffDate)) continue;
    const opp = m.teamA === team ? m.teamB : m.teamB === team ? m.teamA : null;
    if (!opp || !m.actual) continue;
    const sourceTeam = statKey === "d" ? opp : team;
    const sourceData = m.actual[sourceTeam];
    if (!sourceData) continue;
    for (const playerName in sourceData) {
      const val = getActualStat(m, sourceTeam, playerName, "k"); // team-level kills, either own or conceded
      if (typeof val === "number") total += val;
    }
    games += mapsCountedFor(m); // real map count — a Bo5 sums 3 maps, a CS2 Bo1 sums 1
  }
  return games > 0 ? total / games : null;
}

function pointInTimeLeagueAvgStat(pastMatches, teams, statKey, cutoffDate) {
  const rates = Object.keys(teams)
    .map((t) => pointInTimeTeamStat(pastMatches, t, statKey, cutoffDate))
    .filter((r) => r !== null);
  if (rates.length === 0) return null;
  return rates.reduce((s, r) => s + r, 0) / rates.length;
}

/* ============================================================
   LANE-SPECIFIC OPPONENT ADJUSTMENT — a player's kills/deaths are
   compared against the specific opponent in their own role (e.g.
   your jungler vs. their jungler), not a team-wide average that
   mixes in the enemy support's low death rate with the enemy
   jungler's. A real diagnostic (scripts/optimize_weights.py
   --diagnose-opponent) found the team-wide signal was real but weak
   and didn't strengthen with more data — pointing at the comparison
   itself being too blunt, not at opponent strength being irrelevant.

   Falls back to the team-wide functions above whenever a lane-
   specific read isn't possible: no role data at all (Valorant),
   no opposing player currently on record for that role, or not
   enough matches yet to compute a stable rate. STAT_TYPES.laneSpecific
   controls which stats even attempt this — assists stay team-wide,
   since they come from the whole team's kills, not a single lane
   matchup (confirmed by that same diagnostic: assists had the
   *strongest* team-wide signal of the three stats).
   ============================================================ */

function pointInTimePlayerNamesStat(pastMatches, team, playerNames, statKey, cutoffDate) {
  let total = 0, games = 0;
  for (const m of pastMatches) {
    if (cutoffDate !== null && (!m.date || m.date >= cutoffDate)) continue;
    if (!m.actual || !m.actual[team]) continue;
    for (const name of playerNames) {
      const val = getActualStat(m, team, name, statKey);
      if (typeof val === "number") {
        total += val;
        games += mapsCountedFor(m); // real map count, as above
      }
    }
  }
  return games > 0 ? total / games : null;
}

function pointInTimeLeagueAvgForRole(pastMatches, teams, role, statKey, cutoffDate) {
  const rates = [];
  for (const teamName in teams) {
    const roleNames = teams[teamName].players.filter((p) => p.role === role).map((p) => p.name);
    if (roleNames.length === 0) continue;
    const r = pointInTimePlayerNamesStat(pastMatches, teamName, roleNames, statKey, cutoffDate);
    if (r !== null) rates.push(r);
  }
  return rates.length > 0 ? rates.reduce((a, b) => a + b, 0) / rates.length : null;
}

function laneOpponentMultiplier(pastMatches, teams, player, opponentTeam, oppStrength, oppBasisKey, cutoffDate) {
  if (!player.role || !teams[opponentTeam]) return null; // no role data (Valorant) — signal caller to fall back
  const opponentRoleNames = teams[opponentTeam].players.filter((p) => p.role === player.role).map((p) => p.name);
  if (opponentRoleNames.length === 0) return null; // no current opponent on record for this role
  const oppStat = pointInTimePlayerNamesStat(pastMatches, opponentTeam, opponentRoleNames, oppBasisKey, cutoffDate);
  const leagueAvg = pointInTimeLeagueAvgForRole(pastMatches, teams, player.role, oppBasisKey, cutoffDate);
  if (oppStat === null || !leagueAvg) return null; // not enough data yet — fall back to team-wide
  return 1 + oppStrength * (oppStat / leagueAvg - 1);
}

function gameHasRoleData(teams) {
  // True if ANY player in this dataset has role info at all — used to
  // distinguish "this specific player is missing a role" (LoL/Valorant,
  // where a real diagnostic found team-wide as a FALLBACK for that case
  // specifically to be weak-to-negative — see resolveOpponentMultiplier)
  // from "this game doesn't track roles at all" (CS2 — role is always
  // null for every player, confirmed via direct inspection, not an edge
  // case). The original neutral-over-team-wide-fallback finding was
  // measured before CS2 existed in this app at all, so it was never
  // actually validated for a game with zero role data; applying it there
  // anyway made `opponent` completely inert for CS2 kills/deaths without
  // that ever being a deliberate, tested decision.
  return Object.values(teams).some((team) => team.players.some((p) => p.role));
}

function resolveOpponentMultiplier(teams, pastMatches, player, opponentTeam, oppStrength, cfg, cutoffDate) {
  if (cfg.laneSpecific && gameHasRoleData(teams)) {
    // Only reached for games that actually track roles (LoL, Valorant).
    // For lane-specific stats (kills, deaths) there, a real diagnostic
    // (scripts/optimize_weights.py --diagnose-opponent) found the
    // team-wide fallback signal is weak-to-negative on its own — using
    // it as a fallback for the occasional player missing a role match
    // was silently cancelling out the real lane-specific signal, since
    // a single opponent weight applies uniformly across both
    // populations. Neutral (no adjustment) beats a fallback we've
    // specifically measured to be unreliable, for THIS case
    // specifically.
    const laneMult = laneOpponentMultiplier(pastMatches, teams, player, opponentTeam, oppStrength, cfg.oppBasis, cutoffDate);
    return laneMult !== null ? laneMult : 1;
  }
  // Team-wide path — used for assists always (laneSpecific=false), AND
  // now for kills/deaths in any game with NO role data at all (CS2).
  // That second case used to silently fall through to the branch above
  // and always return neutral (1), since laneOpponentMultiplier
  // immediately bails when player.role is falsy — which is EVERY CS2
  // player, always, confirmed via direct inspection, not an edge case.
  // That meant `opponent` was completely inert for CS2 kills/deaths,
  // undocumented and unintended, not a deliberate decision the way the
  // neutral-fallback above actually was for LoL/Valorant.
  if (cutoffDate === null) {
    return opponentMultiplier(teams, opponentTeam, oppStrength, cfg.oppBasis);
  }
  const oppStatPT = pointInTimeTeamStat(pastMatches, opponentTeam, cfg.oppBasis, cutoffDate);
  const leagueAvgPT = pointInTimeLeagueAvgStat(pastMatches, teams, cfg.oppBasis, cutoffDate);
  const oppStat = oppStatPT !== null ? oppStatPT : teamStatPerGame(teams, opponentTeam, cfg.oppBasis);
  const leagueAvg = leagueAvgPT !== null ? leagueAvgPT : leagueAvgStat(teams, cfg.oppBasis);
  if (oppStat === null || !leagueAvg) return 1;   // unknown opponent — neutral, as above
  return 1 + oppStrength * (oppStat / leagueAvg - 1);
}

function projectPointInTime(pastMatches, teams, player, team, opponentTeam, games, weights, cutoffDate, statType, matchPatch) {
  const cfg = STAT_TYPES[statType];
  const refPatch = parsePatch(matchPatch); // the patch THIS match was played on, not a later one
  const pt = recencyWeightedRate(pastMatches, team, player.name, cfg.key, cutoffDate, weights.recencyHalfLife, refPatch, weights.patchDiscount);
  const histRate = player.hist ? player.hist[cfg.key] : null;

  // Base rate: blend point-in-time (recency-weighted) current-split rate
  // with prior-split rate, same weighting the live model uses. Early in a
  // split (no prior games yet) there's nothing to blend, so fall back to
  // prior-split alone, and only to the final-season number as a last
  // resort (true cold start, no data at all).
  let recentFormRate;
  if (pt.rate !== null) {
    recentFormRate = histRate !== null ? weights.history * histRate + (1 - weights.history) * pt.rate : pt.rate;
  } else {
    const curFallbackPT = player.cur ? player.cur[cfg.key] : undefined;
    recentFormRate = histRate !== null ? histRate
                   : (typeof curFallbackPT === "number" ? curFallbackPT : null);
    if (recentFormRate === null) return null;  // see project() — no rate for this stat
  }
  const { base, careerRate } = applyCareerTier(recentFormRate, player, weights, cfg, cutoffDate);

  const oppMult = resolveOpponentMultiplier(teams, pastMatches, player, opponentTeam, weights.opponent, cfg, cutoffDate);

  // KP multiplier is the one piece still using full-season data — see note above.
  const kpMult = cfg.useKP ? kpMultiplier(player, weights.history, weights.kp, teams, pastMatches, team, cutoffDate) : 1;

  const blendedPT = blendShareTier(base * oppMult * kpMult, weights, pastMatches, teams,
                                   team, player.name, cfg.key, cutoffDate);
  const shrunkPT = shrinkToPrior(blendedPT.perGame, weights, pt.games || 0, pastMatches,
                                 teams, cfg.key, cutoffDate);
  const perGame = shrunkPT.perGame;
  return { base, recentFormRate, careerRate, careerWeight: weights.career, oppMult, kpMult,
           shareTier: blendedPT.shareTier, sharePct: blendedPT.sharePct, shareWeight: weights.share || 0,
           shrunkTo: shrunkPT.shrunkTo, shrinkPull: shrunkPT.shrinkPull,
           perGame, total: perGame * games, priorGames: pt.games };
}

// Per-stat-type defaults, tuned via a real backtest against historical
// results (scripts/optimize_weights.py) rather than picked by feel.
// The `opponent` weight went through a real investigative arc worth
// knowing: a first pass found team-wide opponent comparison carried no
// usable signal (opponent=0), which held up under a noise-vs-data-volume
// diagnostic. Switching to a LANE-SPECIFIC comparison (a player vs. their
// direct positional opponent, not the whole enemy roster) initially still
// converged to opponent=0 — not because lane-specific data was uninformative
// (a --diagnose-opponent run split by lane-specific-vs-fallback rows showed
// real, positive signal in the lane-specific subset), but because the model
// was falling back to the same weak/negative team-wide signal whenever
// lane-specific data wasn't available (~63% of rows), which cancelled out
// the real signal in the other ~37%. Changing the fallback to neutral (no
// adjustment) instead of team-wide finally let the lane-specific signal
// through: opponent settled at a genuine non-zero value for both kills and
// deaths. Assists intentionally stays on team-wide comparison throughout —
// the diagnostic found assists' signal lives at the team level, not the lane
// level, which tracks with assists coming from the whole team's kills.
// Assists' patchDiscount is still an interpolated placeholder (the exact
// searched value never got captured) — replace with the real number from a
// fresh `--stat assists` run whenever convenient; low-stakes since
// patchDiscount only matters when cross-patch data exists at all.
/* Per-GAME, per-stat weights, all measured via scripts/optimize_weights.py
   backtesting against real accumulated match data — not guessed. A single
   shared set was previously used across all three games, which was a real
   source of inconsistency: the optimal weights differ substantially by
   game (e.g. `opponent` wants 0.2-0.4 for LoL but 1.0 for Valorant
   kills/deaths, and 0.0 for Valorant assists).

   Measured improvements vs. the old shared defaults:
     LoL      kills +8.3%,  deaths +17.1%, assists +12.7%
     Valorant kills +2.1%,  deaths +3.1%,  assists +16.8%
     CS2      kills +38.0%, deaths +29.4%, assists +20.3% (n=530, confirmed with both fixes below live)

   NOTE on CS2, TWO real bugs found and fixed, both changing what CS2's
   weights can actually do — confirmed via a real re-scrape + re-measure
   with both fixes live (the numbers above reflect that, not the
   original broken state):

   (1) `opponent` was COMPLETELY inert for kills/deaths, silently, this
   whole time — not a documented limitation, an actual bug.
   resolveOpponentMultiplier's lane-specific path requires player.role,
   which is null for EVERY CS2 player (CS2 doesn't track discrete
   positions the way LoL does) — so it always fell through to neutral
   (1), regardless of the opponent weight's value. Now routes CS2's
   lane-specific stats through the team-wide path instead (same one
   already used for assists), via gameHasRoleData() distinguishing "this
   game has no role data at all" (CS2) from "this specific player is
   missing a role" (the original, still-valid LoL/Valorant finding for
   why team-wide as a FALLBACK was worse than staying neutral there).

   (2) `kp` was hardcoded to 0 for every CS2 player in scrape_cs2.py,
   despite being genuinely computable from data already fetched —
   players_stats returns all 10 players' kills/deaths/assists per map
   with team labels, so kill participation is just (kills+assists) /
   team's total kills for that map, the same math LoL already uses.
   Now computed for real.

   A real, notable side-finding from the confirmed re-measurement:
   `opponent` actually working for the first time made the DEFAULT value
   (1.0, the naive starting point) measurably WORSE for CS2 kills/assists
   than not adjusting at all — the search moved it all the way to 0.0 for
   those two stats. Most likely explanation: CS2's discovered-team pool
   is smaller and more dynamic than LoL's (many thinly-sampled backfilled
   teams, per this project's own CS2 data-pipeline history), so
   team-strength estimates are noisier there — a strong opponent
   adjustment amplifies that noise rather than correcting for it. This
   is a real, deliberate result, not a leftover default.

   `history` and `patchDiscount` remain genuinely inert for CS2 (hist is
   still null — no Spring/Summer-style split exists in its continuous
   tournament calendar) — that part of the original finding still holds. */
/* Per-GAME, per-stat weights, all measured via scripts/optimize_weights.py
   backtesting against real accumulated match data — not guessed. A single
   shared set was previously used across all three games, which was a real
   source of inconsistency: the optimal weights differ substantially by
   game (e.g. `opponent` wants 0.2-0.4 for LoL but 1.0 for Valorant
   kills/deaths, and 0.0 for Valorant assists).

   LoL weights include a `career` tier (see applyCareerTier's own
   detailed note for what this signal actually turned out to measure —
   in short, NOT genuine multi-season history at its optimal
   configuration, more likely a cleaner independently-sourced current-
   season number) — added and re-measured after the original per-game
   RE-MEASURED after tournament discovery quadrupled the LoL dataset
   (241 -> 967 series, ~7,400 backtested predictions across regular
   season, playoffs, cups and preseason events). The previous weights
   here were fit on roughly a quarter of the data and are superseded.
   Three changes are worth understanding rather than just accepting:

   - recencyHalfLife converged to 8 for ALL THREE stats (previously
     20/2/20 — i.e. two stats on "flat average" and one on a very sharp
     2-match decay). With a much longer history now available, a
     moderate decay beats both extremes, and the fact that three
     independent searches landed on the same value is a real signal
     rather than noise.
   - patchDiscount collapsed to 0.0 for kills and deaths (was 1.0). This
     follows directly from the bigger pool: with matches now spanning
     Lock-In through Summer Playoffs across many patches, most history
     is off-patch relative to any given target, so a full discount threw
     away nearly all of it. You cannot afford to discount off-patch
     games when the pool is a year wide.
   - career roughly halved (0.8/0.85/0.85 -> 0.4/0.5/0.3). This is the
     optimizer's answer to a real measured problem: career's benefit
     rises through a season and is actively NEGATIVE early on (see
     scripts/diagnose_lol_career_leakage.py). A sample-maturity ramp was
     built and tested as the "obvious" fix and was REJECTED by all three
     stats in favour of simply lowering the flat weight.

   That same diagnostic was originally built to test whether the career
   tier was LEAKING future data into backtests — the static career field
   has no cutoff filtering, and at SEASON_HALF_LIFE=0.05 it collapses to
   roughly the current-season aggregate. The data refuted that outright:
   career's benefit rises monotonically through the season (kills -1.1%
   -> +6.6%, assists -1.5% -> +10.8%), the opposite of the contamination
   signature. The structural leak is real but is not inflating the
   numbers, so a point-in-time rewrite is cleanup-on-principle, not a
   correctness emergency.

   Improvements reported by the search (+3.7% kills, +7.1% deaths, +3.4%
   assists) are measured against DEFAULT_WEIGHTS, which carries
   career:0.0 — they are NOT a claim of that much gain over the weights
   previously shipped here.

   One further hypothesis was tested and REJECTED: that mixing preseason
   and cup events (Lock-In, Kickoff, LCK/CBLOL Cup — 127 of 966 matches,
   13%) into the same history pool as regular season and playoffs was
   adding more noise than coverage, by analogy with the real
   tier-mismatch problem confirmed in CS2's career data.
   scripts/experiment_stage_filtering.py measured it properly, holding
   the evaluation set fixed (n=6631 identical across variants) and
   varying only the history pool. Keeping everything won for all three
   stats, and excluding more made it monotonically worse (kills 2.6556
   -> 2.6664, assists 4.8203 -> 4.8621). Volume beats purity here; the
   CS2 analogy did not transfer. Do not "clean up" the pool by stage. */
/* KP IS PINNED AT 0 FOR EVERY GAME AND STAT, AND THAT IS A MEASUREMENT.

   kpMultiplier used to read player.cur.kp — a whole-season figure with no
   cutoff awareness — so a backtest of a match in May was handed a kill
   participation partly built from games played in August. Quantified by
   comparing the season figure against its own leave-one-out version, on
   the question it actually answers, "what will this player's KP be in
   this match":

       season aggregate, as shipped (leaks)          2.72pp error
       season aggregate, leave-one-out               3.37pp
       point-in-time, recency-weighted, shrunk       3.32pp
       point-in-time, raw                            3.86pp

   So the leak was worth 0.66pp, about a quarter of the estimator's
   apparent accuracy. Rerunning the weight search with a leak-free KP,
   every variant converged on the same place — CS2 kills scored 6.0302
   with the layer off and 6.0300 at its best leak-free setting, which was
   kp 0.2 shrunk so hard toward the league that the multiplier is ~1.
   Same on both assists: the best leak-free kp was 0.0.

   The knockout report used to credit kp with +1.49% on CS2 kills and call
   it "carries the model". It was not carrying anything. It was reading
   the future. The honest CS2 kills number is 6.0302, not 5.9418, and that
   1.49% is a correction rather than a regression.

   The layer is kept, not deleted, and it is now point-in-time — so if CS2
   per-match history ever gets deep enough for a player's own KP to beat
   the league average (median prior sample today: 2 matches), the search
   can turn it back on without reintroducing the leak. */
const DEFAULT_WEIGHTS_BY_GAME_AND_STAT = {
  // LoL: re-derived with walk-forward validation rather than by minimising
  // error over the whole season at once. The previous per-stat values were
  // chosen in-sample, which on this data is measurably optimistic — the
  // search reported 2.7176 MAE for kills where the same weights score
  // 2.7730 on folds they were not fitted to.
  //
  // Three parameters carry the model out-of-sample (turning each off, in
  // MAE terms): career +1.8/+2.7/+1.7%, opponent +1.5/+1.3/+3.1%, history
  // +0.5/+0.6/+1.4%. kp and patchDiscount move it by under 0.1% in either
  // direction on every stat, so they are pinned at 0 rather than left
  // holding a value the search fitted to noise.
  //
  // One shared set now beats the three separately tuned ones, which is
  // what per-stat overfitting looks like from the outside. Measured
  // out-of-sample over 6 walk-forward folds: kills -0.40% (4/6 folds),
  // deaths -1.00% (5/6), assists -0.64% (5/6); it also improves 6/7, 5/7
  // and 7/7 regions respectively, and holds on the earliest quarter of the
  // season, which no fold selection touched. Reproduce with:
  //   python scripts/dev/optimize_weights.py --game lol --validate
  //
  // THE SAME TEST WAS RUN ON CS2 AND VALORANT AND BOTH KEEP THEIR THREE
  // SETS (scripts/dev/shared_weight_set.py). This is a LoL property, not
  // a general one. On CS2 a shared set is worse even on the folds it was
  // fitted to, because that game's stats want structurally different
  // things -- kills a role adjustment, deaths team pace, assists
  // neither. On Valorant the search found 0.6% in-sample by switching
  // `history` off entirely, a tier that costs +4.0% when removed, and it
  // inverted on the holdout: kills +0.88%, deaths +1.27%.
  lol: {
    kills: { history: 0.8, opponent: 0.4, kp: 0.0, recencyHalfLife: 8, patchDiscount: 0.0, career: 0.6, share: 0.0, shrink: 0.0 },
    deaths: { history: 0.8, opponent: 0.4, kp: 0.0, recencyHalfLife: 8, patchDiscount: 0.0, career: 0.6, share: 0.0, shrink: 0.0 },
    assists: { history: 0.8, opponent: 0.4, kp: 0.0, recencyHalfLife: 8, patchDiscount: 0.0, career: 0.6, share: 0.0, shrink: 0.0 },
    headshots: { history: 0.0, opponent: 0.0, kp: 0.0, recencyHalfLife: 20, patchDiscount: 0.0, career: 0.0, share: 0.0, shrink: 0.0 },  // unreachable — STAT_TYPES.headshots is CS2-only; present so the per-game lookup never returns undefined
  },
  // Valorant: validated out-of-sample and deliberately UNCHANGED. Every
  // candidate was rejected (higher history +0.61% winning 0/6 folds, flat
  // recency +0.00%, zeroing kp/patchDiscount +0.02%). history carries this
  // game — removing it costs +4.0% on kills and deaths — and is already
  // weighted for that. A null result is still a result.
  valorant: {
    // THE SHARE TIER IS THIS GAME'S FIRST REAL SECOND SIGNAL. Before it,
    // the knockout report said history carried Valorant alone: opponent
    // 0.0, career 0.0 (no scraper exists for it), kp inert, recency inert.
    // Turning history off cost +4.0% and turning anything else off cost
    // nothing, which is another way of saying the model was a recency-
    // weighted average with decoration. Measured out-of-sample over 6
    // walk-forward folds, the tier is worth -4.10% on deaths (6/6 folds),
    // -1.41% on kills (5/6) and -1.60% on assists (5/6), and it moves
    // deaths' calibration from 0.996x to exactly 1.000x. See the long
    // note on SHARE_HALF_LIFE for why the pace term is the league's and
    // not the team's.
    //
    // RE-MEASURED after fixing a real bug that made the kp weight
    // STRUCTURALLY INERT for this entire game: scrape_valorant.py wrote
    // kp as a literal 0 for every player, and kp_multiplier()'s
    // `if not cur_kp: return 1.0` guard then fired every single time, so
    // no kp value the optimizer picked could ever change a prediction.
    // The previously shipped numbers here (kp: 0.3 across all three)
    // were therefore fitted around a dead parameter. This is the same
    // bug already found and fixed on the CS2 side.
    //
    // That the fix took effect is visible in the result itself: the
    // three stats now choose DIFFERENT kp values (0.0 / 0.3 / 0.1).
    // While kp was inert every candidate scored identically, so ties
    // resolved to the same value for all three — divergence is the
    // signature of the search responding to real signal.
    //
    // opponent: 0.0 on all three is measured, matching the same result
    // CS2 showed. Valorant has no per-player role data either (role is
    // None), so the lane-specific opponent path can't engage and it
    // falls back to a team-wide estimate built on a thin sample.
    //
    // career: 0.0 because Valorant is now the ONLY game without its own
    // career-history scraper (LoL has scrape_career.py, CS2 has
    // scrape_cs2_career.py). It is a clean no-op rather than a tuned
    // value — applyCareerTier() falls back to the base rate whenever a
    // player carries no career data — so this stays 0.0 until a
    // Valorant equivalent exists, at which point it needs measuring
    // rather than assuming.
    // These are the SECOND measurement. The first was taken with the kp
    // fix live but before a separate parsing bug was found, which was
    // silently discarding rows whose stats rendered without the
    // attack/defense split (see scrape_valorant.py). Fixing that
    // recovered 90 predictions in VCT China (n 549 -> 639) and improved
    // that region's MAE markedly (kills 6.4953 -> 6.1139, deaths 4.5593
    // -> 4.1734), so the earlier weights were fit on an incomplete
    // sample and are superseded.
    kills: { history: 0.7, opponent: 0.0, kp: 0.0, recencyHalfLife: 8, patchDiscount: 0.0, career: 0.0, share: 0.4, shrink: 4.0 },
    deaths: { history: 0.7, opponent: 0.0, kp: 0.0, recencyHalfLife: 6, patchDiscount: 0.3, career: 0.0, share: 0.7, shrink: 0.0 },  // kp is dead weight for deaths (useKP: false) — see the cs2 note below
    // ASSISTS RE-FIT against the deeper history (history_matches: the
    // prior event's maps, which the scraper used to discard). Median
    // per-player depth went 8 -> 12 maps, and the weights that suited
    // eight do not suit twelve: share 0.4 -> 0.5, shrink 1.0 -> 4.0,
    // patchDiscount 0.8 -> 1.0, history 0.4 -> 0.3. The last one is the
    // tell — the prior event's matches are now IN the pool, so the tier
    // that summarises them is worth less.
    //
    // Found by greedy search on the first four folds and judged on the
    // last two, which the search never saw: -1.31% where it looked and
    // -0.49% where it did not, 2/2 held-out folds. Then the repo's
    // ordinary adoption test on the whole season, which it passes at
    // every fold count tried: 4/4, 4/6, 7/8, 7/10, about -1.0% each
    // time.
    //
    // Both sets of numbers were re-taken after a de-duplication bug was
    // found (the first run after match_id landed put every current
    // match in the history list as well, since the stored copy had no
    // id to match on). The candidate survived it; the claim that deeper
    // history helps DEATHS did not -- at shipped weights that was
    // -1.47% over 5/6 folds with the duplicates and is -0.17% over 2/6
    // without. Assists is the one stat the extra depth actually moves.
    //
    // KILLS AND DEATHS WERE SEARCHED THE SAME WAY AND REJECTED. Both
    // looked better than this on the folds they were fitted to (kills
    // -0.52%, deaths -2.56%) and neither survived the holdout: kills
    // +0.00%, deaths +0.67% WORSE, 1/2 held-out folds each. A few
    // hundred candidates against six folds will manufacture a majority
    // roughly a third of the time, which is what a search reporting its
    // own score looks like. They stay as they are.
    assists: { history: 0.3, opponent: 0.0, kp: 0.0, recencyHalfLife: 6, patchDiscount: 1.0, career: 0.0, share: 0.5, shrink: 4.0 },
    headshots: { history: 0.0, opponent: 0.0, kp: 0.0, recencyHalfLife: 20, patchDiscount: 0.0, career: 0.0, share: 0.0, shrink: 0.0 },  // unreachable — STAT_TYPES.headshots is CS2-only; present so the per-game lookup never returns undefined
  },
  cs2: {
    // All three stats now measured with THREE real fixes live: kp
    // actually computed (was hardcoded to 0), opponent adjustment
    // actually functioning for kills/deaths (was silently always
    // neutral due to the role-data dependency bug), and — the biggest
    // one — a genuine point-in-time-aware career tier (a real, confirmed
    // leakage bug initially made career look artificially strong: it was
    // a static snapshot with no cutoff-date awareness, so a historical
    // backtest prediction could be fed a career number partly built from
    // games that hadn't happened yet as of that prediction's own date).
    //
    // The leakage fix was verified two ways before trusting these
    // numbers, not just assumed correct from logic: (1) direct
    // inspection of real matches confirmed every included career game is
    // genuinely before the cutoff date, every excluded one genuinely at
    // or after it; (2) career_rate correlates only moderately (0.578)
    // with the model's existing pt_rate signal, not near-1.0 — ruling
    // out "it's just a cleaner copy of the same thing" and, combined
    // with (1), ruling out residual leakage. Most likely real
    // explanation: career_rate (60-day decay, sourced from a complete
    // dedicated player-history query) and pt_rate (games-based decay,
    // limited to this app's own team-discovery dataset) are measuring
    // related but genuinely different things, and career_rate's data is
    // simply more complete. Deaths settling at 0.95 rather than exactly
    // 1.0 (unlike kills/assists) is a real, differentiating signal that
    // the search isn't just blindly maxing out every dimension.
    //
    // Real gains vs. the pre-any-of-this-session's-CS2-fixes baseline:
    // kills +29.5%, deaths +31.5%, assists +19.6% (n=1666). Lower than
    // an earlier intermediate measurement (+42.7%/+40.9%/+30.6%), which
    // is a HONEST, expected result, not a regression: that earlier
    // number was measured while kp_mult was still silently broken (see
    // below) and deflating BOTH the baseline and the "best" search
    // uniformly, and while career data still had a null-stat
    // contamination bug pulling some players' numbers down. Fixing both
    // moved the baseline up too, shrinking the relative improvement
    // even though the ABSOLUTE predictions got more accurate. Confirmed
    // via a real prediction-breakdown diagnostic: the original
    // systematic UNDER-prediction (every single miss in a live
    // screenshot going the same direction) is gone -- diffs now show a
    // genuine mix of over/under misses, the real signature of normal
    // match-to-match variance rather than a fixable model bias.
    //
    // Two real, confirmed bugs found and fixed via that live screenshot
    // report + breakdown diagnostic, on top of everything above:
    //
    // (1) kp_mult was silently ~0.9 (kills) to ~0.7 (deaths) for EVERY
    // CS2 player regardless of their real kill participation --
    // kp_multiplier's team_avg_kp=66.0 constant is calibrated for LoL's
    // 0-100 percentage convention, but CS2's kp was a 0-1 fraction.
    // Fixed by scaling CS2's kp to the same 0-100 convention at the
    // source (scrape_cs2.py), not by adding game-aware branching here.
    //
    // (2) career_games could include fake zero-kill games from matches
    // bo3.gg hadn't finished processing yet (confirmed earlier this
    // project as a real phenomenon) -- scrape_cs2_career.py now
    // explicitly excludes null stat rows instead of silently coalescing
    // them to 0 via `or 0`, which had been treating "not ready yet" the
    // same as "genuinely 0 kills".
    //
    // A THIRD real issue was investigated but is NOT fixed: career_games
    // draws from EVERY match a player has played, with no tier/star
    // filtering the way cur/pt_rate get from the main pipeline -- live
    // reconnaissance confirmed bo3.gg's player-scoped /matches endpoint
    // simply doesn't expose tier/star data via any tested `with=`
    // expansion (tournament objects only carry id/image_url/
    // last_match_date). Given the systematic bias resolved without this
    // fix, it's a real but lower-priority remaining gap, not an active
    // problem.
    //
    // opponent:0.0 across ALL THREE stats is now MEASURED, not inferred.
    // --diagnose-opponent on real CS2 data (n=774) gives
    // corr(opponent deviation, prediction residual) of just +0.015 for
    // kills and +0.028 for assists — i.e. essentially no usable signal,
    // so a zero weight is correct rather than a shrug. Deaths is the one
    // exception at +0.113: weak but genuinely positive and pointing the
    // right way, yet the weight search still landed on 0.0, which says
    // the current multiplicative opponent form isn't capturing even that
    // much. Likely root cause for all three: the average opponent-
    // strength estimate rests on only ~8 prior games, which is thin
    // enough that the estimate's own noise swamps a real effect that
    // small. So the honest read is "not enough data per opponent yet",
    // not "opponent strength doesn't matter in CS2" — worth re-checking
    // as the tracked pool grows, with deaths the most likely first stat
    // to turn on. One caveat on those numbers: --diagnose-opponent
    // computes its base with DEFAULT_WEIGHTS (LoL-shaped) rather than
    // the measured CS2 weights, which adds noise to the residual; the
    // near-zero kills/assists results are almost certainly robust to
    // that, deaths' +0.113 less so.
    //
    // A separate, real, confirmed bug was also fixed here
    // (teamStatPerGame/team_stat_per_game summing every historical
    // player for a team uncapped while only capping the divisor at 5)
    // via a shared likelyStarters()/likely_starters() helper. That fix
    // barely moved these numbers on its own.
    //
    // RE-DERIVED OUT-OF-SAMPLE (walk-forward; see the LoL note above).
    //
    // recencyHalfLife was the big one. Deaths shipped at 2 — decay sharp
    // enough that a player's last couple of maps dominated everything else
    // — and turning it off is worth -3.52%, winning every fold. It helps
    // kills and assists too. CS2 plays in dense tournament blocks rather
    // than a weekly season, so "recent" and "a fortnight ago" are often the
    // same event; heavy decay threw away sample for no gain.
    //
    // career for KILLS was actively harmful at its shipped maximum of 1.0:
    // removing it entirely measured -0.95%. It sits at 0.5 rather than 0
    // because 0 measured only marginally better (-1.43% vs -1.28% combined
    // with the recency change, inside noise at this sample size) and CS2
    // career coverage is still partial (148/285 players matched on the last
    // run). Re-validate as that coverage improves.
    //
    // history and patchDiscount are pinned at 0 because they are
    // STRUCTURALLY inert here, as the note above already explains: CS2
    // players carry hist=None. Zeroing them changes no prediction —
    // measured at exactly +0.00% across every fold — and stops the shipped
    // values implying they were tuned.
    //
    // Caveat: CS2's validation window is short (795 rows, folds from
    // 2026-09-10) because its history only recently deepened. Lower
    // confidence than the LoL numbers.
    //
    // KILLS RE-DERIVED AGAIN after the kp baseline was fixed — see the
    // long note on leagueAvgKP. Every number in the paragraphs above was
    // measured while `kp` could only ever SUBTRACT in this game (the
    // baseline was LoL's 66 against a CS2 scale of ~26), so the search
    // was tuning the rest of the model around a dent it could not name.
    // Two of those conclusions do not survive the fix:
    //
    //   kp 0.1 -> 0.3. It was small because a bigger value meant a bigger
    //     across-the-board haircut. Two-sided, it earns its keep.
    //   career 0.5 -> 1.0. The note above records career:1.0 as "actively
    //     harmful" for kills. It was not: at 1.0 it pulled predictions
    //     UP, straight into the kp layer's flat ~6% cut, and the search
    //     read the resulting overshoot as career's fault.
    //
    // recencyHalfLife also drops 20 -> 6, reversing the "decay threw away
    // sample" finding for kills only (deaths and assists keep it).
    // Measured out-of-sample over 6 walk-forward folds: -4.44% MAE,
    // winning 6/6 folds, with calibration at 1.011x. Reproduce with:
    //   python scripts/dev/optimize_weights.py --game cs2 --stat kills --validate \
    //     --candidate '{"history":0.0,"opponent":0.0,"kp":0.3,"recencyHalfLife":6,"patchDiscount":0.0,"career":1.0}'
    // (the search returned history 0.3 / patchDiscount 0.4; both are
    // structurally inert here, so they stay pinned at 0 per the note
    // above — verified to produce bit-identical predictions.)
    //
    // ASSISTS was offered the same candidate and REJECTED it out-of-sample
    // (-0.37%, winning 3/6 folds), so it is deliberately unchanged. The kp
    // fix already moved its calibration from 0.944x to 1.004x, which was
    // the actual defect; a coin-flip MAE change is not a reason to churn.
    //
    // DEATHS carries kp: 0.0 because STAT_TYPES.deaths sets useKP: false.
    // kpMultiplier is never called for this stat, so the 0.3 that sat
    // here was a dead number the search "fitted" against a parameter it
    // could not move. Zeroing it changes no prediction; it stops the
    // table claiming a tuning that never happened. (Same for Valorant.)
    // shrink raised from 0 after the CS2 roster roughly doubled. The
    // scraper used to rebuild its team list from one page of the global
    // feed each run; once past matches carried over, 58 teams of
    // thin-history players joined the roster, and pulling a thin sample
    // toward the league rate went from worthless to the largest single
    // accuracy gain on this game. Walk-forward, 6 folds, adopted on the
    // repo's majority rule:
    //   kills   k=0 -> 8: 6/6 folds, MAE 6.7073 -> 6.1344 (-8.54%); every
    //   k from 2 to 12 wins 6/6, so this is a plateau rather than a point.
    // KP CAME ALIVE for kills once the career tier was actually being
    // written. It measured 0.0 before, and that was honest at the time:
    // 1,124 of 1,404 players had no career record, so the base rate this
    // multiplier scales was mostly noise and scaling noise by role does
    // nothing. With an independent per-game history behind 98% of
    // players at a median of 41 games, there is a real number to adjust.
    //
    // It is a plateau, not a knife edge -- 6/6 folds at EVERY value from
    // 0.1 to 0.8, improving monotonically (-0.11% to -0.56%) -- and it
    // holds at every fold count tried: 4/4, 6/6, 7/8, 6/9.
    //
    // A share weight for kills came out of the same sweep at 4/6 and was
    // REJECTED: 1/4 at four folds and 4/8 at eight, which is fold-
    // boundary luck rather than signal.
    // SHRINK LOWERED 8 -> 4, and the reason is not MAE.
    //
    // The note above already says every k from 2 to 12 won 6/6 folds -- "a
    // plateau rather than a point" -- so 8 was picked from the top of a flat
    // region. What nobody measured is what sitting up there costs, because
    // MAE cannot see it: pulling a projection toward the league mean always
    // lowers absolute error when the signal is noisy. That is what shrinkage
    // is FOR. So a search that only minimises MAE will happily flatten the
    // model until it barely distinguishes players, and report an improvement
    // the whole way.
    //
    // It had. Measured per map over 5,692 point-in-time rows:
    //
    //   sd(projection)                    0.906
    //   sd(each player's own long-run mean)  1.914   <- the spread that exists
    //   ratio                              0.47
    //
    // CS2 has about 5.4 series per team on record, so priorGames sits near 6,
    // and k=8 pulls 8/(6+8) = 57% of every projection to the league average.
    // More than half the number was the league, not the player.
    //
    // That is fatal for this app's actual job. Ranking props is ENTIRELY a
    // question of between-player spread: with none, the projection is a
    // constant, the "edge" is just the line's own deviation from average, and
    // ranking by edge ranks the market's information rather than ours. On 616
    // graded CS2 kills lines across 71 matches the market split 52/48 over,
    // and this model projected OVER on 37% of them -- a fifteen-point
    // directional bias, on the stat that is 47% of the market.
    //
    //   k=4: 4/6 folds, MAE +0.01% (nothing), ratio 0.47 -> 0.61,
    //        projects over 37% -> 42%, picks right 51% -> 52%
    //   k=2: ratio 0.73 but only 3/6 folds, which fails the majority rule
    //   k=0: ratio 1.00 exactly, and 0/6 folds at +2.72% -- a real trade,
    //        not taken here, and recorded in scripts/dev/spread_check.py
    //
    // recencyHalfLife was swept alongside it and moves neither the ratio nor
    // the pick balance (0.61 and 42% at 6, 10, 14 and 20), so this is one
    // weight's problem and not the pair's.
    //
    // scripts/dev/spread_check.py measures all three numbers together and
    // tests/test_model_spread.py holds every stat with a posted market to a
    // floor, so the next MAE-driven sweep cannot quietly flatten it again.
    kills: { history: 0.0, opponent: 0.0, kp: 0.5, recencyHalfLife: 6, patchDiscount: 0.0, career: 1.0, share: 0.2, shrink: 4.0 },
    // deaths' share weight is UNDER REVIEW rather than settled. It was
    // adopted at -3.97% on 795 rows winning 4/6 folds; on the 911 rows
    // there are now, removing it measures -2.74%, which would make it
    // harmful. Neither direction wins most folds, and the per-fold split
    // says why: the whole reversal is one 60-row fold (2026-09-14, 5.949
    // vs 4.971). Strip that fold and the two are level.
    //
    // So it stays, on the grounds that churning a shipped weight on a
    // single small fold is the failure this file already warns about
    // elsewhere. Re-run the knockout as CS2's history deepens; if the
    // HARMFUL verdict survives a fold that is not carrying it alone, drop
    // it to 0.
    //   deaths  k=0 -> 4: 4/6 folds, MAE 4.6915 -> 4.5530 (-2.95%); 4/6 at
    //   every k tried, so the direction is steadier than the size.
    // DEATHS' SHRINK RE-FIT at 98% career coverage, 4.0 -> 3.0. The
    // career scrape had been writing nothing for weeks (two stacked
    // bugs), so 1,124 of 1,404 players had no career tier at all and
    // this weight was fitted to compensate for grounding that was
    // missing rather than absent by design. With the tier present at a
    // median of 41 games per player, less pull toward the prior is
    // right. Majority at every fold count tried: 3/4, 6/6, 7/8, 7/9,
    // -0.18% to -0.25% each time.
    //
    // Three other candidates came out of the same sweep and were
    // rejected for not holding across fold counts: kills career
    // 1.0 -> 0.8 (2/4, so not a majority where it matters most, and
    // -0.10% by 10 folds), deaths shrink 2.0 (bigger total gain, 4/8),
    // and assists shrink 6.0 (a majority everywhere but worth -0.01%
    // to -0.14%, which is not a reason to move a shipped weight).
    // SHARE COMES DOWN 0.6 -> 0.4 for the same reason the shrink did:
    // both were fitted while most players had no career grounding, so
    // the team-pace tier was carrying weight that now belongs to a real
    // per-player rate. The most robust value on offer rather than the
    // largest -- 0.2 and 0.3 score better in total (-1.43% and -1.27% at
    // eight folds) but 0.4 wins a majority at every granularity and is
    // PERFECT at the finer ones: 4/4, 6/6, 8/8, 9/9, -0.70% to -0.98%.
    deaths: { history: 0.0, opponent: 0.0, kp: 0.0, recencyHalfLife: 20, patchDiscount: 0.0, career: 1.0, share: 0.4, shrink: 3.0 },
    //   assists k=1 -> 8: 6/6 folds, MAE 2.9501 -> 2.8452 (-3.56%).
    //
    // headshots was tested the same way and NOT changed: k=2 wins 1/6
    // and k=16 wins 2/6, the sign flips across the range, and the best
    // reading is -0.94%. That is a knife edge, not a plateau.
    assists: { history: 0.0, opponent: 0.0, kp: 0.0, recencyHalfLife: 20, patchDiscount: 0.0, career: 1.0, share: 0.0, shrink: 4.0 },
    // HEADSHOTS, tuned out-of-sample the same way as everything else, on
    // 911 rows that exist only because a scrape run was asked what
    // bo3.gg's players_stats actually contains rather than assumed.
    //
    // Every parameter not named below is STRUCTURALLY inert for this stat
    // and pinned at 0 rather than left holding a fitted number: history
    // and patchDiscount because CS2 players carry hist=None and no patch,
    // kp because STAT_TYPES.headshots sets useKP false, and career because
    // cs2_career_data.json records k/d/a and no headshots at all. The
    // in-sample search happily returned kp 0.3 and career 0.1 for exactly
    // those three; none of them can move a prediction.
    //
    // That leaves shrink, which carries this stat by itself: the knockout
    // report puts it at +3.71% and everything else at noise. It has a
    // clear interior optimum at 3-4 rather than a grid edge — 8 and beyond
    // get steadily worse. Measured over 6 walk-forward folds against a
    // plain recency-weighted rate: -3.58%, winning 6/6, calibration
    // 1.001x. Reproduce with:
    //   python scripts/dev/optimize_weights.py --game cs2 --stat headshots --validate
    //
    // share is pinned at 0 although 0.2 scored 0.16% better, because that
    // margin won only 2 of 6 folds and the knockout calls it inert. The
    // rule this repo already applies to kp and patchDiscount applies here:
    // a parameter whose removal costs nothing still ships a value fitted
    // to noise. Recency is likewise flat — 6, 12 and 20 sit within 0.3pp
    // — and pinned at the no-decay end.
    headshots: { history: 0.0, opponent: 0.0, kp: 0.0, recencyHalfLife: 20, patchDiscount: 0.0, career: 0.8, share: 0.0, shrink: 3.0 },
  },
};

/* ============================================================
   SHARED UI PIECES
   ============================================================ */

function Slider({ label, value, onChange, min, max, step, format, tooltip }) {
  const theme = useTheme();
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: theme.textDim, marginBottom: 5, fontFamily: "'Inter', sans-serif" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          {label}
          {tooltip && (
            <span
              title={tooltip}
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 13, height: 13, borderRadius: "50%", border: `1px solid ${theme.textFaint}`,
                fontSize: 9, color: theme.textFaint, cursor: "help", flexShrink: 0,
              }}
            >
              i
            </span>
          )}
        </span>
        <span style={{ color: theme.accent, fontFamily: "'IBM Plex Mono', monospace" }}>{format ? format(value) : value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(parseFloat(e.target.value))} style={{ width: "100%", accentColor: theme.accent }} />
    </div>
  );
}

// Radar chart visualizing a projection's real components as a shape,
// not just a final number — inspired by a reference app's per-player
// spider chart, adapted to what THIS model actually computes rather
// than copying its exact axes. Each axis is normalized to "% above/
// below neutral" on a 0-100 scale (50 = no effect), so Recent Form and
// Career (raw per-game rates, whatever units the stat/game uses) sit on
// the SAME comparable scale as Opponent and KP (already-centered
// multipliers) — none of these axes share native units otherwise, so
// without this normalization the shape wouldn't mean anything.
// seasonAvg may be null — see ProjectionDetail. Every use below already
// guarded with `seasonAvg > 0`, which is false for null, so each axis
// falls back to its neutral 1 rather than producing NaN.
function projectionRadarAxes(r, seasonAvg) {
  const toRadar = (ratio) => Math.max(0, Math.min(100, 50 + (ratio - 1) * 100));
  const axes = [
    { label: "Recent Form", value: toRadar(seasonAvg > 0 ? r.recentFormRate / seasonAvg : 1) },
    { label: "Opponent", value: toRadar(r.oppMult) },
  ];
  if (r.careerRate != null && r.careerWeight > 0) {
    axes.splice(1, 0, { label: "Career", value: toRadar(seasonAvg > 0 ? r.careerRate / seasonAvg : 1) });
  }
  if (r.kpMult !== 1) {
    axes.push({ label: "KP", value: toRadar(r.kpMult) });
  }
  return axes;
}

function RadarChart({ axes, size = 140, color }) {
  const theme = useTheme();
  const c = size / 2;
  const R = size / 2 - 22; // leave room for axis labels
  const n = axes.length;
  if (n < 3) return null; // a radar shape needs at least 3 axes to mean anything
  const angleFor = (i) => (2 * Math.PI * i) / n - Math.PI / 2;
  const pointFor = (i, valuePct) => {
    const a = angleFor(i);
    const r = (valuePct / 100) * R;
    return [c + r * Math.cos(a), c + r * Math.sin(a)];
  };
  const polygonPoints = axes.map((ax, i) => pointFor(i, ax.value).join(",")).join(" ");
  const ringLevels = [25, 50, 75, 100];

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {ringLevels.map((level) => (
        <polygon
          key={level}
          points={axes.map((_, i) => pointFor(i, level).join(",")).join(" ")}
          fill="none"
          stroke={theme.steel}
          strokeWidth={level === 50 ? 1.2 : 0.6}
          strokeDasharray={level === 50 ? "2,2" : undefined}
        />
      ))}
      {axes.map((ax, i) => {
        const [x, y] = pointFor(i, 100);
        return <line key={ax.label} x1={c} y1={c} x2={x} y2={y} stroke={theme.steel} strokeWidth={0.6} />;
      })}
      <polygon points={polygonPoints} fill={`${color}33`} stroke={color} strokeWidth={1.8} />
      {axes.map((ax, i) => {
        const [px, py] = pointFor(i, ax.value);
        return <circle key={`pt-${ax.label}`} cx={px} cy={py} r={2.5} fill={color} />;
      })}
      {axes.map((ax, i) => {
        const [lx, ly] = pointFor(i, 118);
        return (
          <text key={`label-${ax.label}`} x={lx} y={ly} fill={theme.textFaint} fontSize={9}
                fontFamily="'IBM Plex Mono', monospace" textAnchor="middle" dominantBaseline="middle">
            {ax.label}
          </text>
        );
      })}
    </svg>
  );
}

// Auto-generated human-readable tags from the same real components the
// radar chart plots — same idea as a reference app's "High Volume" /
// "Shootout Script" tags, derived from OUR actual model output rather
// than separately-authored narrative text, so a tag can never say
// something the number itself doesn't support.
function projectionTags(r, theme) {
  const tags = [];
  if (r.careerRate != null && r.careerWeight >= 0.5) {
    const diff = r.careerRate - r.recentFormRate;
    if (Math.abs(diff) > 0.15 * Math.max(r.recentFormRate, 0.1)) {
      tags.push({ text: diff > 0 ? "Career baseline pulling up" : "Career baseline pulling down", color: diff > 0 ? theme.good : theme.bad });
    }
  }
  if (r.oppMult >= 1.08) tags.push({ text: "Favorable matchup", color: theme.good });
  else if (r.oppMult <= 0.92) tags.push({ text: "Tough matchup", color: theme.bad });
  if (r.kpMult >= 1.05) tags.push({ text: "High involvement", color: theme.good });
  else if (r.kpMult <= 0.95) tags.push({ text: "Low involvement", color: theme.bad });
  return tags;
}

// Labeled horizontal progress bar for one component of a projection —
// same idea as a reference app's "Baseline"/"Opportunity" bars, showing
// the actual value driving a number instead of hiding it inside one
// final total.
/* The evidence marker.

   Deliberately silent on a solid projection. A badge on every row is
   wallpaper -- it stops being read, and then it cannot warn anybody
   about the rows that need it. So this draws only when there is
   something to say, which makes its presence the signal.

   Says the count out loud rather than a word alone, because "thin" is
   an opinion and "3 games" is a fact the reader can weigh themselves. */
function EvidenceChip({ games, compact, borrowed }) {
  const theme = useTheme();
  const tier = evidenceTier(games);
  if (tier === null || tier === "solid") return null;
  const tone = tier === "thin" ? theme.bad : theme.textFaint;
  const rounded = Math.round(games);
  // Said differently for a borrowed context, because the number alone
  // would be read as "we only found this much" when the truth is "we
  // found plenty, from somewhere else".
  const title = borrowed
    ? `This event has no completed matches yet, so the projection is built from the `
      + `players' home-region form. That is real form against a different field, and `
      + `how much it transfers is not something this app has any results to measure. `
      + `Treated as a weaker read until the event has played.`
    : tier === "thin"
    ? `Only ${rounded} game${rounded === 1 ? "" : "s"} of evidence behind this projection. `
      + `Measured on past seasons, the model is about 5-12% less accurate than its own average below four games.`
    : `${rounded} games of evidence behind this projection. `
      + `The model is around its own average accuracy in this range, and better from eight games up.`;
  return (
    <span className="kp-chip kp-num" title={title}
          style={{ background: `${tone}1A`, color: tone, border: `1px solid ${tone}33` }}>
      {borrowed ? (compact ? "other event" : "form from another event")
                : (compact ? `${rounded}g` : `${rounded}g evidence`)}
    </span>
  );
}

function ScoreBar({ label, value, max, unit, color }) {
  const theme = useTheme();
  // A bar with nothing to show draws nothing. It used to call .toFixed on
  // whatever it was handed, so one undefined number took the whole page
  // down — React unmounts the tree on a render throw, so the expanded card
  // did not lose a row, it lost everything. Real case: a CS2 player with
  // headshots in their match history but no season rate yet, which made
  // every projection work and this one line throw.
  if (typeof value !== "number" || !isFinite(value)) return null;
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: theme.textFaint, marginBottom: 3 }}>
        <span>{label}</span>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", color: theme.textDim }}>{value.toFixed(1)} {unit}</span>
      </div>
      <div style={{ height: 5, background: theme.steelSoft, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
    </div>
  );
}

// Shared detail view for an expanded player projection — radar chart,
// score decomposition, tags, and (LoL) champion cheat code. Used by
// BOTH PlayerRow (Matchup/Custom Match exploration tab) and
// MatchPlayerRow (the actual Upcoming/Past match cards) so they render
// identically and can't silently drift apart the way they did before
// this was extracted — the two were previously separate components with
// separate detail-rendering code, which is exactly why a UI update to
// one didn't show up in the other.
function ProjectionDetail({ r, p, cfg, games, pastMatches, team }) {
  const theme = useTheme();
  const championStats = useChampionStats();
  const consistency = championStats ? championPoolConsistency(p.name, championStats, cfg.key) : null;
  // A player can have no season rate for this stat while still having a
  // projection: the rate the model uses comes from past_matches, and
  // p.cur is a separate aggregate that a given stat may predate. 134 of
  // 271 CS2 players were in exactly that state for headshots on the day
  // this shipped. null, not undefined, so every consumer below has to
  // decide what to do about it rather than quietly arithmetic on NaN.
  const rawSeasonAvg = p.cur ? p.cur[cfg.key] : undefined;
  const seasonAvg = typeof rawSeasonAvg === "number" && isFinite(rawSeasonAvg) ? rawSeasonAvg : null;
  const seasonTotal = seasonAvg !== null ? seasonAvg * games : null;
  const edgePct = seasonTotal ? ((r.total - seasonTotal) / seasonTotal) * 100 : null;
  const axes = projectionRadarAxes(r, seasonAvg);
  const barScale = Math.max(
    ...[r.recentFormRate, r.careerRate, seasonAvg].filter((v) => typeof v === "number" && isFinite(v)),
    0,
  ) * 1.15;
  const tags = projectionTags(r, theme);
  // pastMatches/team are optional so any caller that hasn't been updated
  // still renders everything else rather than throwing.
  const form = pastMatches && team ? recentForm(pastMatches, team, p.name, cfg.key, 8) : [];
  const steadiness = consistencyScore(form.map((f) => f.value));
  // Unbounded count — `form` above is capped at 8 for the chart, which
  // would badly understate how much history actually exists.
  const trackedCount = pastMatches && team ? recentForm(pastMatches, team, p.name, cfg.key, 500).length : 0;
  return (
    <div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <RadarChart axes={axes} color={theme.accent} />
        <div style={{ flex: 1, minWidth: 140 }}>
          <div style={{ fontFamily: "'Fraunces', serif", fontSize: 30, fontWeight: 600, color: theme.accent }}>
            {r.total.toFixed(1)}
          </div>
          {edgePct != null && Math.abs(edgePct) >= 1 && (
            <div style={{ fontSize: 12, color: edgePct > 0 ? theme.good : theme.bad, fontFamily: "'IBM Plex Mono', monospace", marginTop: 2 }}>
              {edgePct > 0 ? "+" : ""}{edgePct.toFixed(0)}% vs season avg
            </div>
          )}
          {tags.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
              {tags.map((t) => (
                <span key={t.text} style={{
                  fontSize: 11, fontWeight: 500, padding: "4px 11px", borderRadius: 20,
                  fontFamily: "'Inter', sans-serif", background: `${t.color}20`, color: t.color,
                }}>
                  {t.text}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Stated on every projection, not only the weak ones. Someone who
          opens this panel is asking how much to trust the number, and
          "solid" only means anything if the same line would have said
          otherwise. The chip on the row stays silent when there is
          nothing to warn about; this does not. */}
      {typeof r.evidenceGames === "number" && isFinite(r.evidenceGames) && (() => {
        const tier = evidenceTier(r.evidenceGames);
        const tone = tier === "thin" ? theme.bad : tier === "limited" ? theme.textDim : theme.good;
        const games = Math.round(r.evidenceGames);
        const basis = r.careerRate != null && r.careerWeight > 0
          ? "career record and recent matches" : "recent matches";
        return (
          <div style={{ marginTop: 14, padding: "9px 12px", borderRadius: 8,
                        background: theme.steelSoft, fontSize: 11.5, lineHeight: 1.55,
                        color: theme.textFaint }}>
            <span style={{ color: tone, fontWeight: 600 }}>
              {games} game{games === 1 ? "" : "s"} of evidence
            </span>
            {" — drawn from this player's "}{basis}.{" "}
            {tier === "thin"
              ? "Below four games the model has measured about 5–12% worse than its own average, so treat this as a weaker read than the number alone suggests."
              : tier === "limited"
              ? "Around the model's average accuracy; it improves noticeably from eight games up."
              : "In the range where the model has measured more accurate than its own average."}
          </div>
        );
      })()}

      <div style={{ marginTop: 14 }}>
        {/* One scale for all three bars, so their lengths stay comparable.
            Built from the values that exist — a missing season rate must
            not drag the maximum to NaN and flatten every bar to zero. */}
        <ScoreBar label="Recent form" value={r.recentFormRate} max={barScale} unit={`${cfg.key}/g`} color={theme.textDim} />
        {r.careerRate != null && r.careerWeight > 0 && (
          <ScoreBar label="Career baseline" value={r.careerRate} max={barScale} unit={`${cfg.key}/g`} color={theme.accent} />
        )}
        <ScoreBar label="Season average" value={seasonAvg} max={barScale} unit={`${cfg.key}/g`} color={theme.textFaint} />
      </div>

      {form.length > 0 && (
        <div style={{ marginTop: 16, paddingTop: 14, borderTop: `1px solid ${theme.steelSoft}` }}>
          <RecentFormChart rows={form} line={r.total} statLabel={cfg.label.toLowerCase()} />
          {steadiness && (
            <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, color: theme.textFaint }}>Consistency</span>
              <div style={{ flex: 1, minWidth: 90, height: 5, background: theme.steelSoft, borderRadius: 3, overflow: "hidden" }}>
                <div style={{
                  width: `${steadiness.score}%`, height: "100%", borderRadius: 3,
                  background: steadiness.score >= 70 ? theme.good : steadiness.score >= 45 ? theme.accent : theme.bad,
                }} />
              </div>
              <span style={{ fontSize: 11, fontFamily: "'IBM Plex Mono', monospace", color: theme.textDim }}>
                {steadiness.score.toFixed(0)}/100
              </span>
              <span style={{ fontSize: 11, color: theme.textFaint }}>
                avg {steadiness.mean.toFixed(1)} · ±{(steadiness.cv * steadiness.mean).toFixed(1)}
              </span>
            </div>
          )}
        </div>
      )}

      <div style={{ marginTop: 12, fontSize: 11, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace", lineHeight: 1.8, paddingTop: 10, borderTop: `1px solid ${theme.steelSoft}` }}>
        <div>opponent ×{r.oppMult.toFixed(2)} {cfg.useKP && <>&nbsp;·&nbsp; kp ×{r.kpMult.toFixed(2)}</>} &nbsp;→&nbsp; <span style={{ color: theme.text }}>{r.perGame.toFixed(2)} {cfg.key}/game</span> × {games}g</div>
        {/* Only shown where the tier actually applied. A line reading
            "0% of ..." on every LoL card would be noise, and one shown
            when the tier silently fell back would be a lie. */}
        {r.shrinkPull != null && r.shrinkPull > 0.02 && (
          <div>
            thin sample ({r.priorGames}g) &nbsp;·&nbsp; pulled {Math.round(r.shrinkPull * 100)}% toward
            the league average of {r.shrunkTo.toFixed(2)} {cfg.key}/game
          </div>
        )}
        {r.shareTier != null && r.shareWeight > 0 && (
          <div>
            {Math.round(r.shareWeight * 100)}% from team share
            {r.sharePct != null && <> ({(r.sharePct * 100).toFixed(1)}% of team {cfg.key})</>}
            &nbsp;·&nbsp; that estimate alone: {r.shareTier.toFixed(2)} {cfg.key}/game
          </div>
        )}
        {/* Reports the REAL tracked match history first, because the
            prior-split aggregate below it is a narrow one-split slice
            and reading "no data on file" made it look as though the app
            had no history for a player at all — when it may have dozens
            of matches on record. That field only ever covers the single
            immediately-preceding split, so a player who changed region,
            was promoted mid-year, or simply didn't play that one split
            legitimately has none, which is not the same as unknown. */}
        <div>
          tracked history: {trackedCount > 0
            ? `${trackedCount} match${trackedCount === 1 ? "" : "es"} on record`
            : "none on record yet"}
        </div>
        <div>
          prior split: {p.hist
            ? `${p.hist[cfg.key].toFixed(1)} ${cfg.key}/g (${p.hist.g}g)`
            : "didn't play the preceding split"}
        </div>
      </div>

      {consistency && (
        <div style={{ marginTop: 8, paddingTop: 10, borderTop: `1px solid ${theme.steelSoft}` }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: theme.text, letterSpacing: 0.3 }}>Champion cheat code</div>
          <div style={{ fontSize: 11, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace", marginTop: 3 }}>
            {consistency.championsConsidered} champs, {consistency.totalGames}g on file &nbsp;→&nbsp;
            {" "}CV={consistency.coefficientOfVariation != null ? consistency.coefficientOfVariation.toFixed(2) : "n/a"}
            {consistency.coefficientOfVariation != null && (
              <span style={{ color: consistency.coefficientOfVariation < 0.3 ? theme.good : consistency.coefficientOfVariation > 0.6 ? theme.bad : theme.textFaint }}>
                {" "}({consistency.coefficientOfVariation < 0.3 ? "steady regardless of pick" : consistency.coefficientOfVariation > 0.6 ? "swings a lot by champion" : "moderate variation"})
              </span>
            )}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
            {consistency.entries.slice(0, 8).map((e) => {
              const rate = e[cfg.key];
              const diffPct = seasonAvg > 0 ? ((rate - seasonAvg) / seasonAvg) * 100 : 0;
              const pillColor = Math.abs(diffPct) < 8 ? theme.textDim : diffPct > 0 ? theme.good : theme.bad;
              return (
                <span key={e.champion} title={`${rate.toFixed(1)} ${cfg.key}/g over ${e.g} games — ${diffPct >= 0 ? "+" : ""}${diffPct.toFixed(0)}% vs their own average`}
                  style={{
                    fontSize: 10, padding: "4px 8px", borderRadius: 4, fontFamily: "'IBM Plex Mono', monospace",
                    background: `${pillColor}15`, border: `1px solid ${pillColor}45`, color: theme.text,
                  }}>
                  {e.champion} <span style={{ color: pillColor, fontWeight: 600 }}>{rate.toFixed(1)}</span>
                  <span style={{ color: theme.textFaint }}> ({e.g}g)</span>
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/* ============================================================
   RECENT FORM & CONSISTENCY — the last N matches for a player,
   and how reliably they produce.

   Deliberately works at MATCH level rather than per-game. All three
   scrapers now emit a finer `per_game` field -- it is what lets a
   map-1 line and a maps-1-3 line be settled over their own maps -- but
   every projection in this app is a per-MATCH number, and showing
   per-game history beside a per-match projection would invite
   comparing two different units. Match level keeps the history and the
   projection directly comparable.
   ============================================================ */

function recentForm(pastMatches, team, playerName, statKey, limit = 8) {
  const rows = [];
  for (const m of pastMatches) {
    if (!m.actual) continue;
    const onTeam = m.teamA === team || m.teamB === team;
    if (!onTeam) continue;
    const val = getActualStat(m, team, playerName, statKey);
    if (val === undefined || val === null) continue;
    rows.push({
      date: m.date || "",
      value: val,
      opponent: m.teamA === team ? m.teamB : m.teamA,
      stage: m.stage || null,
      // Series format and the maps "value" actually covers. Carried so
      // the UI can show that a Bo5 figure spans 3 maps while a Bo3 spans
      // 2 — without it, bars of different map-counts sit side by side
      // looking directly comparable when they aren't.
      seriesFormat: m.series_format || null,
      mapsCounted: mapsCountedFor(m),
    });
  }
  rows.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0)); // newest first
  return rows.slice(0, limit);
}

// Consistency as coefficient of variation (stdev / mean), inverted onto
// a friendlier 0-100 scale. CV is the right base measure here because it
// is scale-free: 3 assists of spread means something very different for
// a support averaging 12 than for a top laner averaging 4, and a raw
// standard deviation would rank every low-volume player as "consistent"
// purely for having small numbers.
function consistencyScore(values) {
  const n = values.length;
  if (n < 3) return null; // too few games for a variance figure to mean anything
  const mean = values.reduce((s, v) => s + v, 0) / n;
  if (mean <= 0) return null;
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / n;
  const cv = Math.sqrt(variance) / mean;
  // cv 0 -> 100 (perfectly steady), cv 1.0 -> 0 (spread as large as the
  // average). Clamped because CV is unbounded above.
  return { cv, score: Math.max(0, Math.min(100, (1 - cv) * 100)), mean, n };
}

// How often a player cleared a given line — the figure a props-style
// view actually leads with. Ties count as a miss, matching the standard
// "over" convention where landing exactly on the line does not win.
function hitRate(values, line) {
  if (!values.length || line == null) return null;
  const hits = values.filter((v) => v > line).length;
  return { hits, total: values.length, pct: (hits / values.length) * 100 };
}

// Props-style recent form: one bar per recent match, with the current
// projection drawn across as the reference line so "would this have
// cleared?" is readable at a glance. Bars are colored against that line
// rather than against each other, since the line is the actual question.
function RecentFormChart({ rows, line, statLabel }) {
  const theme = useTheme();
  if (!rows.length) return null;
  const ordered = [...rows].reverse(); // oldest -> newest reads left to right
  const maxVal = Math.max(...ordered.map((r) => r.value), line || 0, 1);
  const hr = hitRate(rows.map((r) => r.value), line);
  const H = 92;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: theme.text, fontFamily: "'Inter', sans-serif" }}>
          Last {ordered.length} matches
        </span>
        {hr && (
          <span style={{ fontSize: 12, fontFamily: "'IBM Plex Mono', monospace", color: theme.textDim }}>
            <span style={{ color: hr.pct >= 50 ? theme.good : theme.bad, fontWeight: 700 }}>
              {hr.hits}/{hr.total}
            </span>{" "}
            over {line.toFixed(1)}
          </span>
        )}
      </div>

      <div style={{ position: "relative", height: H, display: "flex", alignItems: "flex-end", gap: 4 }}>
        {line != null && maxVal > 0 && (
          <div
            title={`Projection: ${line.toFixed(1)} ${statLabel}`}
            style={{
              position: "absolute", left: 0, right: 0, bottom: (line / maxVal) * H,
              borderTop: `1px dashed ${theme.accent}`, opacity: 0.75, pointerEvents: "none", zIndex: 1,
            }}
          />
        )}
        {ordered.map((r, i) => {
          const h = Math.max(2, (r.value / maxVal) * H);
          const over = line != null && r.value > line;
          return (
            <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
              <div
                title={`${r.value} vs ${r.opponent}${r.date ? ` · ${r.date}` : ""}`}
                style={{
                  width: "100%", height: h, borderRadius: "3px 3px 0 0",
                  background: over ? theme.good : theme.steel,
                  border: `1px solid ${over ? theme.good : theme.steel}`,
                  minHeight: 2,
                }}
              />
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
        {ordered.map((r, i) => (
          <div key={i} style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 11, fontFamily: "'IBM Plex Mono', monospace", color: theme.text, fontWeight: 600 }}>
              {r.value}
            </div>
            <div style={{ fontSize: 9, color: theme.textFaint, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {r.opponent}
            </div>
            {r.seriesFormat && (
              <div style={{ fontSize: 8, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace", opacity: 0.8 }}>
                {r.seriesFormat}
                {r.mapsCounted ? `·${r.mapsCounted}m` : ""}
              </div>
            )}
          </div>
        ))}
      </div>

      {(() => {
        // Mixed-format warning. A Bo5 figure covers 3 maps and a Bo3
        // covers 2, so a bar chart mixing them is not an apples-to-apples
        // comparison and the hit rate against a single line is only
        // roughly meaningful. Say so rather than letting the chart imply
        // more precision than it has.
        const formats = [...new Set(ordered.map((r) => r.seriesFormat).filter(Boolean))];
        if (formats.length <= 1) return null;
        return (
          <div style={{ marginTop: 8, fontSize: 10, color: theme.textFaint, lineHeight: 1.5 }}>
            Mixed series lengths ({formats.join(" / ")}) — Bo5 bars cover 3 maps, Bo3 cover 2,
            so heights aren't directly comparable.
          </div>
        );
      })()}
    </div>
  );
}

function PlayerRow({ teams, pastMatches, p, team, opponentTeam, games, weights, teamColor, statType }) {
  const theme = useTheme();
  const cfg = STAT_TYPES[statType];
  const r = project(teams, pastMatches, p, team, opponentTeam, games, weights, statType);
  const [open, setOpen] = useState(false);
  return (
    <div className="kp-clickable" onClick={() => setOpen(!open)} style={{ cursor: "pointer", borderBottom: `1px solid ${theme.steel}`, padding: "15px 18px", background: open ? theme.graphiteLight : "transparent" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11, minWidth: 0 }}>
          <span
            style={{
              width: 34, height: 34, borderRadius: "50%", flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: `${teamColor}22`, border: `1.5px solid ${teamColor}`,
              color: teamColor, fontFamily: "'Fraunces', serif", fontWeight: 700, fontSize: 12,
            }}
          >
            {initialsFor(p.name)}
          </span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, color: theme.text, fontSize: 15, fontFamily: "'Fraunces', serif", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</div>
            {p.role && <span className="kp-chip" style={{ background: theme.steelSoft, color: theme.textDim, marginTop: 4 }}>{p.role}</span>}
            <EvidenceChip games={r.evidenceGames} compact />
          </div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{
            fontFamily: "'Fraunces', serif", fontSize: 26, color: theme.accent, fontWeight: 600,
          }}>
            {r.total.toFixed(1)}
          </div>
          <div style={{ fontSize: 10, color: theme.textFaint, marginTop: 4 }}>proj. {cfg.label.toLowerCase()} / {games}g</div>
        </div>
      </div>
      {open && (
        <div style={{ marginTop: 12 }}>
          <ProjectionDetail r={r} p={p} cfg={cfg} games={games} pastMatches={pastMatches} team={team} />
        </div>
      )}
    </div>
  );
}

/* ============================================================
   STANDINGS & POWER RANKINGS — both computed entirely from data
   already loaded, same "derive it for free" pattern as head-to-head.

   Regional standings: straightforward series win/loss record within
   one region's pastMatches, plus game (map) differential as a
   tiebreaker signal.

   Power ranking: a simple in-region win% can't meaningfully compare
   teams that have never played each other — an LCK team and an LEC
   team have no shared opponents in the regular season. Elo solves
   this properly: it's fed by whatever matches exist, and the same
   rating pool naturally absorbs cross-region results the moment
   international events (MSI, Worlds) start happening, at which point
   it becomes a real cross-region comparison rather than a proxy for
   in-region record. Until then it will track closely with in-region
   standings — that's expected and correct, not a bug; the payoff is
   specifically for when cross-region matches start flowing in.
   ============================================================ */

function computeStandings(teams, pastMatches) {
  const standings = {};
  for (const teamName in teams) {
    standings[teamName] = { wins: 0, losses: 0, gameWins: 0, gameLosses: 0 };
  }
  for (const m of pastMatches) {
    if (!m.teamA || !m.teamB || !m.winner) continue;
    // Playoff results don't fold into regular-season standings — they never
    // are on any real broadcast, and blending them would silently produce a
    // wrong table the moment a region enters its bracket.
    if (m.week && playoffRoundRank(m.week) !== null) continue;
    const loser = m.winner === m.teamA ? m.teamB : m.teamA;
    if (standings[m.winner]) standings[m.winner].wins += 1;
    if (standings[loser]) standings[loser].losses += 1;
    const parts = (m.score || "").split("-").map((x) => parseInt(x, 10));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      const winnerGames = Math.max(parts[0], parts[1]);
      const loserGames = Math.min(parts[0], parts[1]);
      if (standings[m.winner]) { standings[m.winner].gameWins += winnerGames; standings[m.winner].gameLosses += loserGames; }
      if (standings[loser]) { standings[loser].gameWins += loserGames; standings[loser].gameLosses += winnerGames; }
    }
  }
  return Object.entries(standings)
    .map(([team, s]) => ({
      team, ...s,
      winPct: s.wins + s.losses > 0 ? s.wins / (s.wins + s.losses) : 0,
      gameDiff: s.gameWins - s.gameLosses,
    }))
    .sort((a, b) => b.winPct - a.winPct || b.gameDiff - a.gameDiff || a.team.localeCompare(b.team));
}

function computeEloRatings(regionsData, regionList, kFactor = 32) {
  const ratings = {};
  const teamRegion = {};
  const allMatches = [];
  for (const regionKey of regionList) {
    const rd = regionsData[regionKey];
    if (!rd || !rd.teams) continue;
    for (const teamName in rd.teams) {
      ratings[teamName] = 1500;
      teamRegion[teamName] = regionKey;
    }
    for (const m of rd.past_matches || []) {
      if (m.teamA && m.teamB && m.winner) allMatches.push(m);
    }
  }
  const sorted = allMatches.sort((a, b) => ((a.date || "") < (b.date || "") ? -1 : (a.date || "") > (b.date || "") ? 1 : 0));
  for (const m of sorted) {
    const { teamA, teamB, winner } = m;
    if (!(teamA in ratings) || !(teamB in ratings)) continue;
    const ra = ratings[teamA], rb = ratings[teamB];
    const expectedA = 1 / (1 + Math.pow(10, (rb - ra) / 400));
    const scoreA = winner === teamA ? 1 : 0;
    ratings[teamA] = ra + kFactor * (scoreA - expectedA);
    ratings[teamB] = rb + kFactor * ((1 - scoreA) - (1 - expectedA));
  }
  return Object.entries(ratings)
    .map(([team, rating]) => ({ team, rating, region: teamRegion[team] }))
    .sort((a, b) => b.rating - a.rating);
}

function EdgeRow({ row, theme, cfg, fresh, ageMinutes, isDesktop }) {
  const { prop, projection, edge } = row;
  // Per row, not per payload: these rows can span fixtures hours apart,
  // and one that has kicked off says so without greying the rest.
  const live = propIsLive(prop);
  const aged = live && !fresh ? ageLabel(ageMinutes) : null;
  // Same affordance as the match cards' PlayerRow: the number on its own
  // is a claim, and the reason to trust or discard it is in the
  // breakdown. A row with no breakdown stays inert rather than opening
  // an empty panel.
  const [open, setOpen] = useState(false);
  const canExpand = !!(row.breakdown && row.player);
  const mapWindow = mapWindowLabel(prop.maps);
  const ambiguous = edge === null;
  // The number shown is the one the board is ranked by, so the order on
  // screen always matches the figures printed on it. The raw edge is
  // still said out loud wherever the two differ, because it is what
  // anyone can recompute from the projection and the line beside it.
  const shown = row.adjustedEdge !== null && row.adjustedEdge !== undefined
    ? row.adjustedEdge : edge;
  // The trusted count, not the raw one: a borrowed-roster event's maps
  // were played in another competition, and the chip and the ranking both
  // have to reflect that rather than the size of the pile.
  const evidence = row.evidence !== undefined && row.evidence !== null
    ? row.evidence
    : (row.breakdown ? row.breakdown.evidenceGames : null);
  // Flagged on the band, not on the arithmetic. Comparing the two
  // numbers marked almost every row, because the top band's 0.94 still
  // shifts a mid-sized edge by more than a printed decimal -- 160 of 219
  // rows on a real board, which is the wallpaper problem the evidence
  // chip was shaped to avoid. Only the bands that change the story
  // (0.34 and 0.72) say so.
  const MATERIAL_DISCOUNT = 0.9;
  const discounted = !ambiguous && typeof shown === "number"
    && edgeMultiplier(evidence) < MATERIAL_DISCOUNT;
  const tone = ambiguous || !live ? theme.textFaint
    : shown > 0 ? theme.good : shown < 0 ? theme.bad : theme.textDim;
  const when = row.when ? new Date(row.when) : null;
  const clock = when && !isNaN(when)
    ? when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";

  return (
    <div className={canExpand ? "kp-clickable" : undefined}
         onClick={canExpand ? () => setOpen(!open) : undefined}
         style={{ padding: "11px 14px", borderBottom: `1px solid ${theme.steel}`,
                  cursor: canExpand ? "pointer" : "default",
                  background: open ? theme.graphiteLight : "transparent" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: theme.text }}>{row.name}</span>
          {row.role && <span style={{ fontSize: 10, color: theme.textFaint, textTransform: "uppercase", letterSpacing: 0.5 }}>{row.role}</span>}
          {/* The board is ranked by edge SIZE, and a big edge off three
              games is the most dangerous row on the screen -- it sorts
              to the top precisely because the model had least to go on. */}
          {row.breakdown && <EvidenceChip games={evidence} compact
                                          borrowed={row.borrowedContext} />}
        </div>
        <div style={{ fontSize: 11, color: theme.textFaint, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {row.team} vs {row.opponent}{clock ? ` · ${clock}` : ""}
          {row.oppKnown === false && (
            /* Said out loud rather than silently folded in. The projection
               is a neutral-opponent estimate: this app has no roster for
               the other side, so the opponent-strength term is 1 and the
               number is less adjusted than the rest of the list. */
            <span title={`No roster for ${row.opponent}, so opponent strength was not applied. The projection is a neutral-opponent estimate.`}
                  style={{ marginLeft: 6, color: theme.accent, opacity: 0.85 }}>
              · no opp adj
            </span>
          )}
        </div>
      </div>

      {isDesktop && (
        <div style={{ textAlign: "right", minWidth: 58 }}>
          <div className="kp-num" style={{ fontSize: 13, color: theme.textDim }}>{projection.toFixed(1)}</div>
          <div style={{ fontSize: 9, letterSpacing: 0.6, textTransform: "uppercase", color: theme.textFaint, marginTop: 2 }}>proj</div>
        </div>
      )}
      <div style={{ textAlign: "right", minWidth: 52 }}>
        <div className="kp-num" style={{ fontSize: 14, fontWeight: 600, color: theme.text }}>{prop.line}</div>
        <div style={{ fontSize: 9, letterSpacing: 0.6, textTransform: "uppercase", color: theme.textFaint, marginTop: 2 }}>line</div>
      </div>
      <div style={{ textAlign: "right", minWidth: 72 }}>
        <div className="kp-num" style={{ fontSize: 15, fontWeight: 700, color: tone }}
             title={discounted
               ? `Raw edge ${edge > 0 ? "+" : ""}${edge.toFixed(1)}, cut to `
                 + `${shown > 0 ? "+" : ""}${shown.toFixed(1)} because it rests on `
                 + `${Math.round(evidence || 0)} games. Measured on past seasons, about `
                 + `${Math.round(100 * edgeMultiplier(evidence))}% of a claimed edge at that `
                 + `sample size actually materialises.`
               : undefined}>
          {ambiguous ? `${prop.lineCount} lines`
            : !live ? "started"
            : `${shown > 0 ? "OVER +" : shown < 0 ? "UNDER " : ""}${shown === 0 ? "0.0" : Math.abs(shown).toFixed(1)}`}
        </div>
        <div style={{ fontSize: 9, letterSpacing: 0.6, textTransform: "uppercase", color: theme.textFaint, marginTop: 2 }}>
          {/* Printed on the row, not left to a tooltip: someone scanning
              for the biggest number is exactly the person who needs to
              know this one was cut, and they are not hovering. */}
          {!live ? `${mapWindow} · started`
            : discounted
            ? `${mapWindow} · from ${edge > 0 ? "+" : ""}${edge.toFixed(1)}${aged ? ` · ${aged}` : ""}`
            : aged ? `${mapWindow} · ${aged}`
            : mapWindow}
        </div>
      </div>
      </div>
      {open && canExpand && (
        // games={prop.maps} on purpose: collectEdges projected this row
        // over the LINE's window, so the detail has to describe that same
        // window or its per-game maths would not multiply out to the
        // number shown above it.
        <div style={{ marginTop: 12 }}>
          <ProjectionDetail r={row.breakdown} p={row.player} cfg={cfg} games={prop.maps}
                            pastMatches={row.pastMatches} team={row.team} />
        </div>
      )}
    </div>
  );
}

/* The board, split into one section per map window.

   Map 1, maps 1-2 and maps 1-3 are three different markets. They are
   priced separately, they are settled separately, and the model's record
   on one says nothing about its record on another -- so they are read
   separately too, rather than interleaved by edge into a single column
   where the only thing telling them apart is a 9px label.

   Ranking inside a section is whatever order the rows arrived in, which
   is rankEdges' global order: filtering preserves it, so the top of each
   section is still that section's best row. */
function windowSections(rows) {
  const byWindow = new Map();
  for (const row of rows) {
    const maps = typeof row.maps === "number" ? row.maps
      : (row.prop && typeof row.prop.maps === "number" ? row.prop.maps : -1);
    if (!byWindow.has(maps)) byWindow.set(maps, []);
    byWindow.get(maps).push(row);
  }
  return [...byWindow.keys()]
    .sort((a, b) => a - b)
    .map((maps) => ({ maps, rows: byWindow.get(maps) }));
}

/* The board, ranked. See collectEdges for why this view exists at all. */
function EdgesTab({ regionsData, regionList, regionLabels, weights, statType, game, isDesktop }) {
  const theme = useTheme();
  const propsData = useProps();
  const fresh = propsAreFresh(propsData);
  const ageMinutes = propsAgeMinutes(propsData);
  const cfg = STAT_TYPES[statType];
  const rows = collectEdges(regionsData, regionList, propsData, weights, statType, game);
  const startedCount = rows.filter((r) => !propIsLive(r.prop)).length;

  /* What this board has actually done, shown ON the board.
     A ranked list asserts that its top is better than its middle, and
     that assertion is testable -- so it is tested here rather than left
     for the reader to take on faith from a tab they may never open. */
  const results = useGradedResults();
  const graded = useMemo(
    () => (results ? modelRecord(regionsData, regionList, results, weights, statType) : []),
    [results, regionsData, regionList, weights, statType]);
  const record = useMemo(() => (graded.length ? recordVsLine(graded) : null), [graded]);
  const ranking = useMemo(() => (graded.length ? rankingIsInformative(graded) : null), [graded]);
  const BREAKEVEN = 0.524;

  const note = (text) => (
    <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                  ...elevation(), padding: "18px 20px", fontSize: 12.5, color: theme.textFaint, lineHeight: 1.6 }}>
      {text}
    </div>
  );

  if (!propsData) {
    return note(<>No lines loaded. <code>props.json</code> is produced by <code>scripts/scrape_props.py</code> from a
      payload saved out of a browser — see “Prop lines” in the README. The projections on the other tabs do not
      depend on it.</>);
  }
  if (!rows.length) {
    return note(<>Lines are loaded, but none of them belong to a player in an upcoming {cfg.label.toLowerCase()} fixture
      this app tracks. That is usually the board being a league outside the tracked regions rather than anything
      broken — <code>scripts/dev/inspect_props_payload.py --names</code> says which.</>);
  }

  const withEdge = rows.filter((r) => r.edge !== null);
  // The biggest number on the board is the biggest ADJUSTED one, because
  // that is the order the board is in. Quoting the raw maximum beside a
  // list sorted the other way would describe a screen nobody is looking at.
  const best = withEdge.length
    ? Math.abs(withEdge[0].adjustedEdge !== null && withEdge[0].adjustedEdge !== undefined
        ? withEdge[0].adjustedEdge : withEdge[0].edge)
    : 0;
  const anyDiscounted = withEdge.some((r) => r.breakdown
    && edgeMultiplier(r.evidence !== undefined ? r.evidence : r.breakdown.evidenceGames) < 0.9);

  /* A board that nearly all points one way is a statement about the
     model, not a list of opportunities.

     Real edges are scattered: the market is wrong in both directions.
     When almost every line reads the same way, the likelier reading is
     that the model and the market disagree about the EVENT rather than
     about the players -- a level offset, which no amount of per-player
     accuracy will fix and which the reader cannot see by scrolling.

     Found on a real board: 57 of 78 Valorant lines read OVER, every one
     of them a VCT Champions fixture projected from regional form, with
     the market pricing a step up in class that regional form does not
     contain. */
  const ONE_SIDED_MIN_ROWS = 10;
  const ONE_SIDED_SHARE = 0.7;
  const decided = fresh ? withEdge.filter((r) => r.edge !== 0) : [];
  const overs = decided.filter((r) => r.edge > 0).length;
  const lean = decided.length >= ONE_SIDED_MIN_ROWS
    && (overs / decided.length >= ONE_SIDED_SHARE
        || overs / decided.length <= 1 - ONE_SIDED_SHARE)
    ? { overs, n: decided.length, side: overs * 2 > decided.length ? "over" : "under" }
    : null;
  const borrowedCount = withEdge.filter((r) => r.borrowedContext).length;

  return (
    <div>
      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                    ...elevation(), overflow: "hidden" }}>
        <div style={{ padding: "13px 16px", borderBottom: `1px solid ${theme.steel}`,
                      display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: theme.text }}>
            {rows.length} posted {rows.length === 1 ? "line" : "lines"}
          </span>
          <span style={{ fontSize: 11.5, color: theme.textFaint }}>
            ranked by evidence-adjusted edge · biggest {best.toFixed(1)}
          </span>
          {/* Informational now, not a refusal. Edges ARE drawn on an old
              payload, because a line whose match has not been played is
              still the line; what the reader needs is the caveat, which
              is the age itself. Amber rather than red for the same
              reason — it is a thing to know, not a thing that went
              wrong. */}
          {!fresh && (
            <span style={{ fontSize: 11, color: theme.textDim, marginLeft: "auto" }}>
              lines fetched {ageLabel(ageMinutes)} — still priced, but they move
            </span>
          )}
          {startedCount > 0 && (
            <span style={{ fontSize: 11, color: theme.textFaint, marginLeft: !fresh ? 0 : "auto" }}>
              {startedCount} {startedCount === 1 ? "fixture has" : "fixtures have"} started
            </span>
          )}
        </div>
        {record && record.winRate && (
          <div style={{ padding: "11px 16px", borderBottom: `1px solid ${theme.steel}`,
                        fontSize: 11.5, lineHeight: 1.6, color: theme.textDim }}>
            <strong style={{ color: theme.text }}>
              This board's record: {(100 * record.winRate.mean).toFixed(1)}%
            </strong>{" "}
            when the projection disagreed with the line, over {record.matches} matches
            {" "}(95% CI {(100 * record.winRate.lo).toFixed(1)}–{(100 * record.winRate.hi).toFixed(1)}%).
            Breakeven at −110 is {(100 * BREAKEVEN).toFixed(1)}%
            {record.winRate.lo > BREAKEVEN ? ", which this clears."
              : record.winRate.hi < BREAKEVEN ? ", which this is below."
              : ", and that interval still contains it — nothing here is established yet."}
            {ranking && !ranking.informative && (
              <>
                {" "}Rows are sorted by the size of the disagreement, but a bigger one has
                {" "}<strong style={{ color: theme.text }}>not</strong> won more often so far
                {" "}({(100 * ranking.big.mean).toFixed(0)}% at {ranking.threshold}+ against
                {" "}{(100 * ranking.small.mean).toFixed(0)}% below it), so read the order as
                {" "}a sort, not a ranking of confidence.
              </>
            )}
            {ranking && ranking.informative && (
              <>
                {" "}Rows above {ranking.threshold} have won {(100 * ranking.big.mean).toFixed(0)}%
                {" "}against {(100 * ranking.small.mean).toFixed(0)}% below it.
              </>
            )}
          </div>
        )}
        {lean && (
          <div style={{ padding: "11px 16px", borderBottom: `1px solid ${theme.steel}`,
                        background: `${theme.accent}0E`, fontSize: 12, lineHeight: 1.55,
                        color: theme.textDim }}>
            <strong style={{ color: theme.text }}>
              {lean.overs} of {lean.n} lines read {lean.side.toUpperCase()}.
            </strong>{" "}
            Edges that nearly all point one way usually mean the model and the market
            disagree about the fixture rather than about the players — a level offset,
            which per-player accuracy cannot fix.
            {borrowedCount === withEdge.length && withEdge.length > 0 && (
              <> Every line here is for an event with no completed matches yet, so the
                 projections come from the players’ form in another competition. The
                 market is pricing something about this event that that form does not
                 contain.</>
            )}
          </div>
        )}
        {windowSections(rows).map(({ maps, rows: section }) => (
          <Fragment key={maps}>
            {/* Named even when it is the only section on the board. A
                reader who cannot see which maps they are looking at has
                to infer it from the line, and the whole point of
                separating the windows is that the inference is wrong as
                soon as a second one appears. */}
            <div style={{ padding: "9px 16px", borderTop: `1px solid ${theme.steel}`,
                          borderBottom: `1px solid ${theme.steel}`, background: theme.graphiteLight,
                          display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontSize: 10.5, letterSpacing: 1, textTransform: "uppercase",
                             fontWeight: 700, color: theme.textDim }}>
                {maps > 0 ? mapWindowLabel(maps) : "window not stated"}
              </span>
              <span style={{ fontSize: 11, color: theme.textFaint }}>
                {section.length} {section.length === 1 ? "line" : "lines"}
              </span>
            </div>
            {section.map((row, i) => (
              <EdgeRow key={`${row.game}-${row.name}-${row.team}-${maps}-${i}`} row={row} theme={theme} cfg={cfg}
                       fresh={fresh} ageMinutes={ageMinutes} isDesktop={isDesktop} />
            ))}
          </Fragment>
        ))}
      </div>
      <div style={{ fontSize: 11, color: theme.textFaint, marginTop: 10, lineHeight: 1.6 }}>
        Edge is the projection minus the line, over the line’s own map window, discounted by how much evidence
        the projection rests on. Measured over past seasons, a disagreement built on fewer than four games
        realises about a third of its face value, and one built on twelve or more realises almost all of it — so
        ranking on the raw number promoted the rows the model knew least about. {anyDiscounted && "A row that was cut shows the figure it came from beside its map window. "}
        It is a model disagreeing with a market, not a prediction of the result — and the model is the same one
        the accuracy figures on the Future tab describe.
      </div>
    </div>
  );
}

/* The record. Deliberately austere: this screen either has enough evidence
   to say something or it says it does not, and it never splits the
   difference with an encouraging number off a handful of bets. */
const RECORD_MIN_SAMPLE = 30;

/* The graded record, fetched once per component that needs it.

   Lifted out of RecordTab because the EDGES board needs it too, and for
   a reason worth stating: that board is a ranked list, and a ranked
   list asserts that the top of it is better than the middle. The
   measured record says it is not -- bigger disagreements have not won
   more often. A board making that claim while the evidence sits one tab
   away is the kind of thing a paying reader is entitled to be annoyed
   about.

   undefined = still loading, null = unavailable. */
function useGradedResults() {
  const [results, setResults] = useState(undefined);
  useEffect(() => {
    let live = true;
    fetch(DATA_URL_RESULTS, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (live) setResults(data); })
      .catch(() => { if (live) setResults(null); });
    return () => { live = false; };
  }, []);
  return results;
}

/* Has a bigger disagreement actually won more often?

   Returns null until there is enough to say. The comparison is the top
   band against everything below it, both clustered on the match, and it
   answers "is the top of this board worth more than the rest of it" --
   which is the only thing the ranking claims. */
function rankingIsInformative(rows, threshold = 2) {
  const decided = rows.filter((r) => r.result !== "push" && typeof r.edge === "number");
  const big = decided.filter((r) => Math.abs(r.edge) >= threshold);
  const small = decided.filter((r) => Math.abs(r.edge) < threshold);
  const rate = (set) => clusteredMean(
    groupByMatch(set).map((m) => m.filter((r) => r.won).length / m.length));
  const bigRate = rate(big), smallRate = rate(small);
  if (!bigRate || !smallRate || big.length < 30 || small.length < 30) return null;
  return {
    big: bigRate, small: smallRate, threshold,
    // "Informative" only if the top band's interval clears the bottom
    // band's point estimate. Anything less is two noisy numbers that
    // happen to be ordered.
    informative: bigRate.lo > smallRate.mean,
  };
}

/* ---------- The parlay ladder, on screen -----------------------------------

   Cross-game on purpose: the legs are ranked in units of the model's own
   error, which is what makes a CS2 kills line and a LoL kills line comparable
   at all, so there is no reason to keep them on separate boards.

   WHAT IS WITHHELD AND WHAT IS NOT. The per-prop probability is withheld until
   parlayEvidence says the ranking separates -- see the long note there. What is
   shown regardless:

     the legs            a fact about the board
     the multiplier      a fact about the board (check it against yours)
     the break-even      arithmetic on the multiplier: (1/M)^(1/n)
     EV at the MEASURED rate   arithmetic on the hit rate this app has actually
                               recorded, clustered on the match. Not a model
                               claim. It is the most useful number here and it
                               is currently negative at every entry size.

   That last one is the point of shipping this now rather than later. A reader
   can see exactly what the model would have to reach before any rung is worth
   playing, and exactly how far short it currently falls. */
function ParlaysTab({ dataByGame, propsData, weightsByGameAndStat, isDesktop }) {
  const theme = useTheme();
  const results = useGradedResults();

  /* Every live line on every game, each with a fixture behind it, ranked in
     error units. The board inference runs here too, so a line whose fixture
     the schedule has not published still reaches the ladder. */
  const rows = useMemo(() => {
    const all = [];
    for (const gameId of GAME_LIST) {
      const cfg = GAMES[gameId];
      const scraped = (dataByGame && dataByGame[gameId]) || cfg.fallbackRegions;
      const regions = withBoardFixtures(scraped, propsData, gameId);
      for (const [statType] of statsForGame(gameId)) {
        const weights = (weightsByGameAndStat[gameId] || {})[statType];
        if (!weights) continue;
        for (const row of collectEdges(regions, cfg.regionList, propsData, weights,
                                       statType, gameId)) {
          // A line on a match that has started is not a bet any more.
          if (!propIsLive(row.prop)) continue;
          // Several lines with no market one named is a refusal upstream, and
          // a rung must not be built on a payout nobody quoted.
          if (row.edge === null) continue;
          all.push(row);
        }
      }
    }
    return all;
  }, [dataByGame, propsData, weightsByGameAndStat]);

  /* The record, across every game and stat, for the gate. Same re-projection
     the Record tab does, pooled rather than per stat, because the question
     "does our ranking separate" is about the ranking and not about kills. */
  const recordRows = useMemo(() => {
    if (!results) return [];
    const all = [];
    for (const gameId of GAME_LIST) {
      const cfg = GAMES[gameId];
      const regions = (dataByGame && dataByGame[gameId]) || cfg.fallbackRegions;
      for (const [statType] of statsForGame(gameId)) {
        const weights = (weightsByGameAndStat[gameId] || {})[statType];
        if (!weights) continue;
        for (const row of modelRecord(regions, cfg.regionList, results, weights, statType)) {
          all.push({ ...row, game: gameId, statType });
        }
      }
    }
    return all;
  }, [results, dataByGame, weightsByGameAndStat]);

  const evidence = useMemo(
    () => (recordRows.length ? parlayEvidence(recordRows) : null), [recordRows]);
  const ladder = useMemo(() => buildParlays(rows), [rows]);

  const shell = (children) => (
    <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`,
                  ...cardShape(theme.cornerStyle), ...elevation(), padding: "18px 20px",
                  fontSize: 12.5, color: theme.textFaint, lineHeight: 1.65 }}>
      {children}
    </div>
  );

  if (!propsData) return shell("No posted lines loaded, so there is nothing to build a parlay from.");
  if (!ladder.length) {
    return shell(`${rows.length} live line(s) across every game, which is not enough distinct `
      + `players to fill even a two-leg entry. The ladder needs one leg per player.`);
  }

  const measured = evidence && evidence.overall;
  const validated = !!(evidence && evidence.validated);

  return (
    <div>
      {/* The honest headline, above everything, because it decides how to read
          every row below it. */}
      <div style={{ background: theme.graphite, border: `1px solid ${validated ? theme.steel : theme.steelSoft || theme.steel}`,
                    ...cardShape(theme.cornerStyle), ...elevation(), padding: "14px 16px",
                    marginBottom: 14, fontSize: 12.5, color: theme.textDim, lineHeight: 1.6 }}>
        <div style={{ color: theme.text, fontWeight: 600, marginBottom: 6 }}>
          {validated ? "Our per-leg probabilities are shown"
                     : "Our per-leg probabilities are withheld"}
        </div>
        {evidence ? (
          <>
            <div>{evidence.reason}.</div>
            {measured && (
              <div style={{ marginTop: 6 }}>
                Across {measured.n} graded match{measured.n === 1 ? "" : "es"} our picks have
                landed <strong style={{ color: theme.text }}>{(measured.mean * 100).toFixed(1)}%</strong>
                {" "}of the time ({(measured.lo * 100).toFixed(1)}–{(measured.hi * 100).toFixed(1)}%,
                clustered on the match). Every rung below is priced against that rate, not
                against a model number.
              </div>
            )}
          </>
        ) : (
          <div>The graded record has not loaded, so nothing here has been checked against it.</div>
        )}
      </div>

      {ladder.map((rung) => {
        const perLeg = measured ? measured.mean : null;
        // EV at the rate actually measured, with same-match correlation applied
        // where the rung shares a fixture. Arithmetic on a measurement.
        const atMeasured = perLeg === null ? null
          : parlayProbability(rung, () => perLeg);
        const evAtMeasured = parlayExpectedValue(atMeasured, rung.multiplier);
        const shortfall = perLeg !== null && rung.breakEven !== null
          ? rung.breakEven - perLeg : null;
        return (
          <div key={rung.size}
               style={{ background: theme.graphite, border: `1px solid ${theme.steel}`,
                        ...cardShape(theme.cornerStyle), ...elevation(), marginBottom: 10,
                        padding: "14px 16px" }}>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline",
                          gap: 10, marginBottom: 10 }}>
              <span style={{ color: theme.text, fontWeight: 700, fontSize: 14 }}>
                {rung.size} legs
              </span>
              <span style={{ color: theme.textDim, fontSize: 12 }}>
                {rung.multiplier ? `pays ${rung.multiplier}x` : "no multiplier on file"}
              </span>
              {rung.breakEven !== null && (
                <span style={{ color: theme.textDim, fontSize: 12 }}>
                  · needs <strong style={{ color: theme.text }}>
                    {(rung.breakEven * 100).toFixed(1)}%
                  </strong> a leg
                </span>
              )}
              {shortfall !== null && (
                <span style={{ fontSize: 12,
                               color: shortfall > 0 ? theme.bad : theme.good }}>
                  · {shortfall > 0
                      ? `${(shortfall * 100).toFixed(1)} points short of that`
                      : `${(-shortfall * 100).toFixed(1)} points clear of that`}
                </span>
              )}
            </div>

            <div style={{ fontSize: 12, color: theme.textDim, marginBottom: 8 }}>
              {rung.sharesAMatch
                ? `${rung.matches} fixture(s) for ${rung.size} legs — some legs share a match, `
                  + `so they are correlated and priced that way`
                : `${rung.matches} different fixtures, so the legs are independent`}
            </div>

            <div style={{ display: "grid", gap: 4, marginBottom: 10 }}>
              {rung.legs.map((leg, i) => (
                <div key={i} style={{ display: "flex", flexWrap: "wrap", gap: 8,
                                      fontSize: 12, color: theme.textDim }}>
                  <span style={{ color: theme.text, minWidth: 110 }}>{leg.name}</span>
                  <span>{leg.team} vs {leg.opponent}</span>
                  <span>{STAT_TYPES[leg.statType].label} maps 1-{leg.maps}</span>
                  <span>
                    line {leg.prop.line} · we say {leg.projection.toFixed(1)}
                    {" "}<strong style={{ color: theme.text }}>
                      {leg.projection > leg.prop.line ? "OVER" : "UNDER"}
                    </strong>
                  </span>
                  <span style={{ color: theme.textFaint }}>
                    {Math.abs(standardisedEdge(leg)).toFixed(2)} error-widths
                  </span>
                </div>
              ))}
            </div>

            <div style={{ borderTop: `1px solid ${theme.steel}`, paddingTop: 8,
                          fontSize: 12, color: theme.textDim }}>
              {validated ? (
                <span>Our chance: shown once wired to per-leg probabilities.</span>
              ) : (
                <span>Our chance of this landing: <strong>withheld</strong> — the ranking has
                  not been shown to separate, so a number here would be a label with nothing
                  behind it.</span>
              )}
              {atMeasured !== null && (
                <div style={{ marginTop: 4 }}>
                  At the {(perLeg * 100).toFixed(1)}% we have actually measured, this rung lands{" "}
                  <strong style={{ color: theme.text }}>{(atMeasured * 100).toFixed(1)}%</strong>
                  {" "}of the time and returns{" "}
                  <strong style={{ color: evAtMeasured >= 0 ? theme.good : theme.bad }}>
                    {evAtMeasured >= 0 ? "+" : ""}{(evAtMeasured * 100).toFixed(0)}%
                  </strong>
                  {" "}per unit staked.
                </div>
              )}
            </div>
          </div>
        );
      })}

      <div style={{ fontSize: 11.5, color: theme.textFaint, lineHeight: 1.6, marginTop: 4 }}>
        Multipliers are the published PrizePicks Power Play defaults and are not read from
        any feed — they move, and they differ by entry type and jurisdiction. Check them
        against your own board; the break-even beside each one recomputes from whatever is
        in force. Same-match legs are priced with the correlation measured from this app's
        own graded record (two legs on one match land the same way 55.2% of the time against
        50.0% across matches), not as independent bets.
      </div>
    </div>
  );
}

function RecordTab({ regionsData, regionList, weights, statType, isDesktop }) {
  const theme = useTheme();
  const cfg = STAT_TYPES[statType];
  const results = useGradedResults();

  const rows = useMemo(
    () => (results ? modelRecord(regionsData, regionList, results, weights, statType) : []),
    [results, regionsData, regionList, weights, statType]
  );

  const shell = (children) => (
    <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                  ...elevation(), padding: "18px 20px", fontSize: 12.5, color: theme.textFaint, lineHeight: 1.65 }}>
      {children}
    </div>
  );

  if (results === undefined) return shell("Loading the graded record…");
  if (!results || !(results.graded || []).length) {
    return shell(<>
      <strong style={{ color: theme.text }}>No graded lines yet.</strong> The record builds itself: every
      refresh appends the board to <code>props_history.jsonl</code>, and a line becomes gradeable once its
      match has been played and scraped. Nothing to do but keep refreshing — and nothing here should be
      claimed until this screen says otherwise.
    </>);
  }

  const decided = rows.length;
  const won = rows.filter((r) => r.won).length;
  const buckets = recordByEdge(rows);
  const windows = recordByWindow(rows);
  const vsLine = recordVsLine(rows);
  const cal = calibration(rows);
  const graded = results.graded.length;

  // Breakeven at -110, the standard price. A win rate below this loses
  // money however good it looks next to 50%, and 50% is the number
  // people instinctively compare against -- so it is stated, not left
  // for the reader to remember.
  const BREAKEVEN = 0.524;

  return (
    <div>
      {shell(<>
        <div style={{ color: theme.text, fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
          {graded} line{graded === 1 ? "" : "s"} graded · {decided} where the projection disagreed with the line
        </div>
        {decided < RECORD_MIN_SAMPLE ? (
          <>Too few to report a rate. Below {RECORD_MIN_SAMPLE} decided bets a win rate is noise wearing a
          decimal point, and the whole reason for keeping this record is to avoid claiming things the
          evidence does not support. It will fill on its own.</>
        ) : (
          <>The projection's side won <strong style={{ color: theme.text }}>{won}</strong> of{" "}
          <strong style={{ color: theme.text }}>{decided}</strong> ({(100 * won / decided).toFixed(1)}%).
          Each projection was rebuilt from data that predated its own match, so this is not the model
          grading its own homework — but it is a small sample, and a sample this size moves several points
          on a handful of results.</>
        )}
      </>)}

      {/* The only question that decides whether any of this is worth
          paying for, and the app never asked it -- it lived in a dev
          script. Clustered on the match, and signed so POSITIVE means
          the line was closer: there is no reading of this that
          flatters the model by accident. */}
      {vsLine.winRate && vsLine.maeGap && (
        <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                      ...elevation(), marginTop: 12, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${theme.steel}`, fontSize: 12.5, color: theme.text }}>
            Against the posted line
            <span style={{ color: theme.textFaint, fontSize: 11 }}>
              {" "}· {vsLine.matches} matches, {vsLine.props} props
            </span>
          </div>
          {[
            {
              label: "Win rate when we disagreed",
              value: `${(100 * vsLine.winRate.mean).toFixed(1)}%`,
              ci: `${(100 * vsLine.winRate.lo).toFixed(1)}% to ${(100 * vsLine.winRate.hi).toFixed(1)}%`,
              // Settled only when the whole interval clears breakeven.
              // A point estimate above it with an interval straddling
              // it is not a profitable model, it is an unfinished
              // measurement.
              verdict: vsLine.winRate.lo > BREAKEVEN ? "beats breakeven"
                : vsLine.winRate.hi < BREAKEVEN ? "below breakeven"
                : "cannot tell yet",
              good: vsLine.winRate.lo > BREAKEVEN,
              bad: vsLine.winRate.hi < BREAKEVEN,
              note: `breakeven at -110 is ${(100 * BREAKEVEN).toFixed(1)}%`,
            },
            {
              label: "Our error minus the line's",
              value: `${vsLine.maeGap.mean > 0 ? "+" : ""}${vsLine.maeGap.mean.toFixed(3)}`,
              ci: `${vsLine.maeGap.lo.toFixed(2)} to ${vsLine.maeGap.hi.toFixed(2)}`,
              verdict: vsLine.maeGap.hi < 0 ? "we forecast better"
                : vsLine.maeGap.lo > 0 ? "the line forecasts better"
                : "cannot tell yet",
              good: vsLine.maeGap.hi < 0,
              bad: vsLine.maeGap.lo > 0,
              note: "positive means the line was closer to the truth",
            },
          ].map((row) => (
            <div key={row.label} style={{ padding: "11px 16px", borderBottom: `1px solid ${theme.steel}` }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <div style={{ flex: 1, fontSize: 12.5, color: theme.textDim, minWidth: 150 }}>{row.label}</div>
                <div className="kp-num" style={{ fontSize: 15, fontWeight: 700, color: theme.text }}>{row.value}</div>
                <div className="kp-num" style={{ fontSize: 11, color: theme.textFaint, minWidth: 130, textAlign: "right" }}>
                  95% CI {row.ci}
                </div>
                <div style={{ fontSize: 11, fontWeight: 600, minWidth: 128, textAlign: "right",
                              color: row.good ? theme.good : row.bad ? theme.bad : theme.textFaint }}>
                  {row.verdict}
                </div>
              </div>
              <div style={{ fontSize: 10.5, color: theme.textFaint, marginTop: 4 }}>{row.note}</div>
            </div>
          ))}
          <div style={{ padding: "10px 16px", fontSize: 11, color: theme.textFaint, lineHeight: 1.6 }}>
            Both intervals are clustered on the match, not the prop. Ten props from one map share its
            rounds and how one-sided it was, so counting them as ten independent results makes every
            interval about three times too narrow — which is how eight matches once got reported here
            as though they meant something.
          </div>
        </div>
      )}

      {cal && cal.withLine > 0 && (
        <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                      ...elevation(), marginTop: 12, padding: "12px 16px", fontSize: 12, color: theme.textDim, lineHeight: 1.65 }}>
          <span style={{ color: theme.text, fontWeight: 600 }}>Is the number centred?</span>{" "}
          Across {cal.n} graded projections the average miss is{" "}
          <span className="kp-num" style={{ color: theme.text }}>
            {cal.mean > 0 ? "+" : ""}{cal.mean.toFixed(2)}
          </span>{" "}
          — {Math.abs(cal.mean) < 0.25 ? "essentially centred" : cal.mean > 0 ? "projecting high" : "projecting low"}.
          We landed closer than the line on{" "}
          <span className="kp-num" style={{ color: theme.text }}>
            {(100 * cal.closer / cal.withLine).toFixed(1)}%
          </span>{" "}
          of them. Average error cannot see this on its own: a model wrong by the same amount every
          time and one wrong in random directions score identically, and only the first turns into the
          same recommendation on every player.
        </div>
      )}

      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                    ...elevation(), marginTop: 12, overflow: "hidden" }}>
        <div style={{ padding: "12px 16px", borderBottom: `1px solid ${theme.steel}`, fontSize: 12.5, color: theme.text }}>
          By map window
        </div>
        {windows.map((w) => {
          const enough = w.n >= RECORD_MIN_SAMPLE;
          return (
            <div key={w.maps} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 16px",
                                       borderBottom: `1px solid ${theme.steel}` }}>
              <div style={{ flex: 1, fontSize: 12.5, color: theme.textDim }}>
                {w.maps > 0 ? mapWindowLabel(w.maps) : "window not stated"}
              </div>
              <div className="kp-num" style={{ fontSize: 12, color: theme.textFaint, minWidth: 70, textAlign: "right" }}>
                {w.won}/{w.n}
              </div>
              <div className="kp-num" style={{ fontSize: 14, fontWeight: 700, minWidth: 64, textAlign: "right",
                                               color: enough ? (w.rate > 0.5 ? theme.good : w.rate < 0.5 ? theme.bad : theme.textDim)
                                                             : theme.textFaint }}>
                {enough ? `${(100 * w.rate).toFixed(0)}%` : "\u2014"}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
                    ...elevation(), marginTop: 12, overflow: "hidden" }}>
        <div style={{ padding: "12px 16px", borderBottom: `1px solid ${theme.steel}`, fontSize: 12.5, color: theme.text }}>
          By how far the projection disagreed
        </div>
        {buckets.map((b) => {
          const label = b.hi === Infinity ? `${b.lo}+ ${cfg.label.toLowerCase()}`
            : `${b.lo}–${b.hi} ${cfg.label.toLowerCase()}`;
          const enough = b.n >= RECORD_MIN_SAMPLE;
          return (
            <div key={b.lo} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 16px",
                                     borderBottom: `1px solid ${theme.steel}` }}>
              <div style={{ flex: 1, fontSize: 12.5, color: theme.textDim }}>{label}</div>
              <div className="kp-num" style={{ fontSize: 12, color: theme.textFaint, minWidth: 70, textAlign: "right" }}>
                {b.won}/{b.n}
              </div>
              <div className="kp-num" style={{ fontSize: 14, fontWeight: 700, minWidth: 64, textAlign: "right",
                                               color: enough ? (b.rate > 0.5 ? theme.good : b.rate < 0.5 ? theme.bad : theme.textDim)
                                                             : theme.textFaint }}>
                {enough ? `${(100 * b.rate).toFixed(0)}%` : "—"}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ fontSize: 11, color: theme.textFaint, marginTop: 10, lineHeight: 1.65 }}>
        If the model is worth anything, that column climbs as the disagreement grows. If it does not, a
        confident edge is worth no more than a marginal one. Rates are withheld below {RECORD_MIN_SAMPLE}
        {" "}bets per row, in both tables — a window with four bets in it has no record, only a number.
        {" "}Pushes and projections landing exactly on the line are excluded — neither is a bet.
        {isDesktop && " Nothing here accounts for the price paid, so a win rate above 50% is not by itself a profit."}
      </div>
    </div>
  );
}

function StandingsTab({ teams, pastMatches, upcomingMatches, regionsData, regionList, regionLabels, isDesktop }) {
  const theme = useTheme();
  const standings = computeStandings(teams, pastMatches);
  const power = computeEloRatings(regionsData, regionList);

  // Combine completed playoff results and scheduled-but-unplayed playoff
  // matches into one normalized list so the bracket status reads as a
  // single coherent picture instead of being split across tabs — grouped
  // and ordered by the same tournament-progression logic used in
  // Future/Past Results, not naive chronological/alphabetical order.
  const playoffPast = pastMatches
    .filter((m) => m.week && playoffRoundRank(m.week) !== null)
    .map((m) => ({ ...m, roundLabel: m.week, played: true }));
  const playoffUpcoming = (upcomingMatches || [])
    .filter((m) => (m.block || m.week) && playoffRoundRank(m.block || m.week) !== null)
    .map((m) => ({ ...m, roundLabel: m.block || m.week, played: false }));
  const playoffGroups = sortGroupsByProgression(groupByLabel([...playoffPast, ...playoffUpcoming], (m) => m.roundLabel));
  const inPlayoffs = playoffGroups.length > 0;

  if (standings.every((s) => s.wins === 0 && s.losses === 0) && !inPlayoffs) {
    return (
      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: "20px 16px", textAlign: "center" }}>
        <div style={{ fontSize: 13, color: theme.textDim }}>No completed matches yet this split.</div>
      </div>
    );
  }

  return (
    <div>
      <div style={isDesktop ? { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, alignItems: "start" } : undefined}>
        <div className={bracketClass(theme)} style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme), overflow: "hidden", marginBottom: isDesktop ? 0 : 20 }}>
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${theme.steel}`, fontSize: 13, fontWeight: 600, color: theme.text, fontFamily: "'Inter', sans-serif" }}>
            Standings{inPlayoffs ? <span style={{ color: theme.textFaint, fontWeight: 400 }}> · regular season (final)</span> : ""}
          </div>
          {inPlayoffs && (
            <div style={{ padding: "8px 16px", fontSize: 11, color: theme.textDim, lineHeight: 1.5, borderBottom: `1px solid ${theme.steelSoft}` }}>
              This region has entered playoffs — the table below reflects the completed regular season only. See the bracket below for playoff results.
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 40px 40px 56px", padding: "8px 16px", fontSize: 11, color: theme.textFaint, fontFamily: "'Inter', sans-serif", fontWeight: 500 }}>
            <span>Team</span><span style={{ textAlign: "right" }}>W</span><span style={{ textAlign: "right" }}>L</span><span style={{ textAlign: "right" }}>Games</span>
          </div>
          {standings.map((s, i) => (
            <div key={s.team} style={{ display: "grid", gridTemplateColumns: "1fr 40px 40px 56px", padding: "10px 16px", fontSize: 13, borderTop: `1px solid ${theme.steelSoft}`, alignItems: "center" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 10, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace", width: 14 }}>{i + 1}</span>
                <span style={{ width: 5, height: 16, borderRadius: 2, background: teams[s.team] ? teams[s.team].color : theme.textFaint }} />
                <span style={{ fontFamily: "'Fraunces', serif", fontWeight: 600 }}>{s.team}</span>
              </span>
              <span style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", color: theme.good }}>{s.wins}</span>
              <span style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", color: theme.bad }}>{s.losses}</span>
              <span style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", color: theme.textDim, fontSize: 11 }}>{s.gameWins}-{s.gameLosses}</span>
            </div>
          ))}
        </div>

        <div className={bracketClass(theme)} style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme), overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${theme.steel}`, fontSize: 13, fontWeight: 600, color: theme.text, fontFamily: "'Inter', sans-serif" }}>
            Power ranking <span style={{ color: theme.textFaint, fontWeight: 400 }}>· all regions</span>
          </div>
          <div style={{ padding: "8px 16px", fontSize: 11, color: theme.textFaint, lineHeight: 1.5, borderBottom: `1px solid ${theme.steelSoft}` }}>
            Elo-style rating pooled across every region — meaningful for cross-region comparison once international matches (MSI, Worlds, Champions) start feeding in. Until then it tracks closely with in-region record, which is expected.
          </div>
          {power.map((p, i) => (
            <div key={p.team} style={{ display: "grid", gridTemplateColumns: "1fr 70px 60px", padding: "10px 16px", fontSize: 13, borderTop: `1px solid ${theme.steelSoft}`, alignItems: "center" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 10, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace", width: 14 }}>{i + 1}</span>
                <span style={{ fontFamily: "'Fraunces', serif", fontWeight: 600 }}>{p.team}</span>
              </span>
              <span style={{ textAlign: "right", fontSize: 10, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace" }}>{regionLabels[p.region] || p.region}</span>
              <span style={{ textAlign: "right", fontFamily: "'IBM Plex Mono', monospace", color: theme.accent, fontWeight: 700 }}>{Math.round(p.rating)}</span>
            </div>
          ))}
        </div>
      </div>

      {inPlayoffs && (
        <div className={bracketClass(theme)} style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme), overflow: "hidden", marginTop: 20 }}>
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${theme.steel}`, fontSize: 13, fontWeight: 600, color: theme.text, fontFamily: "'Inter', sans-serif" }}>
            Playoffs
          </div>
          {playoffGroups.map(([label, matches]) => (
            <div key={label}>
              <div style={{ padding: "8px 14px 4px", fontSize: 10, letterSpacing: 1.5, color: theme.accent, fontFamily: "'IBM Plex Mono', monospace", borderTop: `1px solid ${theme.steelSoft}`, textTransform: "uppercase" }}>
                {label}
              </div>
              {matches.map((m, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 14px", fontSize: 12 }}>
                  <span>
                    <span style={{ color: teams[m.teamA] ? teams[m.teamA].color : theme.text, fontWeight: m.winner === m.teamA ? 700 : 400 }}>{m.teamA}</span>
                    <span style={{ color: theme.textFaint }}> vs </span>
                    <span style={{ color: teams[m.teamB] ? teams[m.teamB].color : theme.text, fontWeight: m.winner === m.teamB ? 700 : 400 }}>{m.teamB}</span>
                  </span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: m.played ? theme.textDim : theme.accent }}>
                    {m.played ? `${m.score} · ${m.winner} won` : (m.date || "TBD")}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   HEAD-TO-HEAD — computed entirely from pastMatches already in
   memory, no extra data needed. Scoped to whatever pastMatches was
   passed in (a single region/split), which is correct since two
   teams only meet within the same region's round robin anyway.
   ============================================================ */

function getHeadToHead(pastMatches, teamA, teamB) {
  const meetings = pastMatches.filter(
    (m) => (m.teamA === teamA && m.teamB === teamB) || (m.teamA === teamB && m.teamB === teamA)
  );
  meetings.sort((a, b) => ((a.date || "") < (b.date || "") ? 1 : (a.date || "") > (b.date || "") ? -1 : 0)); // newest first
  let winsA = 0, winsB = 0;
  for (const m of meetings) {
    if (m.winner === teamA) winsA++;
    else if (m.winner === teamB) winsB++;
  }
  return { meetings, winsA, winsB };
}

// Champion-pool CONSISTENCY — a genuinely forward-usable confidence
// signal, unlike anything that would need to guess an opponent's future
// draft. Uses only a player's OWN past per-champion performance (always
// knowable ahead of time) to measure how much their output swings
// depending on what they're playing. A low coefficient of variation
// means their numbers hold up regardless of champion; a high one means
// their projection should be trusted less tightly, since the actual
// draft (unknown until minutes before the game) could meaningfully move
// the real outcome either direction.
function championPoolConsistency(playerName, championStats, statKey, minGamesPerChampion = 2) {
  if (!championStats || !championStats.player_champions) return null;
  const prefix = `${playerName}|`;
  const entries = Object.entries(championStats.player_champions)
    .filter(([key, stats]) => key.startsWith(prefix) && stats.g >= minGamesPerChampion)
    .map(([key, stats]) => ({ champion: key.slice(prefix.length), ...stats }));
  if (entries.length < 2) return null; // need 2+ distinct, reasonably-sampled champions to say anything about consistency at all

  const totalGames = entries.reduce((sum, e) => sum + e.g, 0);
  const weightedMean = entries.reduce((sum, e) => sum + e[statKey] * e.g, 0) / totalGames;
  const weightedVariance = entries.reduce((sum, e) => sum + e.g * Math.pow(e[statKey] - weightedMean, 2), 0) / totalGames;
  const stdDev = Math.sqrt(weightedVariance);
  const coefficientOfVariation = weightedMean > 0 ? stdDev / weightedMean : null;

  return {
    championsConsidered: entries.length,
    totalGames,
    weightedMean,
    stdDev,
    coefficientOfVariation,
    entries: entries.sort((a, b) => b.g - a.g), // most-played first
  };
}

function HeadToHeadCard({ teams, pastMatches, teamA, teamB, bare = false }) {
  const theme = useTheme();
  const { meetings, winsA, winsB } = getHeadToHead(pastMatches, teamA, teamB);
  const colorA = teams[teamA] ? teams[teamA].color : theme.text;
  const colorB = teams[teamB] ? teams[teamB].color : theme.text;

  const content = (
    <>
      <div style={{ fontSize: 13, fontWeight: 600, color: theme.text, fontFamily: "'Inter', sans-serif", marginBottom: 12 }}>Head-to-head</div>
      {meetings.length === 0 ? (
        <div style={{ fontSize: 12, color: theme.textDim }}>No prior meetings this split.</div>
      ) : (
        <>
          {/* Same colour-bar treatment as every other team reference, so
              the record reads as the emphasis rather than the names. */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 12 }}>
            <TeamTag name={teamA} color={colorA} dim={winsA < winsB} />
            <span className="kp-num" style={{ fontSize: 20, fontWeight: 700, color: theme.text, flexShrink: 0 }}>{winsA} – {winsB}</span>
            <TeamTag name={teamB} color={colorB} dim={winsB < winsA} />
          </div>
          {meetings.map((m, i) => (
            <div
              key={i}
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, padding: "7px 0",
                borderTop: i > 0 ? `1px solid ${theme.steelSoft}` : "none", color: theme.textDim,
              }}
            >
              <span>{m.week ? `${m.week} · ` : ""}{m.date}</span>
              <span className="kp-num" style={{ color: theme.textDim }}>
                <span style={{ color: theme.text, fontWeight: 600 }}>{m.winner}</span> won {m.score}
              </span>
            </div>
          ))}
        </>
      )}
    </>
  );

  if (bare) return <div style={{ padding: "12px 14px" }}>{content}</div>;
  return (
    <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: 14, marginBottom: 12 }}>
      {content}
    </div>
  );
}

function MatchupPanel({ teams, pastMatches, teamA, teamB, games, weights, statType }) {
  const theme = useTheme();
  if (!teams[teamA] || !teams[teamB]) {
    return (
      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: "12px 14px", fontSize: 12, color: theme.textFaint }}>
        Roster data not loaded for one of these teams yet.
      </div>
    );
  }
  const totalsA = teams[teamA].players.reduce((s, p) => s + project(teams, pastMatches, p, teamA, teamB, games, weights, statType).total, 0);
  const totalsB = teams[teamB].players.reduce((s, p) => s + project(teams, pastMatches, p, teamB, teamA, games, weights, statType).total, 0);
  return (
    <div>
      <HeadToHeadCard teams={teams} pastMatches={pastMatches} teamA={teamA} teamB={teamB} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {[[teamA, teamB, totalsA], [teamB, teamA, totalsB]].map(([team, opp, total], i) => (
          <div key={i} className={bracketClass(theme)} style={{ background: theme.graphite, border: `1px solid ${teams[team].color}33`, ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme, teams[team].color), overflow: "hidden" }}>
            <div style={{ padding: "10px 12px", background: `${teams[team].color}18`, borderBottom: `1px solid ${teams[team].color}33`, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span style={{ fontWeight: 700, fontSize: 14, fontFamily: "'Fraunces', serif" }}>{team}</span>
              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, color: teams[team].color }}>Σ {total.toFixed(1)}</span>
            </div>
            {likelyStarters(teams[team].players).map((p) => (
              <PlayerRow key={p.name} teams={teams} pastMatches={pastMatches} p={p} team={team} opponentTeam={opp} games={games} weights={weights} teamColor={teams[team].color} statType={statType} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function WeightControls({ weights, onChangeWeight, expanded, onToggleExpanded, statLabel, defaults }) {
  const theme = useTheme();
  /* The collapsed state used to print every weight as one long mono line
     clipped with an ellipsis ("...defaults · Hist…"), which told you
     nothing and looked like a rendering fault. What actually matters when
     this panel is shut is whether the model is running as tuned or has
     been altered — so say that, and offer the way back. */
  const changed = defaults
    ? Object.keys(defaults).filter((k) => weights[k] !== defaults[k])
    : [];
  const LABELS = { history: "History", opponent: "Opponent", kp: "KP", recencyHalfLife: "Recency", patchDiscount: "Patch", career: "Career", share: "Team share", shrink: "Shrinkage" };
  return (
    <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: 18, marginBottom: 22 }}>
      <div
        className="kp-clickable"
        onClick={onToggleExpanded}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: expanded ? 12 : 0 }}
      >
        <div style={{ fontSize: 13, color: theme.text, fontFamily: "'Inter', sans-serif", fontWeight: 600 }}>
          Advanced tuning <span style={{ color: theme.textFaint, fontWeight: 400 }}>· {statLabel} · optional</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, color: theme.textDim, transform: expanded ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}>▾</span>
        </div>
      </div>
      {!expanded && (
        <div style={{ fontSize: 11, color: theme.textFaint, lineHeight: 1.55, marginTop: 2 }}>
          {changed.length === 0 ? (
            <>Running on backtest-tuned defaults.</>
          ) : (
            <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span className="kp-chip" style={{ background: `${theme.accent}1A`, color: theme.accent, border: `1px solid ${theme.accent}33` }}>
                {changed.length} adjusted
              </span>
              <span>{changed.map((k) => LABELS[k] || k).join(", ")}</span>
              <button
                className="kp-btn"
                onClick={(e) => { e.stopPropagation(); changed.forEach((k) => onChangeWeight(k, defaults[k])); }}
                style={{
                  background: "none", border: `1px solid ${theme.steel}`, borderRadius: 14,
                  color: theme.textDim, fontSize: 10.5, padding: "3px 10px", cursor: "pointer",
                  fontFamily: "'Inter', sans-serif", fontWeight: 600,
                }}
              >
                Reset
              </button>
            </span>
          )}
        </div>
      )}
      {expanded && (
        <>
          <div style={{ fontSize: 11, color: theme.textFaint, lineHeight: 1.5, marginBottom: 14 }}>
            The defaults here were already tuned against real historical results (see optimize_weights.py) — only touch these if you want to experiment.
          </div>
          <Slider label="Historical (prior split) weight" value={weights.history} onChange={(v) => onChangeWeight("history", v)} min={0} max={1} step={0.05} format={(v) => `${Math.round(v * 100)}% history / ${Math.round((1 - v) * 100)}% this split`}
            tooltip="How much weight the player's PREVIOUS split gets vs. their CURRENT split. Higher means the model trusts their track record more than what they've done so far this split." />
          <Slider label="Opponent-strength adjustment" value={weights.opponent} onChange={(v) => onChangeWeight("opponent", v)} min={0} max={2} step={0.1} format={(v) => `${v.toFixed(1)}×`}
            tooltip="How much the opponent's own strength shifts the projection up or down. 0 means the opponent is ignored entirely; higher values react more strongly to a weak or strong matchup." />
          <Slider label="Kill-participation influence" value={weights.kp} onChange={(v) => onChangeWeight("kp", v)} min={0} max={1} step={0.05} format={(v) => (v === 0 ? "off — measured worthless once it stopped reading the future" : `${Math.round(v * 100)}%`)}
            tooltip="How much the player's own kill participation pulls the projection above or below the base rate. Ships at 0 for every game: it used to be measured against a whole-season figure that included the match being predicted, and once that was fixed it scored no better than being switched off." />
          <Slider
            label="Recency half-life (this split)"
            value={weights.recencyHalfLife}
            onChange={(v) => onChangeWeight("recencyHalfLife", v)}
            min={2}
            max={20}
            step={1}
            format={(v) => (v >= 20 ? "flat average (no decay)" : `${v} match${v > 1 ? "es" : ""} — recent form weighted higher`)}
            tooltip="How quickly older matches this split stop mattering. A low number means only the last few games really count; a high number treats the whole split as equally relevant."
          />
          <Slider
            label="Off-patch discount"
            value={weights.patchDiscount}
            onChange={(v) => onChangeWeight("patchDiscount", v)}
            min={0}
            max={1}
            step={0.05}
            format={(v) => (v === 0 ? "ignore patch" : `-${Math.round(v * 100)}% weight for matches on an older patch`)}
            tooltip="How much less a match counts if it was played on an older game patch than the one being projected for."
          />
          <Slider
            label="Thin-sample shrinkage"
            value={weights.shrink || 0}
            onChange={(v) => onChangeWeight("shrink", v)}
            min={0}
            max={16}
            step={1}
            format={(v) => (v === 0 ? "off — trust the sample however small" : `half-trust at ${v} map${v > 1 ? "s" : ""} on record`)}
            tooltip="How hard a player with little history is pulled toward the league's average player. A player with this many maps on record is trusted half as much as the league; with far more, not at all. Off for LoL, which already has career data to ground a newcomer."
          />
          <Slider
            label="Team-share weight"
            value={weights.share || 0}
            onChange={(v) => onChangeWeight("share", v)}
            min={0}
            max={1}
            step={0.05}
            format={(v) => (v === 0 ? "off — player's own rate only" : `${Math.round(v * 100)}% from share of team total`)}
            tooltip="Blends in a second estimate built the other way round: the player's share of their team's total, times how much a map in this league usually produces. It helps most on deaths, and is off for LoL, where the opponent adjustment already does this job."
          />
          <Slider
            label="Career (gol.gg baseline) weight"
            value={weights.career}
            onChange={(v) => onChangeWeight("career", v)}
            min={0}
            max={0.8}
            step={0.05}
            format={(v) => (v === 0 ? "off — recent form / split history only" : `${Math.round(v * 100)}% career baseline blended in`)}
            tooltip="How much the player's independently-sourced career baseline (not just this app's own tracked matches) is blended into the final number."
          />
        </>
      )}
    </div>
  );
}

/* ---------- Games-in-series control — pulled out of the weight tuning
   panel entirely, since this is a "what am I asking for" choice used
   often, not a model-tuning knob touched rarely. Always visible. ---------- */

function GamesControl({ theme, games, setGames }) {
  return (
    <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: 14, marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: theme.text, fontFamily: "'Inter', sans-serif", marginBottom: 12 }}>Games in series</div>
      <div style={{ display: "flex", gap: 6 }}>
        {[1, 2, 3].map((n) => (
          <button
            key={n}
            className="kp-btn"
            onClick={() => setGames(n)}
            style={{
              flex: 1, padding: "11px 0", borderRadius: 6, border: "1px solid " + (games === n ? theme.accent : theme.steel),
              background: games === n ? theme.accentSoft : "transparent", color: games === n ? theme.accent : theme.textDim,
              fontSize: 15, fontWeight: 700, cursor: "pointer", fontFamily: "'IBM Plex Mono', monospace",
            }}
          >
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------- Future tab (card list, like Past, predicted only) ---------- */

function FutureMatchCard({ teams, pastMatches, match, weights, statType, games, game }) {
  const theme = useTheme();
  const homeRegion = useHomeRegion();
  const propsData = useProps();
  const propsFresh = propsAreFresh(propsData);
  const propsAge = propsAgeMinutes(propsData);
  const [open, setOpen] = useState(false);
  const teamKeys = [match.teamA, match.teamB];

  // Only refused when NEITHER side is rostered, which leaves nothing to
  // project. One unknown side used to refuse the whole fixture, and CS2
  // tracks about fifty teams against a hundred in its fixture list, so
  // three quarters of a real board rendered as this message — including
  // cards that carried posted lines.
  const knownTeams = teamKeys.filter((t) => teams[t]);
  if (knownTeams.length === 0) {
    return (
      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), marginBottom: 10, padding: "12px 14px", fontSize: 12, color: theme.textFaint }}>
        {match.teamA} vs {match.teamB} — no roster data for either team yet.
      </div>
    );
  }
  const halfKnown = knownTeams.length === 1;

  const cfg = STAT_TYPES[statType];
  const rows = knownTeams.flatMap((team) => {
    const opp = team === match.teamA ? match.teamB : match.teamA;
    return likelyStarters(teams[team].players).map((p) => {
      const breakdown = project(teams, pastMatches, p, team, opp, games, weights, statType);
      // A posted line names the map window of the fixture it belongs to,
      // and the projection compared against it has to cover the same maps
      // — which is not necessarily the number the selector is showing,
      // since a Bo5 and a Bo3 can be on screen together. breakdown.perGame
      // is a per-map rate, so scaling it by the line's own window is the
      // whole of the conversion.
      // _sortKey, not date: formatUpcoming() replaces `date` with a display
      // string ("Sep 21") and keeps the ISO timestamp here. Passing the
      // display string is not a parse error that announces itself --
      // new Date("Sep 21") is a valid date in 2001 -- so every line falls
      // outside the window and the app shows nothing, with no complaint.
      // Every window posted for this player in this fixture, not just
      // one: map 1 and maps 1-2 are separate markets and the card shows
      // both rather than silently picking a side.
      const posted = propsFor(propsData, game, p.name, statType,
                              match._sortKey || match.date);
      // breakdown.perGame is a per-map rate, so each window's projection
      // is that rate times its own map count -- one breakdown answers
      // them all, which is why it is computed once above.
      const propProjections = posted.map((w) => projectionOverWindow(breakdown, w));
      return { team, name: p.name, role: p.role, proj: breakdown.total,
               props: posted, propProjections, player: p, breakdown };
    });
  });
  const totalProj = rows.reduce((s, row) => s + row.proj, 0);

  return (
    <div className={bracketClass(theme)} style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme), marginBottom: 10, overflow: "hidden" }}>
      <div
        className="kp-clickable"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`${match.teamA} versus ${match.teamB}, projected ${totalProj.toFixed(0)} ${cfg.label.toLowerCase()} combined. Activate for player-level detail.`}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); }
        }}
        style={{ cursor: "pointer", padding: "15px 18px", display: "flex", alignItems: "center", gap: 14 }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
            <TeamTag name={match.teamA} color={teamColorOf(teams, match.teamA, theme.textDim)}
                     region={homeRegion(match.teamA)} />
            <span style={{ color: theme.textFaint, fontSize: 10.5, fontWeight: 500 }}>vs</span>
            <TeamTag name={match.teamB} color={teamColorOf(teams, match.teamB, theme.textDim)}
                     region={homeRegion(match.teamB)} />
          </div>
          <div style={{ marginTop: 7, fontSize: 11.5, color: theme.textFaint, display: "flex", alignItems: "center", gap: 7 }}>
            <span>{match.date}{match.time ? ` · ${match.time}` : ""}</span>
            {halfKnown && (
              <>
                <span aria-hidden="true" style={{ opacity: 0.5 }}>•</span>
                <span title={`No roster for ${teamKeys.find((t) => !teams[t])}, so only ${knownTeams[0]} is projected and opponent strength is not applied.`}
                      style={{ color: theme.accent, opacity: 0.85 }}>
                  {knownTeams[0]} only
                </span>
              </>
            )}
            <span aria-hidden="true" style={{ opacity: 0.5 }}>•</span>
            <span>{games} game{games === 1 ? "" : "s"}</span>
          </div>
        </div>
        {/* Labelled for the side it actually covers. A half-known fixture's
            total is one team's, and calling it the match total would make
            it look like the two teams were projected to score the same. */}
        <StatReadout value={totalProj.toFixed(0)}
                     label={halfKnown ? `proj ${cfg.label} · ${knownTeams[0]}` : `proj ${cfg.label}`}
                     size={28} />
        <Chevron open={open} color={theme.textFaint} />
      </div>
      {open && (
        <div style={{ borderTop: `1px solid ${theme.steel}` }}>
          <HeadToHeadCard teams={teams} pastMatches={pastMatches} teamA={match.teamA} teamB={match.teamB} bare />
          {rows.map((row) => (
            <MatchPlayerRow
              key={row.team + row.name}
              theme={theme}
              teamColor={teams[row.team].color}
              name={row.name}
              role={row.role}
              stats={[{ label: "PROJ", value: row.proj.toFixed(1), color: theme.accent, big: true }]}
              props={row.props}
              propProjections={row.propProjections}
              propsFresh={propsFresh}
              propsAge={propsAge}
              r={row.breakdown}
              p={row.player}
              cfg={cfg}
              games={games}
              pastMatches={pastMatches}
              team={row.team}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- Grouping helper ---------- */

function groupByLabel(items, labelFn) {
  const groups = [];
  const index = {};
  for (const item of items) {
    const label = labelFn(item) || "Other";
    if (!(label in index)) {
      index[label] = groups.length;
      groups.push([label, []]);
    }
    groups[index[label]][1].push(item);
  }
  return groups;
}

/* ============================================================
   PLAYOFF-AWARE ORDERING — groupByLabel above just buckets items in
   whatever order their label was first encountered, which tracks
   correctly with tournament progression for "Week 1, Week 2, ..."
   labels (since match dates are already monotonic with week number)
   but isn't reliable once playoff round names enter the picture —
   a double-elimination bracket's lower-bracket rounds can chronologically
   interleave with upper-bracket rounds in ways that don't read as a
   sensible progression if you just sort by whichever round's first
   match happened earliest. This gives playoff-style labels an explicit
   canonical rank instead, confirmed against a real live TCL 2026 Summer
   bracket (Upper/Lower Bracket Quarterfinals/Semifinals/Final, Grand
   Final) rather than guessed — falls back to alphabetical for any label
   that doesn't match a known pattern, so an unrecognized label degrades
   gracefully instead of crashing or vanishing.
   ============================================================ */

const PLAYOFF_STAGE_PATTERNS = [
  { test: (l) => /swiss|group stage|regular season/.test(l), rank: 0 },
  { test: (l) => /play-?in/.test(l), rank: 1 },
  { test: (l) => /upper.*(round\s*1|ro\s*16)/.test(l), rank: 10 },
  { test: (l) => /lower.*(round\s*1|ro\s*16)/.test(l), rank: 11 },
  { test: (l) => /upper.*(quarterfinal|ro\s*8)/.test(l), rank: 20 },
  { test: (l) => /lower.*round\s*2/.test(l), rank: 21 },
  { test: (l) => /lower.*(quarterfinal|ro\s*8)/.test(l), rank: 22 },
  { test: (l) => /^(quarterfinal|ro\s*8)/.test(l), rank: 20 }, // unqualified — single-elimination regions
  { test: (l) => /upper.*(semifinal|ro\s*4)/.test(l), rank: 30 },
  { test: (l) => /lower.*(semifinal|ro\s*4)/.test(l), rank: 32 },
  { test: (l) => /^(semifinal|ro\s*4)/.test(l), rank: 30 },
  { test: (l) => /upper.*final/.test(l), rank: 40 },
  { test: (l) => /lower.*final/.test(l), rank: 42 },
  { test: (l) => /grand final|^final(s)?$/.test(l), rank: 50 },
];

function playoffRoundRank(label) {
  const l = label.toLowerCase();
  for (const { test, rank } of PLAYOFF_STAGE_PATTERNS) {
    if (test(l)) return rank;
  }
  return null; // not a recognized playoff-stage label
}

function sortGroupsByProgression(groups) {
  return groups.slice().sort(([labelA], [labelB]) => {
    const weekA = /^Week (\d+)$/i.exec(labelA);
    const weekB = /^Week (\d+)$/i.exec(labelB);
    if (weekA && weekB) return parseInt(weekA[1], 10) - parseInt(weekB[1], 10);
    if (weekA && !weekB) return -1; // regular-season weeks always precede playoff rounds
    if (!weekA && weekB) return 1;
    const rankA = playoffRoundRank(labelA);
    const rankB = playoffRoundRank(labelB);
    if (rankA !== null && rankB !== null) return rankA - rankB;
    if (rankA !== null) return -1; // a recognized playoff stage precedes an unrecognized label
    if (rankB !== null) return 1;
    return labelA.localeCompare(labelB);
  });
}

function WeekHeader({ label }) {
  const theme = useTheme();
  return (
    <div style={{ fontSize: 12, fontWeight: 600, color: theme.textDim, fontFamily: "'Inter', sans-serif", margin: "18px 0 10px" }}>
      {label}
    </div>
  );
}

// Small, consistent visual language for tournament stage across the
// app — regular season stays quiet, playoffs/finals stand out, since
// those are the matches someone browsing past results is most likely
// looking for specifically.
const STAGE_STYLE = {
  finals: { label: "Finals", weight: 700 },
  playoffs: { label: "Playoffs", weight: 600 },
  "play-in": { label: "Play-in", weight: 500 },
  cup: { label: "Cup", weight: 500 },
  preseason: { label: "Preseason", weight: 500 },
  regular_season: { label: "Regular season", weight: 500 },
};

function StageBadge({ stage }) {
  const theme = useTheme();
  const s = STAGE_STYLE[stage] || STAGE_STYLE.regular_season;
  const isFeatured = stage === "finals" || stage === "playoffs";
  return (
    <span
      style={{
        fontSize: 11, fontWeight: s.weight, padding: "4px 11px", borderRadius: 20,
        fontFamily: "'Inter', sans-serif",
        color: isFeatured ? theme.accent : theme.textFaint,
        background: isFeatured ? theme.accentSoft : theme.steelSoft,
      }}
    >
      {s.label}
    </span>
  );
}

function TournamentHeader({ tournament, stage }) {
  const theme = useTheme();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "28px 0 12px", paddingBottom: 10, borderBottom: `1px solid ${theme.steel}` }}>
      <span style={{ fontSize: 18, fontWeight: 600, color: theme.text, fontFamily: "'Fraunces', serif" }}>
        {tournament}
      </span>
      <StageBadge stage={stage} />
    </div>
  );
}

// Horizontal scrolling day picker. Chosen over a range slider because
// the days are discrete labelled things rather than a continuum — you
// pick "Saturday", you don't scrub toward it — and a scroll row shows
// each day's label and match count directly instead of hiding them
// behind a handle position. Scrolls sideways rather than wrapping so
// the row stays one scannable line on any width.
function DayPicker({ dates, activeDate, onChange, counts }) {
  const theme = useTheme();
  if (dates.length <= 1) return null; // a picker over one day is just clutter

  const label = (iso) => {
    const d = new Date(iso + "T00:00:00");
    if (isNaN(d)) return { top: iso, bottom: "" };
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diff = Math.round((d - today) / 86400000);
    const md = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (diff === 0) return { top: "Today", bottom: md };
    if (diff === 1) return { top: "Tomorrow", bottom: md };
    return { top: d.toLocaleDateString(undefined, { weekday: "short" }), bottom: md };
  };

  const total = Object.values(counts).reduce((s, n) => s + n, 0);

  /* Two lines, not three. The old chip stacked day name, date and count
     separately, which made "All / days / 3" read as three unrelated
     fragments and gave every chip the height of a card. */
  const Chip = ({ selected, top, bottom, count, onClick }) => (
    <button
      onClick={onClick}
      className="kp-btn"
      aria-pressed={selected}
      style={{
        flex: "0 0 auto", minWidth: 74, padding: "8px 13px", cursor: "pointer",
        borderRadius: 12, fontFamily: "'Inter', sans-serif", textAlign: "center",
        border: `1px solid ${selected ? theme.accent : theme.steel}`,
        background: selected ? theme.accentSoft : theme.graphite,
        color: selected ? theme.accent : theme.textDim,
        scrollSnapAlign: "start",
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>{top}</div>
      <div style={{ fontSize: 10.5, whiteSpace: "nowrap", marginTop: 2, opacity: 0.8 }}>
        {bottom ? <>{bottom} <span aria-hidden="true">·</span> </> : null}
        <span className="kp-num">{count}</span>
      </div>
    </button>
  );

  return (
    <div
      className="kp-dayscroll"
      style={{
        display: "flex", gap: 8, overflowX: "auto", overflowY: "hidden",
        paddingBottom: 8, marginBottom: 16, scrollSnapType: "x proximity",
      }}
    >
      <Chip selected={!activeDate} top="All" bottom="" count={total} onClick={() => onChange(null)} />
      {dates.map((iso) => {
        const l = label(iso);
        return (
          <Chip
            key={iso}
            selected={activeDate === iso}
            top={l.top}
            bottom={l.bottom}
            count={counts[iso] || 0}
            onClick={() => onChange(activeDate === iso ? null : iso)}
          />
        );
      })}
    </div>
  );
}

/* ---------- Consistency tab ---------- */

// Ranks every tracked player by how reliably they produce, so the
// steadiest names are browsable in one place rather than having to be
// discovered by opening players one at a time.
//
// Ranking on consistency ALONE would be misleading: a player averaging
// 1.2 kills with almost no variance would top the list while being
// useless to act on. So volume is shown alongside, the list can be
// sorted by either, and a minimum-games floor keeps small samples from
// manufacturing fake steadiness.
function ConsistencyTab({ teams, pastMatches, statType, isDesktop }) {
  const theme = useTheme();
  const cfg = STAT_TYPES[statType];
  const [sortBy, setSortBy] = useState("consistency");
  // The analysis WINDOW — how many recent matches to score consistency
  // over — not merely a minimum. The previous control capped at 10,
  // which was a leftover from before tournament discovery quadrupled the
  // match pool; players now routinely have far more history than that,
  // and capping the window threw most of it away.
  const [windowSize, setWindowSize] = useState(15);

  // How much history actually exists, so the control can be bounded by
  // the real data rather than an arbitrary constant that goes stale the
  // moment the pool grows again.
  let deepest = 0;
  for (const [teamName, teamData] of Object.entries(teams)) {
    for (const player of teamData.players || []) {
      deepest = Math.max(deepest, recentForm(pastMatches, teamName, player.name, cfg.key, 200).length);
    }
  }
  const maxWindow = Math.max(10, Math.min(60, deepest));
  const effectiveWindow = Math.min(windowSize, maxWindow);

  const rows = [];
  for (const [teamName, teamData] of Object.entries(teams)) {
    for (const player of teamData.players || []) {
      const form = recentForm(pastMatches, teamName, player.name, cfg.key, effectiveWindow);
      if (form.length < 3) continue; // variance needs at least 3 points to mean anything
      const values = form.map((f) => f.value);
      const steadiness = consistencyScore(values);
      if (!steadiness) continue;
      rows.push({
        name: player.name, team: teamName, role: player.role,
        color: teamData.color, form, values,
        score: steadiness.score, mean: steadiness.mean, cv: steadiness.cv, n: steadiness.n,
      });
    }
  }

  rows.sort((a, b) => (sortBy === "consistency" ? b.score - a.score : b.mean - a.mean));
  const top = rows.slice(0, 60);

  return (
    <div>
      <div style={{ fontSize: 11, color: theme.textFaint, marginBottom: 12, lineHeight: 1.5 }}>
        How steady each player's {cfg.label.toLowerCase()} output has been across their recent matches.
        Consistency is scored from the coefficient of variation (spread relative to their own average),
        so it's comparable across high- and low-volume players. A high score means their output is
        predictable — not that it's large, which is why the average is shown next to it.
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}>
        {[["consistency", "Most consistent"], ["volume", "Highest average"]].map(([id, label]) => (
          <button
            key={id}
            onClick={() => setSortBy(id)}
            style={{
              fontSize: 12, fontWeight: 600, padding: "6px 13px", borderRadius: 20,
              fontFamily: "'Inter', sans-serif", cursor: "pointer", border: "none",
              color: sortBy === id ? theme.accent : theme.textDim,
              background: sortBy === id ? theme.accentSoft : theme.steelSoft,
            }}
          >
            {label}
          </button>
        ))}
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: theme.textFaint }}>
          last {effectiveWindow} matches
          <input
            type="range" min={3} max={maxWindow} step={1} value={effectiveWindow}
            onChange={(e) => setWindowSize(parseInt(e.target.value, 10))}
            style={{ width: 110, accentColor: theme.accent }}
          />
          <span style={{ fontFamily: "'IBM Plex Mono', monospace" }}>max {maxWindow}</span>
        </span>
      </div>

      {top.length === 0 ? (
        <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), padding: 16, textAlign: "center", fontSize: 12, color: theme.textDim }}>
          No players have 3+ recent matches on record yet — there isn't enough match history loaded to score consistency.
        </div>
      ) : (
        <div style={isDesktop ? { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 10, alignItems: "start" } : {}}>
          {top.map((row, i) => (
            <ConsistencyCard key={row.team + row.name} row={row} rank={i + 1} cfg={cfg} />
          ))}
        </div>
      )}
    </div>
  );
}

function ConsistencyCard({ row, rank, cfg }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const barColor = row.score >= 70 ? theme.good : row.score >= 45 ? theme.accent : theme.bad;
  return (
    <div
      className="kp-clickable"
      onClick={() => setOpen(!open)}
      style={{
        background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
        ...elevation(), marginBottom: 10, padding: "14px 16px", cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 11, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace", width: 20 }}>
          {rank}
        </span>
        <span style={{
          width: 34, height: 34, borderRadius: "50%", flexShrink: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: `${row.color}22`, border: `1.5px solid ${row.color}`,
          color: row.color, fontFamily: "'Inter', sans-serif", fontWeight: 700, fontSize: 12,
        }}>
          {initialsFor(row.name)}
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: theme.text, fontFamily: "'Fraunces', serif", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {row.name}
          </div>
          <div style={{ fontSize: 11, color: theme.textFaint }}>
            {row.team}{row.role ? ` · ${row.role}` : ""}
          </div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontFamily: "'Fraunces', serif", fontSize: 22, fontWeight: 600, color: barColor }}>
            {row.score.toFixed(0)}
          </div>
          <div style={{ fontSize: 10, color: theme.textFaint }}>consistency</div>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
        <div style={{ flex: 1, height: 5, background: theme.steelSoft, borderRadius: 3, overflow: "hidden" }}>
          <div style={{ width: `${row.score}%`, height: "100%", background: barColor, borderRadius: 3 }} />
        </div>
        <span style={{ fontSize: 11, fontFamily: "'IBM Plex Mono', monospace", color: theme.textDim }}>
          avg {row.mean.toFixed(1)} {cfg.key}
        </span>
        <span style={{ fontSize: 11, color: theme.textFaint }}>{row.n}g</span>
      </div>

      {open && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${theme.steelSoft}` }}>
          <RecentFormChart rows={row.form.slice(0, 8)} line={row.mean} statLabel={cfg.label.toLowerCase()} />
          <div style={{ marginTop: 8, fontSize: 11, color: theme.textFaint, fontFamily: "'IBM Plex Mono', monospace" }}>
            spread ±{(row.cv * row.mean).toFixed(1)} around a {row.mean.toFixed(1)} average
            {" "}(CV {row.cv.toFixed(2)}) — line shown is their own average, not a projection.
          </div>
        </div>
      )}
    </div>
  );
}

/* ============================================================
   ACCURACY SUMMARY — the product's actual claim, stated up front.

   Every projection in this app is checkable: the Past Results tab
   already re-projects each completed match using only the data that
   existed before it was played, then compares against what happened.
   That backtest was previously buried one sentence at a time inside
   individual result cards ("model missed by avg 3.3 kills/player"),
   so the one thing that tells a visitor whether any of this is worth
   trusting was never actually stated.

   Aggregated here over the most recent matches. Deliberately capped:
   this re-runs the point-in-time projection for every player in every
   match counted, which is the same work the Past Results tab does per
   card, and the headline does not get more honest by being slower.
   ============================================================ */
const ACCURACY_SAMPLE = 25;

/* The backtest headline: how far the projections landed from what players
   actually did, over the most recent completed matches, each projection
   built only from data that predated its own match.

   The caption's second half is load-bearing rather than decorative. "Within
   3 kills, 82%" reads like a claim about betting and is not one: a line is
   usually within 3 too, and it carries a margin. Distance from the result
   and performance against a price are different measurements, and only the
   first is on this screen. props_history.jsonl and scripts/score_props.py
   exist to make the second one sayable, and until they have a season behind
   them this component must not imply it. */
function AccuracySummary({ teams, pastMatches, weights, statType, isDesktop, borrowedHistory = false }) {
  const theme = useTheme();
  const cfg = STAT_TYPES[statType];

  const summary = useMemo(() => {
    const recent = [...pastMatches]
      .sort((a, b) => ((a.date || "") < (b.date || "") ? 1 : (a.date || "") > (b.date || "") ? -1 : 0))
      .slice(0, ACCURACY_SAMPLE);

    const errors = [];
    for (const match of recent) {
      // BOTH teams must be in the current roster, not just the one being
      // iterated. CS2's past_matches reference 125 teams while the roster
      // carries 60 — the rest are opponents from other regions and older
      // events — and the model reaches into the opponent's roster to build
      // its opponent-strength term. Passing a team it does not have throws.
      // The match cards already guard both sides before projecting; this
      // aggregate has to do the same.
      if (!teams[match.teamA] || !teams[match.teamB]) continue;
      for (const team of [match.teamA, match.teamB]) {
        const opp = team === match.teamA ? match.teamB : match.teamA;
        const mapsCounted = mapsCountedFor(match);
        for (const player of teams[team].players || []) {
          const actual = getActualStat(match, team, player.name, cfg.key);
          if (actual === undefined || actual === null) continue;
          const breakdown = projectPointInTime(
            pastMatches, teams, player, team, opp, mapsCounted, weights, match.date, statType, match.patch
          );
          errors.push(Math.abs(actual - breakdown.total));
        }
      }
    }
    if (errors.length === 0) return null;
    const mae = errors.reduce((s, e) => s + e, 0) / errors.length;
    const within3 = errors.filter((e) => e <= 3).length / errors.length;
    return { mae, within3, players: errors.length, matches: recent.length };
  }, [teams, pastMatches, weights, statType, cfg.key]);

  if (!summary) return null;

  const cells = [
    { value: summary.mae.toFixed(1), label: `avg miss / player`, tone: theme.accent },
    { value: `${Math.round(summary.within3 * 100)}%`, label: `within 3 ${cfg.label.toLowerCase()}`, tone: theme.good },
    { value: summary.matches, label: `matches tested`, tone: theme.text },
  ];

  return (
    <div className={bracketClass(theme)} style={{
      background: theme.graphite, border: `1px solid ${theme.steel}`,
      ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme),
      padding: isDesktop ? "16px 20px" : "14px 16px", marginBottom: 16,
      display: "flex", alignItems: "center", gap: isDesktop ? 28 : 16, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", gap: isDesktop ? 28 : 18 }}>
        {cells.map((c) => (
          <StatReadout key={c.label} value={c.value} label={c.label} size={isDesktop ? 26 : 22} color={c.tone} align="left" />
        ))}
      </div>
      <div style={{ flex: 1, minWidth: 190, fontSize: 11.5, color: theme.textFaint, lineHeight: 1.55 }}>
        Measured against the last {summary.matches} completed matches
        {borrowedHistory ? " these teams played, including in their home regions" : " in this region"} — {summary.players.toLocaleString()} player projections, each made using only the data
        that existed before that match was played.
        {" "}
        <span style={{ opacity: 0.85 }}>
          This is distance from the <em>result</em>, not performance against a <em>line</em>. A
          projection can sit closer to the truth than the posted line and still lose money, because
          the line is close too and is priced with a margin. Nothing here is a measured record
          against the market.
        </span>
      </div>
    </div>
  );
}

function FutureTab({ teams, pastMatches, upcomingMatches, weights, statType, isDesktop, games, game }) {
  const theme = useTheme();
  const [customMode, setCustomMode] = useState(false);
  const teamNames = Object.keys(teams);
  const [customA, setCustomA] = useState(teamNames[0]);
  const [customB, setCustomB] = useState(teamNames[1]);
  const canCustomMatchup = teamNames.length >= 2;

  // Sort by actual scheduled time (soonest first) before grouping — don't
  // rely on whatever order the source data happened to arrive in.
  const sorted = [...upcomingMatches].sort((a, b) => {
    const ta = a._sortKey || a.date || "";
    const tb = b._sortKey || b.date || "";
    return ta < tb ? -1 : ta > tb ? 1 : 0;
  });

  // Day filtering. dayIndex 0 is the "all days" position; 1..n map onto
  // the distinct scheduled dates in order.
  const dates = [...new Set(sorted.map((m) => m.date).filter(Boolean))].sort();
  const dateCounts = sorted.reduce((acc, m) => {
    if (m.date) acc[m.date] = (acc[m.date] || 0) + 1;
    return acc;
  }, {});
  // Keyed on the DATE itself rather than a position index. An index is
  // only meaningful relative to a particular list, and the schedule
  // reshapes on every refresh — a stored index silently points at a
  // different day (or off the end) once the list changes. A date either
  // still exists or it doesn't, and the guard below handles that
  // explicitly instead of quietly showing the wrong day.
  const [selectedDate, setSelectedDate] = useState(null);
  const activeDate = selectedDate && dates.includes(selectedDate) ? selectedDate : null;
  const visible = activeDate ? sorted.filter((m) => m.date === activeDate) : sorted;

  const grouped = sortGroupsByProgression(groupByLabel(visible, (m) => m.block || m.week || null));
  // On wide screens, let cards flow into as many columns as fit rather than
  // a single stacked column — auto-fill means this scales smoothly with
  // whatever width is actually available instead of a hard 2-vs-1 rule.
  // alignItems: "start" keeps each card its own natural height rather than
  // stretching to match a taller expanded neighbor in the same row.
  const gridStyle = isDesktop
    ? { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))", gap: 14, alignItems: "start" }
    : {};

  return (
    <div>
      <div style={{ fontSize: 11, color: theme.textFaint, marginBottom: 12, lineHeight: 1.5 }}>
        Projected {STAT_TYPES[statType].label.toLowerCase()} ({games} game{games > 1 ? "s" : ""} combined) for each
        upcoming matchup, using current model weights.
      </div>
      <DayPicker dates={dates} activeDate={activeDate} onChange={setSelectedDate} counts={dateCounts} />
      {activeDate && visible.length === 0 && (
        <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), padding: "16px", textAlign: "center", fontSize: 12, color: theme.textDim, marginBottom: 10 }}>
          No matches on this day.
        </div>
      )}
      {/* An empty schedule is a normal state between splits, not an error.
          It previously pointed the reader at a control further down the
          page ("use the tool below") instead of just offering it. */}
      {upcomingMatches.length === 0 && (
        <div style={{
          background: theme.graphite, border: `1px solid ${theme.steel}`,
          ...cardShape(theme.cornerStyle), ...elevation(),
          padding: "34px 20px", textAlign: "center", marginBottom: 10,
        }}>
          <div style={{ opacity: 0.45, display: "flex", justifyContent: "center", marginBottom: 12 }}>
            <Reticle size={26} color={theme.textDim} />
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: theme.text, fontFamily: "'Fraunces', serif" }}>
            No upcoming matches scheduled
          </div>
          <div style={{ fontSize: 12, color: theme.textFaint, marginTop: 6, lineHeight: 1.55, maxWidth: 380, marginLeft: "auto", marginRight: "auto" }}>
            Nothing is on the schedule for this region yet. You can still project any
            two teams against each other from this split's data.
          </div>
          {canCustomMatchup && !customMode && (
            <button
              className="kp-btn"
              onClick={() => setCustomMode(true)}
              style={{
                marginTop: 16, padding: "9px 18px", background: theme.accentSoft,
                border: `1px solid ${theme.accentBorder}`, borderRadius: 10,
                color: theme.accent, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
                fontFamily: "'Inter', sans-serif",
              }}
            >
              Build a custom matchup
            </button>
          )}
        </div>
      )}
      {grouped.map(([label, matches]) => (
        <div key={label}>
          <WeekHeader label={label} />
          <div style={gridStyle}>
            {matches.map((m, i) => <FutureMatchCard key={i} teams={teams} pastMatches={pastMatches} match={m} weights={weights} statType={statType} games={games} game={game} />)}
          </div>
        </div>
      ))}

      {/* A secondary action, sized like one. Full-bleed it read as a banner
          with more weight than the projections above it. Suppressed when the
          schedule is empty, since the empty state above already offers it —
          otherwise the same action appears twice on the same screen. */}
      {upcomingMatches.length > 0 && canCustomMatchup ? (
        <button
          className="kp-btn"
          onClick={() => setCustomMode(!customMode)}
          aria-expanded={customMode}
          style={{
            marginTop: 14, padding: "9px 16px", background: "transparent",
            border: `1px solid ${theme.steel}`, borderRadius: 10,
            color: theme.textDim, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
            fontFamily: "'Inter', sans-serif", display: "inline-flex", alignItems: "center", gap: 7,
          }}
        >
          {customMode ? "Hide custom matchup" : "Build a custom matchup"}
          <Chevron open={customMode} color={theme.textFaint} size={12} />
        </button>
      ) : !canCustomMatchup ? (
        <div style={{ marginTop: 8, padding: "10px 14px", fontSize: 11, color: theme.textFaint, textAlign: "center" }}>
          Custom matchup needs at least 2 teams with data loaded for this region — only {teamNames.length} currently available.
        </div>
      ) : null}

      {customMode && canCustomMatchup && (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
            {[[customA, setCustomA], [customB, setCustomB]].map(([val, setter], i) => (
              <select key={i} value={val} onChange={(e) => setter(e.target.value)} style={{ flex: 1, background: theme.graphiteLight, color: theme.text, border: `1px solid ${teams[val] ? teams[val].color : theme.steel}55`, ...cardShape(theme.cornerStyle), padding: "10px 8px", fontSize: 14, fontWeight: 600 }}>
                {teamNames.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            ))}
          </div>
          <MatchupPanel teams={teams} pastMatches={pastMatches} teamA={customA} teamB={customB} games={games} weights={weights} statType={statType} />
        </div>
      )}
    </div>
  );
}

/* ---------- Past Results tab ---------- */

// The "games in series" slider intentionally does NOT apply here — every
// past match's "actual" data represents exactly 2 real games (maps 1+2,
// the app's established convention), so projecting for a different game
// count would compare against actual data that doesn't match, silently
// biasing the backtest. Fixed at 2 on purpose, not an oversight.
function PastMatchCard({ teams, pastMatches, match, weights, statType }) {
  const theme = useTheme();
  const homeRegion = useHomeRegion();
  const [open, setOpen] = useState(false);
  const cfg = STAT_TYPES[statType];
  const teamKeys = [match.teamA, match.teamB];

  if (!teams[match.teamA] || !teams[match.teamB]) {
    return (
      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), marginBottom: 10, padding: "12px 14px", fontSize: 12, color: theme.textFaint }}>
        {match.teamA} vs {match.teamB} — roster data not loaded for one of these teams yet.
      </div>
    );
  }

  const rows = teamKeys.flatMap((team) => {
    const opp = team === match.teamA ? match.teamB : match.teamA;
    return teams[team].players
      .map((p) => ({ p, actual: getActualStat(match, team, p.name, cfg.key) }))
      .filter(({ actual }) => actual !== undefined && actual !== null) // undefined = didn't play; null = legacy data without this stat
      .map(({ p, actual }) => {
        // maps_counted, not 2 — "actual" sums the series' real prop
        // window (maps 1-2 for a Bo3, 1-3 for a Bo5), so a fixed 2 here
        // would under-predict every Bo5 and make the PROJ/ACT/DIFF
        // columns disagree with the model's own backtest.
        const mapsCounted = mapsCountedFor(match);
        const breakdown = projectPointInTime(pastMatches, teams, p, team, opp, mapsCounted, weights, match.date, statType, match.patch);
        return { team, name: p.name, role: p.role, proj: breakdown.total, priorGames: breakdown.priorGames, actual, diff: actual - breakdown.total, player: p, breakdown, mapsCounted };
      });
  });

  if (rows.length === 0) {
    // Legacy fallback data only ever tracked kills — nothing to show for
    // deaths/assists on those matches.
    return (
      <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), marginBottom: 10, padding: "12px 14px", fontSize: 12, color: theme.textFaint }}>
        {match.teamA} vs {match.teamB} — {cfg.label.toLowerCase()} data not available for this match.
      </div>
    );
  }

  const totalProj = rows.reduce((s, r) => s + r.proj, 0);
  const totalActual = rows.reduce((s, r) => s + r.actual, 0);
  const avgAbsDiff = rows.reduce((s, r) => s + Math.abs(r.diff), 0) / rows.length;
  // How many rows had zero prior current-split games to draw on (very early
  // in a split) — worth flagging since those projections lean entirely on
  // prior-split history rather than this season's form.
  const coldStartCount = rows.filter((r) => r.priorGames === 0).length;

  return (
    <div className={bracketClass(theme)} style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), ...bracketStyle(theme), marginBottom: 10, overflow: "hidden" }}>
      <div
        className="kp-clickable"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`${match.teamA} versus ${match.teamB}, ${match.date}. ${match.winner} won ${match.score}. Projected ${totalProj.toFixed(0)}, actual ${totalActual}. Activate for player-level detail.`}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); }
        }}
        style={{ cursor: "pointer", padding: "15px 18px", display: "flex", alignItems: "center", gap: 14 }}
      >
        {/* Result facts on the left, model performance on the right. These
            used to run together in one sentence that wrapped mid-number
            ("missed by avg 3.4 / kills/player"), so neither the result nor
            the model's error could be read at a glance. */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
            <TeamTag name={match.teamA} color={teams[match.teamA].color} dim={match.winner !== match.teamA}
                     region={homeRegion(match.teamA)} />
            <span style={{ color: theme.textFaint, fontSize: 10.5, fontWeight: 500 }}>vs</span>
            <TeamTag name={match.teamB} color={teams[match.teamB].color} dim={match.winner !== match.teamB}
                     region={homeRegion(match.teamB)} />
          </div>
          <div style={{ marginTop: 7, fontSize: 11.5, color: theme.textFaint, display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
            <span><span style={{ color: theme.textDim, fontWeight: 600 }}>{match.winner}</span> won {match.score}</span>
            {match.series_format && (
              <>
                <span aria-hidden="true" style={{ opacity: 0.5 }}>•</span>
                <span className="kp-num">{match.series_format}, maps 1-{mapsCountedFor(match)}</span>
              </>
            )}
            <span aria-hidden="true" style={{ opacity: 0.5 }}>•</span>
            <span className="kp-num">{match.date}</span>
            {coldStartCount > 0 && (
              <span className="kp-chip" style={{ background: `${theme.bad}1A`, color: theme.bad, border: `1px solid ${theme.bad}33` }}>
                {coldStartCount} no prior form
              </span>
            )}
          </div>
        </div>
        <StatReadout value={`${totalProj.toFixed(0)}/${totalActual}`} label="proj / actual" size={19} />
        <div style={{ textAlign: "right", flexShrink: 0, minWidth: 62 }}>
          <DeltaBadge value={avgAbsDiff} />
          <div style={{ fontSize: 9.5, letterSpacing: 0.9, textTransform: "uppercase", color: theme.textFaint, marginTop: 6, fontWeight: 600 }}>avg miss</div>
        </div>
        <Chevron open={open} color={theme.textFaint} />
      </div>
      {open && (
        <div style={{ borderTop: `1px solid ${theme.steel}` }}>
          {rows.map((row) => (
            <MatchPlayerRow
              key={row.team + row.name}
              theme={theme}
              teamColor={teams[row.team].color}
              name={row.name}
              role={row.role}
              extraChip={row.priorGames === 0 && (
                <span className="kp-chip" style={{ background: `${theme.bad}20`, color: theme.bad }}>No prior form</span>
              )}
              stats={[
                { label: "PROJ", value: row.proj.toFixed(1), color: theme.textDim },
                { label: "ACT", value: row.actual, color: theme.text },
                { label: "DIFF", value: `${row.diff > 0 ? "+" : ""}${row.diff.toFixed(1)}`, color: row.diff > 0 ? theme.good : row.diff < 0 ? theme.bad : theme.textFaint },
              ]}
              r={row.breakdown}
              p={row.player}
              cfg={cfg}
              games={row.mapsCounted}
              pastMatches={pastMatches}
              team={row.team}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PastResultsTab({ teams, pastMatches, weights, statType, isDesktop }) {
  const theme = useTheme();
  const [stageFilter, setStageFilter] = useState("all");

  // Sort by actual date (newest first) rather than trusting array order —
  // the live scraper and the offline fallback snapshot store matches in
  // opposite orders, so relying on array position was fragile.
  const sorted = [...pastMatches].sort((a, b) => {
    const da = a.date || "";
    const db = b.date || "";
    return da < db ? 1 : da > db ? -1 : 0;
  });

  const availableStages = [...new Set(sorted.map((m) => m.stage).filter(Boolean))];
  const filtered = stageFilter === "all" ? sorted : sorted.filter((m) => m.stage === stageFilter);

  // Grouped by tournament first (older data without a "tournament" field
  // falls back to a single "Other Matches" bucket rather than vanishing),
  // then by week/round within each tournament using the existing
  // playoff-aware ordering. Since `sorted` is already newest-first,
  // grouping preserves that as most-recent-tournament-first too — each
  // tournament's group naturally starts at its own latest match.
  const byTournament = groupByLabel(filtered, (m) => m.tournament || null);
  const gridStyle = isDesktop
    ? { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))", gap: 14, alignItems: "start" }
    : {};

  return (
    <div>
      <div style={{ fontSize: 11, color: theme.textFaint, marginBottom: 12, lineHeight: 1.5 }}>
        Point-in-time projections: each match uses only the {STAT_TYPES[statType].label.toLowerCase()} data
        that existed before it was played (plus the current slider weights), so this
        is a real backtest of the model, not hindsight. Rows marked "no prior form" had
        zero current-split games to draw on yet and lean on prior-split history alone.
      </div>
      {availableStages.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 18 }}>
          {["all", ...availableStages].map((s) => {
            const active = stageFilter === s;
            const styleInfo = s === "all" ? { label: "All" } : STAGE_STYLE[s] || { label: s[0].toUpperCase() + s.slice(1) };
            return (
              <button
                key={s}
                onClick={() => setStageFilter(s)}
                style={{
                  fontSize: 12, fontWeight: 600, padding: "6px 13px", borderRadius: 20,
                  fontFamily: "'Inter', sans-serif", cursor: "pointer", border: "none",
                  color: active ? theme.accent : theme.textDim,
                  background: active ? theme.accentSoft : theme.steelSoft,
                }}
              >
                {styleInfo.label}
              </button>
            );
          })}
        </div>
      )}
      {byTournament.map(([tournament, tMatches]) => {
        const stage = tMatches[0]?.stage || "regular_season";
        const weekGroups = sortGroupsByProgression(groupByLabel(tMatches, (m) => m.week || null));
        return (
          <div key={tournament}>
            {tournament !== "Other" && <TournamentHeader tournament={tournament} stage={stage} />}
            {weekGroups.map(([label, matches]) => (
              <div key={label}>
                <WeekHeader label={label} />
                <div style={gridStyle}>
                  {matches.map((m, i) => <PastMatchCard key={i} teams={teams} pastMatches={pastMatches} match={m} weights={weights} statType={statType} />)}
                </div>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

/* ---------- Data status banner ---------- */

/* "updated 9/18/2026, 1:47:37 PM" makes the reader do arithmetic to answer
   the only question they actually have, which is whether this is current.
   The scrapers run twice a day, so elapsed time is the useful form — the
   exact timestamp stays available on hover. Seconds were never meaningful
   here and are dropped. */
function relativeTime(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (isNaN(then)) return null;
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

/* How old the DATA is, and which region is the oldest.

   Not when the scraper ran. Those are different questions and the badge
   was answering the wrong one: the LoL scraper falls back to committed
   data for any region it cannot reach, then stamps generated_at with the
   current time, so a region carrying five-day-old matches sat under
   "Live data · updated 9h ago". A total outage is already handled --
   that scraper refuses to rewrite the file at all -- but a partial one
   was silently mislabelled, and a partial one is the common case.

   Each region now records its own refreshed_at. The badge reports the
   OLDEST of them, because a page is only as current as the stalest
   thing on it, and names that region so the reader knows which numbers
   to distrust rather than distrusting all of them.

   Files written before the field existed have no refreshed_at anywhere,
   and fall back to generated_at -- the old behaviour, for old data. */
function oldestRegion(regions, fallback) {
  let worstKey = null, worstAt = null;
  for (const [key, region] of Object.entries(regions || {})) {
    const at = region && region.refreshed_at;
    if (!at) continue;
    const t = new Date(at).getTime();
    if (isNaN(t)) continue;
    if (worstAt === null || t < worstAt) { worstAt = t; worstKey = key; }
  }
  if (worstAt === null) return { at: fallback || null, region: null, perRegion: false };
  return { at: new Date(worstAt).toISOString(), region: worstKey, perRegion: true };
}

// A region more than this far behind the freshest one is not just old,
// it is out of step with the rest of the page -- which is the thing
// worth naming, and it is what a half-scraped run looks like.
const REGION_LAG_HOURS = 12;

function DataStatus({ status, lastUpdated, errorDetail, onRetry, regions }) {
  const theme = useTheme();
  const color = status === "live" ? theme.good : status === "loading" ? theme.textDim : theme.accent;
  const oldest = oldestRegion(regions, lastUpdated);
  const effective = oldest.at || lastUpdated;
  const relative = relativeTime(effective);
  // Data older than a day usually means the scrape has been failing, which
  // is worth a visible change of tone rather than a quietly stale number.
  const stale = effective && (Date.now() - new Date(effective).getTime()) > 36 * 3600 * 1000;
  // Named only when it is actually behind the rest. On a healthy run
  // every region shares one timestamp and saying "oldest: LCS" would be
  // noise dressed as information.
  const freshest = Object.values(regions || {})
    .map((r) => r && r.refreshed_at ? new Date(r.refreshed_at).getTime() : NaN)
    .filter((t) => !isNaN(t));
  const lagging = oldest.perRegion && freshest.length > 1
    && Math.max(...freshest) - new Date(oldest.at).getTime() > REGION_LAG_HOURS * 3600 * 1000
    ? oldest.region : null;
  const label =
    status === "live" ? `Live data · updated ${relative || "recently"}` :
    status === "loading" ? "Loading live data…" :
    status === "no-url" ? "Using bundled snapshot (no live source configured)" :
    "Live fetch failed — using bundled snapshot";
  return (
    <div style={{ marginBottom: 16, padding: "8px 12px", background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation() }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div
          style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: stale ? theme.accent : theme.textDim }}
          title={lastUpdated ? new Date(lastUpdated).toLocaleString() : undefined}
        >
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: stale ? theme.accent : color, flexShrink: 0 }} />
          {label}
          {lagging && (
            <span style={{ color: theme.accent }}>
              · {lagging} is {relativeTime(oldest.at)}
            </span>
          )}
        </div>
        {status !== "loading" && (
          <button onClick={onRetry} style={{ background: "none", border: "none", color: theme.textFaint, fontSize: 11, cursor: "pointer", textDecoration: "underline" }}>
            refresh
          </button>
        )}
      </div>
      {status === "error" && errorDetail && (
        <div style={{ marginTop: 6, fontSize: 10, color: theme.bad, fontFamily: "'IBM Plex Mono', monospace", wordBreak: "break-word" }}>
          {errorDetail}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   ROOT
   ============================================================ */

function formatUpcoming(rawList) {
  // Normalizes either the live API's ISO timestamps or fallback {date,time}.
  // Keeps the original value as _sortKey so grouping/sorting isn't affected
  // by the display-formatted string.
  return rawList.map((m) => {
    if (m.date && m.date.includes("T")) {
      const d = new Date(m.date);
      return {
        ...m,
        _sortKey: m.date,
        date: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        time: d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }),
      };
    }
    return { ...m, _sortKey: m.date };
  });
}

/* ---------- Game switcher — a deliberate menu action instead of a plain
   button row, so choosing a game feels like a real mode switch rather than
   just another selector competing for space with region/view/stat. ---------- */

function GameSwitcher({ game, selectGame, statusByGame, theme }) {
  const [open, setOpen] = useState(false);
  const current = GAMES[game];
  const currentAccent = GAME_ACCENTS[game].accent;

  return (
    <div style={{ position: "relative", marginBottom: 16 }}>
      <button
        className="kp-btn"
        onClick={() => setOpen(!open)}
        style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", background: theme.graphite, border: `1px solid ${currentAccent}55`,
          ...cardShape(theme.cornerStyle), ...elevation(), cursor: "pointer",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: currentAccent, boxShadow: `0 0 8px ${currentAccent}` }} />
          <span style={{ fontFamily: "'Fraunces', serif", fontWeight: 700, fontSize: 15, color: theme.text }}>{current.label}</span>
        </span>
        <span style={{ fontSize: 11, color: theme.textFaint, transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s ease" }}>▾</span>
      </button>

      {open && (
        <>
          <div
            className="kp-backdrop-anim"
            onClick={() => setOpen(false)}
            style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 40 }}
          />
          <div
            className="kp-menu-anim"
            style={{
              position: "absolute", top: "calc(100% + 8px)", left: 0, right: 0, zIndex: 41,
              background: theme.graphiteLight, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle),
              boxShadow: "0 12px 32px rgba(0,0,0,0.5)", overflow: "hidden",
            }}
          >
            {GAME_LIST.map((id) => {
              const accent = GAME_ACCENTS[id].accent;
              const isCurrent = id === game;
              const status = statusByGame[id];
              return (
                <button
                  key={id}
                  className="kp-clickable"
                  onClick={() => { selectGame(id); setOpen(false); }}
                  style={{
                    width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "14px 16px", background: isCurrent ? `${accent}14` : "transparent",
                    border: "none", borderLeft: `3px solid ${isCurrent ? accent : "transparent"}`,
                    textAlign: "left", cursor: "pointer",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ width: 10, height: 10, borderRadius: "50%", background: accent }} />
                    <span>
                      <div style={{ fontFamily: "'Fraunces', serif", fontWeight: 700, fontSize: 14, color: isCurrent ? accent : theme.text }}>
                        {GAMES[id].label}
                      </div>
                      <div style={{ fontSize: 10, color: theme.textFaint, marginTop: 1 }}>{GAMES[id].regionList.length} regions tracked</div>
                    </span>
                  </span>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: status === "live" ? theme.good : status === "loading" ? theme.textFaint : theme.bad }} />
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

/* ---------- Top navigation: Region / View / Stat, redesigned with real
   visual presence — big pill buttons for region, underlined tabs for view,
   a segmented control for stat — instead of three cramped, near-identical
   button rows squeezed into a narrow column. Placed at the top of the main
   content area on desktop (full width to work with) and in the mobile
   stack on small screens (still bigger/clearer than the old treatment). ---------- */

function TopNav({ theme, gameCfg, game, region, setRegion, tab, setTab, statType, setStatType, isDesktop }) {
  // The live board, so the stat selector can follow what the provider
  // is actually posting instead of offering four tabs equally when two
  // of them lead nowhere.
  const propsData = useProps();
  // Parlays sits next to Edges because it is the same board read a different
  // way: Edges ranks single lines, Parlays stacks them. It is cross-game, so
  // the region and stat selectors above do not apply to it.
  const TABS = [["future", "Future"], ["edges", "Edges"], ["parlays", "Parlays"], ["record", "Record"], ["past", "Past Results"], ["consistency", "Consistency"], ["standings", "Standings"]];
  return (
    <div style={{ marginBottom: isDesktop ? 22 : 14 }}>
      {/* Region picker. Seven regions wrapped onto two rows on a phone,
          which cost ~90px of the first screen before any content. One
          scrolling row instead — the same pattern the day picker uses. */}
      <div
        className={isDesktop ? "" : "kp-dayscroll"}
        role="group"
        aria-label="Region"
        style={{
          display: "flex", gap: 8, marginBottom: isDesktop ? 16 : 12,
          flexWrap: isDesktop ? "wrap" : "nowrap",
          overflowX: isDesktop ? "visible" : "auto",
          paddingBottom: isDesktop ? 0 : 6,
          scrollSnapType: isDesktop ? "none" : "x proximity",
        }}
      >
        {gameCfg.regionList.map((key) => (
          <button
            key={key}
            className="kp-btn"
            onClick={() => setRegion(key)}
            aria-pressed={region === key}
            style={{
              padding: isDesktop ? "11px 22px" : "9px 15px", borderRadius: 24,
              border: "1px solid " + (region === key ? theme.accent : theme.steel),
              background: region === key ? theme.accentSoft : theme.graphite,
              color: region === key ? theme.accent : theme.textDim,
              fontSize: isDesktop ? 15 : 13, fontWeight: 600, cursor: "pointer",
              fontFamily: "'Fraunces', serif", flexShrink: 0, scrollSnapAlign: "start",
              ...elevation(),
            }}
          >
            {gameCfg.regionLabels[key]}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12, borderBottom: `1px solid ${theme.steel}` }}>
        {/* Real tab semantics: these look and behave like tabs, so screen
            readers should be told that rather than hearing four buttons. */}
        <div
          role="tablist"
          aria-label="View"
          className={isDesktop ? "" : "kp-dayscroll"}
          style={{
            display: "flex", gap: isDesktop ? 24 : 15,
            overflowX: isDesktop ? "visible" : "auto", maxWidth: "100%",
          }}
        >
          {TABS.map(([id, label]) => (
            <button
              key={id}
              className="kp-btn"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              style={{
                padding: "10px 2px 12px", background: "none", border: "none",
                borderBottom: `2px solid ${tab === id ? theme.accent : "transparent"}`,
                color: tab === id ? theme.accent : theme.textDim,
                fontSize: isDesktop ? 15 : 13.5, fontWeight: 600, cursor: "pointer",
                fontFamily: "'Fraunces', serif", whiteSpace: "nowrap", flexShrink: 0,
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div role="group" aria-label="Stat" style={{ display: "flex", gap: 4, marginBottom: 8 }}>
          {offeredStats(game, propsData, statType).map(([key, cfg, posted]) => (
            <button
              key={key}
              className="kp-btn"
              onClick={() => setStatType(key)}
              aria-pressed={statType === key}
              title={posted === null ? undefined
                : posted === 0 ? `No lines posted on ${cfg.label.toLowerCase()} right now`
                : `${posted} line${posted === 1 ? "" : "s"} posted on ${cfg.label.toLowerCase()}`}
              style={{
                padding: "7px 14px", borderRadius: 20, border: "1px solid " + (statType === key ? theme.accent : theme.steel),
                background: statType === key ? theme.accentSoft : "transparent",
                color: statType === key ? theme.accent : theme.textDim,
                fontSize: 12, fontWeight: 600, cursor: "pointer", fontFamily: "'Inter', sans-serif",
              }}
            >
              {cfg.label}
              {/* The count, so a reader can see where the market is
                  rather than having to press each tab to find out. */}
              {posted ? (
                <span className="kp-num" style={{ fontSize: 10, marginLeft: 5, opacity: 0.75 }}>
                  {posted}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function SettingsPanel({ theme, accentOverride, setAccentOverride, cornerStyle, setCornerStyle, open, setOpen }) {
  const [customHex, setCustomHex] = useState(accentOverride || "");
  return (
    <div style={{ marginBottom: 16 }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: "flex", alignItems: "center", gap: 6, background: "none", border: "none",
          color: theme.textDim, fontSize: 12, cursor: "pointer", padding: 0, fontFamily: "'Inter', sans-serif", fontWeight: 500,
        }}
      >
        <span style={{ fontSize: 13 }}>⚙</span> Customize {open ? "▴" : "▾"}
      </button>
      {open && (
        <div style={{ marginTop: 12, background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: 16 }}>
          <div style={{ fontSize: 12, color: theme.textDim, marginBottom: 10, fontFamily: "'Inter', sans-serif", fontWeight: 600 }}>Accent color</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            <button
              onClick={() => { setAccentOverride(null); setCustomHex(""); }}
              title="Automatic (follows selected game)"
              style={{
                width: 28, height: 28, borderRadius: "50%", cursor: "pointer",
                border: !accentOverride ? `2px solid ${theme.text}` : `1px solid ${theme.steel}`,
                background: "conic-gradient(#C9A86A, #FF4655, #4FD8E8, #7FE07A, #B07FE0, #E08A4F, #C9A86A)",
              }}
            />
            {ACCENT_SWATCHES.map((s) => (
              <button
                key={s.hex}
                onClick={() => { setAccentOverride(s.hex); setCustomHex(s.hex); }}
                title={s.name}
                style={{
                  width: 28, height: 28, borderRadius: "50%", cursor: "pointer", background: s.hex,
                  border: accentOverride === s.hex ? `2px solid ${theme.text}` : "1px solid rgba(255,255,255,0.15)",
                }}
              />
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
            <input
              type="text"
              value={customHex}
              onChange={(e) => setCustomHex(e.target.value)}
              placeholder="#RRGGBB"
              style={{ flex: 1, background: theme.graphiteLight, border: `1px solid ${theme.steel}`, borderRadius: 8, color: theme.text, padding: "7px 10px", fontSize: 12, fontFamily: "'IBM Plex Mono', monospace" }}
            />
            <button
              onClick={() => { if (/^#[0-9a-fA-F]{6}$/.test(customHex)) setAccentOverride(customHex); }}
              style={{ background: theme.steel, border: "none", borderRadius: 8, color: theme.text, padding: "7px 14px", fontSize: 12, fontWeight: 600, cursor: "pointer", fontFamily: "'Inter', sans-serif" }}
            >
              Use
            </button>
          </div>
          <div style={{ fontSize: 12, color: theme.textDim, marginBottom: 10, fontFamily: "'Inter', sans-serif", fontWeight: 600 }}>Card corners</div>
          <div style={{ display: "flex", gap: 8 }}>
            {[["angular", "Angular"], ["rounded", "Rounded"]].map(([id, label]) => (
              <button
                key={id}
                onClick={() => setCornerStyle(id)}
                style={{
                  flex: 1, padding: "9px 0", border: "1px solid " + (cornerStyle === id ? theme.accent : theme.steel),
                  background: cornerStyle === id ? theme.accentSoft : theme.graphiteLight,
                  color: cornerStyle === id ? theme.accent : theme.textDim,
                  fontSize: 12, fontWeight: 600, cursor: "pointer", fontFamily: "'Inter', sans-serif",
                  ...(id === "angular" ? { clipPath: `polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 8px 100%, 0 calc(100% - 8px))` } : { borderRadius: 8 }),
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function KillProjector() {
  const isDesktop = useIsDesktop();
  const [game, setGame] = useState("lol");
  const [region, setRegion] = useState("LCS");
  const [tab, setTab] = useState("future");
  const [statType, setStatType] = useState("kills");
  const [games, setGames] = useState(2);

  // Per-GAME, per-stat weights persist across visits, across switching
  // between Kills/Deaths/Assists, AND across switching games — custom
  // CS2 tuning doesn't get clobbered by looking at LoL, and each game
  // starts from its own measured defaults rather than one shared set
  // (which was a real source of prediction inconsistency, since the
  // measured optima differ substantially per game).
  const [weightsByGameAndStat, setWeightsByGameAndStat] = useState(() => {
    const stored = loadStored("kp.weightsByGameAndStat", DEFAULT_WEIGHTS_BY_GAME_AND_STAT);
    // Migration: a blob saved before a new weight key existed (e.g.
    // "career", added after this was first shipped) would otherwise
    // leave that key undefined for anyone with existing saved state —
    // safe for the projection math itself (undefined > 0 is false) but
    // not for a <Slider value={undefined}>. Backfill any missing key
    // from the current defaults, per game and stat, without touching
    // anything the person already customized.
    const migrated = {};
    for (const g of Object.keys(DEFAULT_WEIGHTS_BY_GAME_AND_STAT)) {
      migrated[g] = {};
      for (const s of Object.keys(DEFAULT_WEIGHTS_BY_GAME_AND_STAT[g])) {
        migrated[g][s] = { ...DEFAULT_WEIGHTS_BY_GAME_AND_STAT[g][s], ...(stored[g] && stored[g][s] ? stored[g][s] : {}) };
      }
    }
    return migrated;
  });
  useEffect(() => saveStored("kp.weightsByGameAndStat", weightsByGameAndStat), [weightsByGameAndStat]);
  // Fall back through game defaults then LoL, so a stored blob written by
  // an older build (or a newly-added game) can't leave weights undefined.
  const weights =
    (weightsByGameAndStat[game] && weightsByGameAndStat[game][statType]) ||
    (DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game] && DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game][statType]) ||
    DEFAULT_WEIGHTS_BY_GAME_AND_STAT.lol[statType];
  const setWeight = (key, value) =>
    setWeightsByGameAndStat((prev) => {
      const gameWeights = prev[game] || DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game] || DEFAULT_WEIGHTS_BY_GAME_AND_STAT.lol;
      return {
        ...prev,
        [game]: { ...gameWeights, [statType]: { ...gameWeights[statType], [key]: value } },
      };
    });

  const [slidersExpanded, setSlidersExpanded] = useState(() => loadStored("kp.slidersExpanded", false));
  useEffect(() => saveStored("kp.slidersExpanded", slidersExpanded), [slidersExpanded]);

  const [accentOverride, setAccentOverride] = useState(() => loadStored("kp.accentOverride", null));
  useEffect(() => saveStored("kp.accentOverride", accentOverride), [accentOverride]);
  const [cornerStyle, setCornerStyle] = useState(() => loadStored("kp.cornerStyle", "rounded"));
  useEffect(() => saveStored("kp.cornerStyle", cornerStyle), [cornerStyle]);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const gameAccent = GAME_ACCENTS[game];
  const accent = accentOverride || gameAccent.accent;
  const theme = {
    ...BASE_TOKENS,
    accent,
    accentSoft: accentOverride ? `${accentOverride}18` : gameAccent.accentSoft,
    accentBorder: accentOverride ? `${accentOverride}55` : gameAccent.accentBorder,
    cornerStyle,
  };

  // Data, load status, and error detail are all keyed by game — switching
  // games doesn't lose or re-fetch the other game's already-loaded data.
  const [dataByGame, setDataByGame] = useState({
    lol: GAMES.lol.fallbackRegions,
    valorant: GAMES.valorant.fallbackRegions,
  });
  const [statusByGame, setStatusByGame] = useState({
    lol: GAMES.lol.dataUrl ? "loading" : "no-url",
    valorant: GAMES.valorant.dataUrl ? "loading" : "no-url",
  });
  const [lastUpdatedByGame, setLastUpdatedByGame] = useState({});
  const [errorByGame, setErrorByGame] = useState({});

  const fetchGameData = (gameId) => {
    const cfg = GAMES[gameId];
    if (!cfg.dataUrl) {
      setStatusByGame((prev) => ({ ...prev, [gameId]: "no-url" }));
      return;
    }
    setStatusByGame((prev) => ({ ...prev, [gameId]: "loading" }));
    setErrorByGame((prev) => ({ ...prev, [gameId]: null }));

    // One retry, after a short delay, on ANY failure (network error, bad
    // HTTP status, or invalid JSON) — a fresh scraper commit can very
    // briefly serve a truncated/mid-write response from
    // raw.githubusercontent.com's CDN before the push fully propagates,
    // which throws immediately on JSON.parse with no way to distinguish
    // it from a genuinely bad file. A short delay + one retry is enough
    // for that propagation race to resolve without meaningfully
    // delaying the common case where the fetch just works the first time.
    const attemptFetch = (attemptsLeft) =>
      fetch(cfg.dataUrl, { cache: "no-store" })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
          return res.json();
        })
        .catch((err) => {
          if (attemptsLeft > 0) {
            console.warn(`Fetch/parse failed for ${gameId}, retrying once in 1.5s:`, err);
            return new Promise((resolve) => setTimeout(resolve, 1500)).then(() => attemptFetch(attemptsLeft - 1));
          }
          throw err;
        });

    attemptFetch(1)
      .then((data) => {
        const fetchedRegions = data.regions || {};
        const anyTeams = Object.values(fetchedRegions).some(
          (r) => r.teams && Object.keys(r.teams).length > 0
        );
        if (!anyTeams) {
          throw new Error(`Fetched ${gameId} data but every region had empty 'teams'`);
        }
        // Merge per-region: a region missing or empty in the fetch keeps its
        // fallback rather than the whole game losing all regions over one
        // partial failure.
        setDataByGame((prev) => {
          const merged = { ...prev[gameId] };
          for (const key of cfg.regionList) {
            const fetched = fetchedRegions[key];
            if (fetched && fetched.teams && Object.keys(fetched.teams).length > 0) {
              merged[key] = fetched;
            }
          }
          return { ...prev, [gameId]: merged };
        });
        setLastUpdatedByGame((prev) => ({
          ...prev,
          [gameId]: data.generated_at || null,
        }));
        setStatusByGame((prev) => ({ ...prev, [gameId]: "live" }));
      })
      .catch((err) => {
        console.warn(`Live data fetch failed for ${gameId}, using bundled snapshot:`, err);
        setErrorByGame((prev) => ({ ...prev, [gameId]: err && err.message ? err.message : String(err) }));
        setStatusByGame((prev) => ({ ...prev, [gameId]: "error" }));
      });
  };

  useEffect(() => {
    GAME_LIST.forEach(fetchGameData);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Separate, simple fetch for champion_stats.json — a standalone
  // reference table, not tied to any one game's live snapshot, so it
  // doesn't need the same per-game merge/fallback machinery above. Same
  // one-retry-on-failure treatment as the main data fetches, for the
  // same CDN-propagation-race reason.
  //
  // NOTE on scope: an earlier design here tried to use Fearless Draft
  // ban-pool carryover from a team's PRIOR series to inform an UPCOMING
  // one. That doesn't actually hold up on reflection, for two
  // independent reasons: (1) this app's data model only ever captures
  // FULLY COMPLETED series as single past_matches entries — there's no
  // "game 1 done, game 2 pending" state the scrape cadence would ever
  // observe: and (2) Fearless ban-pool carryover is scoped to a single
  // series anyway, not across separate series on different days, so
  // even with finer-grained data it wouldn't be the right signal for a
  // genuinely new, upcoming series. What's actually usable here instead
  // is champion-pool CONSISTENCY as a confidence signal (see
  // championPoolConsistency below) — it only needs a player's own past
  // performance, which is always known ahead of time, unlike an
  // opponent's future draft.
  const [championStats, setChampionStats] = useState(null);
  const [propsData, setPropsData] = useState(null);
  useEffect(() => {
    const attemptFetch = (attemptsLeft) =>
      fetch(DATA_URL_CHAMPION_STATS, { cache: "no-store" })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
          return res.json();
        })
        .catch((err) => {
          if (attemptsLeft > 0) {
            return new Promise((resolve) => setTimeout(resolve, 1500)).then(() => attemptFetch(attemptsLeft - 1));
          }
          throw err;
        });
    // Props are optional: the file may not exist yet, or the provider may
    // have had nothing posted. A miss is silent by design — the app is
    // fully usable without lines, and an error banner for "no bets posted
    // right now" would be noise.
    fetch(DATA_URL_PROPS, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setPropsData(data))
      .catch(() => setPropsData(null));

    attemptFetch(1)
      .then((data) => setChampionStats(data))
      .catch((err) => console.warn("Champion stats fetch failed (champion-pool consistency insight will be unavailable):", err));
  }, []);

  const gameCfg = GAMES[game];
  const scrapedRegions = dataByGame[game] || gameCfg.fallbackRegions;
  /* The board's own fixtures, folded in before anything reads the list.

     Here rather than inside EdgesTab because all three tabs have to agree:
     a fixture the Edges view projects and the Upcoming view does not list is
     worse than not having it. Memoized because withBoardFixtures walks the
     whole board against every region's roster, and because it returns the
     same object when there is nothing to add -- which historyPool's WeakMap
     and the hooks below key on.

     The workflow does this too, twice a day, and the two are idempotent so
     the overlap costs nothing. This pass is what makes a line posted since
     the last scrape visible for the hours it is live. */
  const regionsData = useMemo(
    () => withBoardFixtures(scrapedRegions, propsData, game),
    [scrapedRegions, propsData, game]);
  const current = regionsData[region] || { teams: {}, past_matches: [], upcoming_matches: [] };
  /* Split deliberately. `history` is what a player has on record and feeds
     every projection, form chart and consistency score; `current.past_matches`
     is what THIS region has played and feeds standings and past results.
     They are the same list everywhere except a borrowed-roster event that
     has not started yet — see historyPoolFor. */
  const history = historyPool(regionsData, region);
  /* Recomputed only when the data or the tab changes, not per card:
     resolving a home league walks every other region's match list, and
     a board renders dozens of cards. */
  const homeRegion = useMemo(() => homeRegionLookup(regionsData, region),
                             [regionsData, region]);
  /* True when the pool carries matches this region did not play. Was
     "the region has played nothing at all", which stopped being the same
     question once borrowing went per team: an event mid-way through has
     its own matches AND its not-yet-started teams' home seasons. */
  const historyIsBorrowed = history.length > (current.past_matches || []).length;
  const hasData = Object.keys(current.teams || {}).length > 0;
  const normalizedUpcoming = formatUpcoming(current.upcoming_matches || []);

  const selectGame = (id) => {
    setGame(id);
    setRegion(GAMES[id].regionList[0]); // reset to that game's first region
    // Headshots exist for CS2 and nowhere else, so leaving the selection
    // on it while switching to LoL would ask for a stat that game does not
    // record — every projection would come back null and the page would
    // show a column of blanks with nothing to explain them.
    const available = statsForGame(id).map(([key]) => key);
    if (!available.includes(statType)) setStatType(available[0]);
  };

  return (
    <ThemeContext.Provider value={theme}>
    <ChampionStatsContext.Provider value={championStats}>
    <PropsContext.Provider value={propsData}>
    <HomeRegionContext.Provider value={homeRegion}>
      <div style={{
        minHeight: "100vh", color: theme.text, fontFamily: "'Inter', -apple-system, sans-serif", padding: isDesktop ? "32px 32px 60px" : "20px 16px 56px",
        // Exposed to CSS so the stylesheet's focus rings track the live
        // accent without a rule per game.
        "--kp-accent-live": theme.accent,
        background: `radial-gradient(ellipse 1200px 800px at 50% -10%, ${theme.accent}0d, transparent 60%), `
          + `linear-gradient(${theme.steelSoft}33 1px, transparent 1px), linear-gradient(90deg, ${theme.steelSoft}33 1px, transparent 1px), `
          + theme.void,
        backgroundSize: "auto, 48px 48px, 48px 48px, auto",
      }}>
        <style>{`
          /* Everything else in this app is inline styles, which can't do
             :hover or animation — this is the one shared stylesheet, kept
             small and general-purpose rather than styling every element
             individually. Buttons/clickable cards opt in via className. */
          .kp-btn { transition: filter 0.15s ease, transform 0.1s ease, border-color 0.15s ease, background-color 0.15s ease; }
          .kp-btn:hover { filter: brightness(1.18); }
          .kp-btn:active { transform: scale(0.97); }
          .kp-clickable { transition: border-color 0.15s ease, filter 0.15s ease; cursor: pointer; }
          .kp-clickable:hover { filter: brightness(1.08); border-color: rgba(255,255,255,0.16) !important; }
          input[type="range"] { cursor: pointer; }
          input[type="range"]::-webkit-slider-thumb { transition: transform 0.15s ease; }
          input[type="range"]:hover::-webkit-slider-thumb { transform: scale(1.25); }
          @keyframes kp-fade-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
          @keyframes kp-backdrop-in { from { opacity: 0; } to { opacity: 1; } }
          .kp-menu-anim { animation: kp-fade-in 0.16s ease; }
          .kp-backdrop-anim { animation: kp-backdrop-in 0.16s ease; }
          /* Targeting-bracket corner marks — the two sharp corners the
             angular clip-path doesn't cut (top-left, bottom-right) get
             small viewfinder-style accent marks. A deliberate nod to the
             "kill projector / reticle" identity rather than generic corner
             decoration — only makes sense paired with the angular corner
             style, so it's applied conditionally in JS, not always-on. */
          .kp-bracket { position: relative; }
          .kp-bracket::before, .kp-bracket::after {
            content: ""; position: absolute; width: 11px; height: 11px; pointer-events: none; opacity: 0.85;
          }
          .kp-bracket::before { top: 2px; left: 2px; border-top: 1.5px solid var(--kp-bracket-color); border-left: 1.5px solid var(--kp-bracket-color); }
          .kp-bracket::after { bottom: 2px; right: 2px; border-bottom: 1.5px solid var(--kp-bracket-color); border-right: 1.5px solid var(--kp-bracket-color); }
          .kp-chip {
            display: inline-flex; align-items: center; padding: 3px 9px; border-radius: 20px;
            font-size: 10px; font-weight: 600; letter-spacing: 0.3px; font-family: 'Inter', sans-serif;
          }
          /* Day picker scroll row — a thin, quiet scrollbar rather than
             the OS default, which is heavy enough to dominate a row of
             small chips. Still visible (not hidden) so it stays obvious
             that the row scrolls when it overflows. */
          .kp-dayscroll { scrollbar-width: thin; -webkit-overflow-scrolling: touch; }
          .kp-dayscroll::-webkit-scrollbar { height: 6px; }
          .kp-dayscroll::-webkit-scrollbar-track { background: transparent; }
          .kp-dayscroll::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.14); border-radius: 3px;
          }
          .kp-dayscroll::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.24); }
          .kp-divider { border: none; height: 1px; }
          /* ---- Numerals -------------------------------------------------
             Every figure in this app sits in a column that is compared
             against the figure above it. Proportional digits make those
             columns ragged and make a changing number jitter, so all
             numeric readouts use tabular figures. */
          .kp-num { font-family: 'IBM Plex Mono', monospace; font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }
          table, .kp-tabular { font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }

          /* ---- Focus ----------------------------------------------------
             Cards and chips are divs with onClick, which gave keyboard
             users no way in and no visible focus. Anything interactive now
             takes focus and shows it, without adding a ring for mouse
             users. */
          .kp-btn:focus-visible, .kp-clickable:focus-visible, .kp-focus:focus-visible,
          button:focus-visible, [role="button"]:focus-visible, input:focus-visible, select:focus-visible {
            outline: 2px solid var(--kp-accent-live, #C9A86A);
            outline-offset: 2px;
          }
          .kp-btn:focus:not(:focus-visible), .kp-clickable:focus:not(:focus-visible) { outline: none; }

          /* Hairline separators that read as structure rather than as lines. */
          .kp-row + .kp-row { border-top: 1px solid rgba(255,255,255,0.045); }

          /* ---- Motion ---------------------------------------------------
             Respect the OS setting. Animation here is decoration; nothing
             depends on it. */
          @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
              animation-duration: 0.01ms !important; animation-iteration-count: 1 !important;
              transition-duration: 0.01ms !important; scroll-behavior: auto !important;
            }
          }

          /* Content appears as data resolves; a 1-frame fade stops tabs
             from feeling like a hard cut. */
          @keyframes kp-rise { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
          .kp-rise { animation: kp-rise 0.22s ease both; }
        `}</style>
        <div style={{
          maxWidth: isDesktop ? 1400 : 640, margin: "0 auto",
          display: isDesktop ? "flex" : "block", alignItems: "flex-start", gap: isDesktop ? 32 : 0,
        }}>
          {/* ---------- Sidebar on desktop / top stack on mobile: header +
              game/status/games-count/advanced controls. Region/View/Stat
              live in TopNav now, not here — see below. ---------- */}
          <div style={{ width: isDesktop ? 260 : "100%", flexShrink: 0, position: isDesktop ? "sticky" : "static", top: isDesktop ? 32 : "auto" }}>
            {/* The full masthead is a desktop luxury — on a phone the title
                and its explanation were ~180px of the first screen before
                anything actionable. The phone keeps the wordmark and drops
                the rest to a single line. */}
            <div style={{ marginBottom: isDesktop ? 18 : 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: isDesktop ? 11 : 10.5, letterSpacing: 2, color: theme.accent, fontFamily: "'Inter', sans-serif", fontWeight: 600 }}>
                <Reticle size={13} color={theme.accent} active />
                KILL PROJECTOR
              </div>
              <h1 style={{
                fontSize: isDesktop ? 26 : 19, fontWeight: 600, margin: isDesktop ? "6px 0 0" : "5px 0 0",
                fontFamily: "'Fraunces', serif", letterSpacing: -0.2, lineHeight: 1.2,
              }}>
                Weighted {STAT_TYPES[statType].singular} projections
              </h1>
              {isDesktop && (
                <div style={{ fontSize: 13, color: theme.textDim, marginTop: 4, lineHeight: 1.5 }}>
                  Blends this split's rates with prior-split history, opponent strength and kill participation.
                </div>
              )}
            </div>

            <GameSwitcher game={game} selectGame={selectGame} statusByGame={statusByGame} theme={theme} />

            <DataStatus
              status={statusByGame[game]}
              lastUpdated={lastUpdatedByGame[game]}
              // The regions themselves, so the badge can report how old
              // the DATA is rather than when the scraper last ran.
              regions={regionsData}
              errorDetail={errorByGame[game]}
              onRetry={() => fetchGameData(game)}
            />

            {!isDesktop && (
              <TopNav
                theme={theme} gameCfg={gameCfg} game={game}
                region={region} setRegion={setRegion}
                tab={tab} setTab={setTab}
                statType={statType} setStatType={setStatType}
                isDesktop={isDesktop}
              />
            )}

            {/* On desktop the sidebar has room for the tuning controls beside
                the content. On a phone they stack ON TOP of it: every control
                in this column came before the first projection, so the thing
                the app is for started roughly a screen and a half down. They
                render below the content instead — see the main column. */}
            {isDesktop && (
              <>
                <GamesControl theme={theme} games={games} setGames={setGames} />

                {/* ---------- Advanced/optional model tuning, collapsed by default ---------- */}
                <WeightControls
                  weights={weights} onChangeWeight={setWeight}
                  expanded={slidersExpanded} onToggleExpanded={() => setSlidersExpanded(!slidersExpanded)}
                  statLabel={STAT_TYPES[statType].label}
                  defaults={(DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game] || {})[statType]}
                />

                <SettingsPanel
                  theme={theme}
                  accentOverride={accentOverride} setAccentOverride={setAccentOverride}
                  cornerStyle={cornerStyle} setCornerStyle={setCornerStyle}
                  open={settingsOpen} setOpen={setSettingsOpen}
                />
              </>
            )}
          </div>

          {/* ---------- Main content: TopNav (desktop only — full width here
              instead of squeezed into the sidebar) + whichever tab is selected ---------- */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {isDesktop && (
              <TopNav
                theme={theme} gameCfg={gameCfg} game={game}
                region={region} setRegion={setRegion}
                tab={tab} setTab={setTab}
                statType={statType} setStatType={setStatType}
                isDesktop={isDesktop}
              />
            )}

            {!hasData ? (
              <div style={{ background: theme.graphite, border: `1px solid ${theme.steel}`, ...cardShape(theme.cornerStyle), ...elevation(), padding: "20px 16px", textAlign: "center" }}>
                <div style={{ fontSize: 13, color: theme.textDim }}>No {gameCfg.regionLabels[region]} data available yet.</div>
                <div style={{ fontSize: 12, color: theme.textFaint, marginTop: 4 }}>
                  {statusByGame[game] === "live" || statusByGame[game] === "loading"
                    ? "This region may not have loaded from the live source — try refresh above."
                    : "The live source hasn't loaded, and there's no offline snapshot for this region yet."}
                </div>
              </div>
            ) : (
              <>
                {/* The backtest headline applies to the projection-bearing
                    tabs; standings and consistency are descriptive, not
                    predictive, so it would be claiming something there
                    that those views do not show. */}
                {(tab === "future" || tab === "past") && (
                  <AccuracySummary
                    teams={current.teams} pastMatches={history} borrowedHistory={historyIsBorrowed}
                    weights={weights} statType={statType} isDesktop={isDesktop}
                  />
                )}
                {tab === "future" ? (
              <FutureTab teams={current.teams} pastMatches={history} upcomingMatches={normalizedUpcoming} weights={weights} statType={statType} isDesktop={isDesktop} games={games} game={game} />
            ) : tab === "edges" ? (
              <EdgesTab regionsData={regionsData} regionList={gameCfg.regionList} regionLabels={gameCfg.regionLabels}
                        weights={weights} statType={statType} game={game} isDesktop={isDesktop} />
            ) : tab === "parlays" ? (
              <ParlaysTab dataByGame={dataByGame} propsData={propsData}
                          weightsByGameAndStat={weightsByGameAndStat} isDesktop={isDesktop} />
            ) : tab === "record" ? (
              <RecordTab regionsData={regionsData} regionList={gameCfg.regionList}
                         weights={weights} statType={statType} isDesktop={isDesktop} />
            ) : tab === "past" ? (
              <PastResultsTab teams={current.teams} pastMatches={current.past_matches || []} weights={weights} statType={statType} isDesktop={isDesktop} />
            ) : tab === "consistency" ? (
              <ConsistencyTab teams={current.teams} pastMatches={history} statType={statType} isDesktop={isDesktop} />
            ) : (
              <StandingsTab
                teams={current.teams} pastMatches={current.past_matches || []} upcomingMatches={normalizedUpcoming}
                regionsData={regionsData} regionList={gameCfg.regionList} regionLabels={gameCfg.regionLabels}
                isDesktop={isDesktop}
              />
                )}
              </>
            )}

            {!isDesktop && (
              <div style={{ marginTop: 24 }}>
                <GamesControl theme={theme} games={games} setGames={setGames} />
                <WeightControls
                  weights={weights} onChangeWeight={setWeight}
                  expanded={slidersExpanded} onToggleExpanded={() => setSlidersExpanded(!slidersExpanded)}
                  statLabel={STAT_TYPES[statType].label}
                  defaults={(DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game] || {})[statType]}
                />
                <SettingsPanel
                  theme={theme}
                  accentOverride={accentOverride} setAccentOverride={setAccentOverride}
                  cornerStyle={cornerStyle} setCornerStyle={setCornerStyle}
                  open={settingsOpen} setOpen={setSettingsOpen}
                />
              </div>
            )}

            <div style={{ marginTop: 20, fontSize: 11, color: theme.textFaint, lineHeight: 1.6 }}>
              Data: {game === "lol" ? "regular-season box scores (gol.gg) and schedule (LoL Esports API)" : "match stats (VLR.gg)"}.
            </div>
          </div>
        </div>
      </div>
    </HomeRegionContext.Provider>
    </PropsContext.Provider>
    </ChampionStatsContext.Provider>
    </ThemeContext.Provider>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<KillProjector />);

} catch (err) {
  document.getElementById('root').innerHTML =
    '<div id="error-box">Render error: ' + err.message + '\n\n' + (err.stack || '') + '</div>';
}
