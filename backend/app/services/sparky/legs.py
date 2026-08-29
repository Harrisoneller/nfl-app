"""The leg universe: every bet on the slate that could go into a parlay.

The old engine's leg universe was "home moneyline or away moneyline", which is
the worst possible menu for NFL betting. A -2000 favourite adds essentially
no payout and real risk; a +1400 dog is a lottery ticket. The exploitable
markets in the NFL are spreads and totals, where the number itself is the thing the
model has an opinion about, and where the app already has an exact
distribution to price against (``dist_model.GameDistribution``).

What this module does
---------------------
Given, per game, the model's outcome probabilities and the book prices actually
on offer, it produces a flat list of :class:`LegCandidate` — each one a
concrete, priceable bet with:

  - a **fair probability** de-vigged from the market,
  - a **shrunk model probability** (see :mod:`shrinkage`),
  - an explicit **push probability**, from the discrete margin/total mass sitting
    exactly on the number,
  - the **best price across books**, and
  - **factor loadings** describing what the leg is really exposed to, which is
    what lets :mod:`correlation` price a ticket honestly.

Two sourcing rules that matter more than they look
--------------------------------------------------
1. **Fair probability comes from the consensus; EV comes from the best price.**
   Those must be different numbers or there is no such thing as line shopping.
   The consensus pair tells us what the market thinks; the best available price
   tells us what we can actually get. Edge is the gap.

2. **A leg with no book price is not created.** The previous engine, when a
   quote was missing, synthesized one from the model's own probability plus a
   3% hold. That leg's "market implied probability" was then a deterministic
   function of the model probability, so its edge was a constant manufactured
   out of nothing — and because the ranking sorted on edge, those phantom legs
   floated straight to the top of the recommendations. There is no fallback
   here. No price, no leg.

De-vig method
-------------
Two-way markets are de-vigged with the **power** method rather than
proportional normalization. Books do not spread their hold evenly; they load it
onto the longshot. On a -110/-110 the two agree exactly, but on a -2000/+1100
moneyline they differ by about 2.5 points of probability — comfortably more
than any edge we would be trying to detect. See ``odds_math.devig_power``.

Pure module: the caller (``sparky_service``) supplies model probabilities
already computed from ``dist_model`` and prices already read from the DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import odds_math
from .correlation import CH_FAVORITE, CH_MODEL, CH_SCORING, N_CHANNELS
from .odds_math import clamp
from .shrinkage import EdgeShrink, PlattMap, calibrate, shrink_toward_market

MARKET_MONEYLINE = "moneyline"
MARKET_SPREAD = "spread"
MARKET_TOTAL = "total"

#: Default factor-loading magnitudes. Registry-tunable through the service.
RHO_MODEL = 0.55      # "are our numbers right today" — every leg is exposed
RHO_FAVORITE = 0.20   # "does chalk hold this slate" — ML and spread legs
RHO_SCORING = 0.30    # "is this a high-scoring slate" — total legs

#: Total squared loading is capped below 1 so every leg keeps idiosyncratic
#: variance; without this a leg would carry no independent information at all.
_MAX_TOTAL_LOADING = 0.95


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SidePrices:
    """Book prices for the two sides of one market.

    ``consensus_*`` is what the market thinks (median across books, used for
    de-vig); ``best_*`` is the best number a bettor can actually get (used for
    EV and payout). They are separate fields on purpose — see the module
    docstring.
    """

    consensus_a: int | None = None
    consensus_b: int | None = None
    best_a: int | None = None
    best_b: int | None = None
    best_book_a: str | None = None
    best_book_b: str | None = None
    #: Line (points) attached to this market. None for moneyline. For spreads
    #: this is the **home** handicap (negative = home favoured); for totals it
    #: is the total. Side A always takes the line as stated.
    line: float | None = None
    n_books: int = 0

    #: True when this is not the line the market has settled on — a number only
    #: some books hang. Alternate lines are where key-number value lives (a lone
    #: -6.5 against a -7.5 consensus is worth roughly two points of win
    #: probability), so they are enumerated rather than discarded, but they are
    #: labelled because they are thinner and easier to be wrong about.
    is_alt: bool = False

    #: De-vigged probabilities for the two sides, when the caller derived them
    #: rather than leaving them to be read off a quoted pair. Used for an
    #: alternate line that only one side of is quoted anywhere: the consensus
    #: line is inverted into a market-implied distribution and re-evaluated at
    #: this number. Set ``fair_source`` to say which happened.
    fair_a: float | None = None
    fair_b: float | None = None
    fair_source: str = "quoted"     # 'quoted' | 'derived'

    def complete(self) -> bool:
        """Priceable: a fair pair (quoted or derived) and at least one best price."""
        has_fair = (
            (self.consensus_a is not None and self.consensus_b is not None)
            or (self.fair_a is not None and self.fair_b is not None)
        )
        return has_fair and (self.best_a is not None or self.best_b is not None)


@dataclass(frozen=True)
class ModelTriple:
    """Model probabilities for a three-outcome market: ``(a wins, push, b wins)``.

    Comes from ``dist_model.GameDistribution.cover_prob_discrete`` for spreads
    and the total equivalent — i.e. from the discrete, key-number-aware PMF, so
    the push mass is exact at whole numbers and exactly zero at half-points,
    rather than being read off a lookup table.
    """

    a: float
    push: float
    b: float

    def normalized(self) -> ModelTriple:
        z = self.a + self.push + self.b
        if z <= 0:
            return ModelTriple(0.5, 0.0, 0.5)
        return ModelTriple(self.a / z, self.push / z, self.b / z)


@dataclass(frozen=True)
class GameLegInputs:
    """Everything needed to enumerate one game's candidate legs."""

    event_id: str
    home_id: str | None
    away_id: str | None
    label: str = ""                      # e.g. "KC @ BUF"

    # Moneyline
    ml_prices: SidePrices | None = None  # side A = home, side B = away
    model_home_win: float | None = None  # ensemble home win probability

    # Spread (side A = home at `line`, side B = away at `-line`)
    spread_prices: SidePrices | None = None
    model_spread: ModelTriple | None = None

    # Total (side A = over, side B = under)
    total_prices: SidePrices | None = None
    model_total: ModelTriple | None = None

    #: Every quoted line, not just the consensus one. Each entry pairs the
    #: prices at that number with the model's three-outcome view *at that same
    #: number* — they have to be evaluated together or a -6.5 leg gets priced
    #: against the -7.5 distribution, which is the exact error the modal-line
    #: collapse used to hide. When these are supplied they replace
    #: ``spread_prices``/``total_prices`` entirely; the singular fields remain
    #: for callers (and tests) that only ever had one line.
    spread_markets: tuple[tuple[SidePrices, ModelTriple], ...] = ()
    total_markets: tuple[tuple[SidePrices, ModelTriple], ...] = ()

    favorite: str = "home"               # 'home' | 'away'


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


@dataclass
class LegCandidate:
    """One concrete, priceable bet."""

    key: str                     # unique: "{event_id}:{market}:{side}"
    event_id: str
    market: str                  # moneyline | spread | total
    side: str                    # home/away for ml+spread, over/under for total
    label: str                   # human-readable, e.g. "KC -3.5"
    team_id: str | None
    opponent_id: str | None
    line: float | None

    price_american: int
    decimal_odds: float
    book: str | None

    fair_prob: float             # de-vigged market probability for this side
    model_prob: float            # calibrated model probability (pre-shrinkage)
    prob: float                  # post-shrinkage probability — the one we bet on
    push_prob: float

    edge: float                  # prob - fair_prob (both no-vig, apples to apples)
    expected_value: float        # EV per unit at the offered price, push-aware
    tau: float
    loadings: tuple[float, ...]
    is_favorite: bool
    n_books: int = 0
    notes: list[str] = field(default_factory=list)
    #: Not the number the market settled on — see ``SidePrices.is_alt``.
    is_alt: bool = False
    #: 'quoted' when the fair price was de-vigged from a two-way market at this
    #: exact number; 'derived' when it was read off the market-implied
    #: distribution because no such market exists here.
    fair_source: str = "quoted"

    # --- compatibility with the pre-rebuild Leg surface ------------------- #

    @property
    def is_underdog(self) -> bool:
        return not self.is_favorite

    @property
    def unconditional_win_prob(self) -> float:
        """P(this leg wins outright), push mass removed.

        ``prob`` is conditional on no push — that is the basis the book's two
        prices are quoted on, and the basis :mod:`shrinkage` operates on. Any
        code that settles this leg against an actual outcome (the same-game
        lattice, grading, Monte Carlo) needs the unconditional number, and the
        two differ by the whole push mass. Getting this backwards inflates a
        whole-number leg by roughly its push probability.
        """
        return clamp(self.prob, 0.0, 1.0) * max(0.0, 1.0 - self.push_prob)

    @property
    def win_prob(self) -> float:
        """The probability actually bet on (post-calibration, post-shrinkage)."""
        return self.prob

    @property
    def market_implied(self) -> float:
        """Vig-included implied probability at this leg's own price.

        Kept because the old UI displays it, but note it is **not** what
        ``edge`` is measured against: an edge has to be computed against the
        *fair* (de-vigged) number, or the book's hold silently counts as model
        edge. ``fair_prob`` is that number.
        """
        return odds_math.american_to_implied(self.price_american)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "is_underdog": self.is_underdog,
            "win_prob": round(self.prob, 4),
            "market_implied": round(self.market_implied, 4),
            "is_value": self.expected_value > 0,
            "event_id": self.event_id,
            "market": self.market,
            "side": self.side,
            "label": self.label,
            "team_id": self.team_id,
            "opponent_id": self.opponent_id,
            "line": self.line,
            "price_american": self.price_american,
            "decimal_odds": round(self.decimal_odds, 4),
            "book": self.book,
            "fair_prob": round(self.fair_prob, 4),
            "model_prob": round(self.model_prob, 4),
            "prob": round(self.prob, 4),
            "push_prob": round(self.push_prob, 4),
            "edge": round(self.edge, 4),
            "expected_value": round(self.expected_value, 4),
            "tau": round(self.tau, 4),
            "is_favorite": self.is_favorite,
            "n_books": self.n_books,
            "notes": list(self.notes),
            "is_alt": self.is_alt,
            "fair_source": self.fair_source,
        }


# --------------------------------------------------------------------------- #
# Loadings
# --------------------------------------------------------------------------- #


def _loadings(
    market: str,
    *,
    edge_sign: float,
    is_favorite: bool,
    is_over: bool | None,
    rho_model: float = RHO_MODEL,
    rho_favorite: float = RHO_FAVORITE,
    rho_scoring: float = RHO_SCORING,
) -> tuple[float, ...]:
    """What is this leg actually exposed to, beyond its own game?

    - **Model channel.** Every leg loads here, signed by whether it is taking
      the model's side of the disagreement. Two legs that both back the model
      rise and fall together; a leg backing the model stacked with one backing
      the market partly cancels. This is the dominant channel and it is why a
      parlay is one bet on the model, not N independent bets.
    - **Favourite channel.** Moneyline and spread legs load with the sign of the
      side taken. Slates really do have chalk weeks and dog weeks.
    - **Scoring channel.** Totals load with the sign of over/under; a slate
      shares weather systems, officiating and rule regime. Spread legs are left
      at zero here rather than given a speculative small loading — the direction
      of that effect is not something this codebase has measured, and inventing
      it would put an unearned number into the price.
    """
    out = [0.0] * N_CHANNELS
    out[CH_MODEL] = rho_model * (1.0 if edge_sign >= 0 else -1.0)
    if market in (MARKET_MONEYLINE, MARKET_SPREAD):
        out[CH_FAVORITE] = rho_favorite * (1.0 if is_favorite else -1.0)
    elif market == MARKET_TOTAL and is_over is not None:
        out[CH_SCORING] = rho_scoring * (1.0 if is_over else -1.0)

    norm_sq = sum(x * x for x in out)
    if norm_sq > _MAX_TOTAL_LOADING ** 2:
        scale = _MAX_TOTAL_LOADING / (norm_sq ** 0.5)
        out = [x * scale for x in out]
    return tuple(out)


# --------------------------------------------------------------------------- #
# Leg construction
# --------------------------------------------------------------------------- #


def _make_leg(
    *,
    game: GameLegInputs,
    market: str,
    side: str,
    label: str,
    team_id: str | None,
    opponent_id: str | None,
    line: float | None,
    price: int,
    book: str | None,
    fair_prob: float,
    raw_model_prob: float,
    push_prob: float,
    is_favorite: bool,
    is_over: bool | None,
    shrink: EdgeShrink | None,
    cal: PlattMap | None,
    tau: float,
    n_books: int,
    is_alt: bool = False,
    fair_source: str = "quoted",
) -> LegCandidate:
    model_p = clamp(calibrate(clamp(raw_model_prob, 1e-4, 1 - 1e-4), cal), 1e-4, 1 - 1e-4)

    # Shrink against the *fair* (de-vigged) market number, not the raw price:
    # the vig is the book's fee, not the market's opinion, and shrinking toward
    # a vigged number would drag every leg toward -EV by construction.
    shrunk = shrink_toward_market(model_p, clamp(fair_prob, 1e-4, 1 - 1e-4), shrink)

    # Push mass is fixed by the line; win/lose share what is left.
    room = max(0.0, 1.0 - push_prob)
    win_p = clamp(shrunk, 0.0, 1.0) * room

    dec = odds_math.american_to_decimal(price)
    ev = win_p * dec + push_prob - 1.0

    edge_sign = 1.0 if shrunk >= fair_prob else -1.0

    notes: list[str] = []
    if is_alt:
        notes.append("alternate line — not the number the market settled on")
    if fair_source == "derived":
        notes.append(
            "fair price derived from the consensus line, not from a quoted "
            "two-way market at this number"
        )

    return LegCandidate(
        key=_leg_key(game.event_id, market, side, line),
        event_id=game.event_id,
        market=market,
        side=side,
        label=label,
        team_id=team_id,
        opponent_id=opponent_id,
        line=line,
        price_american=int(price),
        decimal_odds=dec,
        book=book,
        fair_prob=fair_prob,
        model_prob=model_p,
        prob=shrunk,
        push_prob=push_prob,
        edge=shrunk - fair_prob,
        expected_value=ev,
        tau=tau,
        loadings=_loadings(
            market, edge_sign=edge_sign, is_favorite=is_favorite, is_over=is_over,
        ),
        is_favorite=is_favorite,
        n_books=n_books,
        notes=notes,
        is_alt=is_alt,
        fair_source=fair_source,
    )


def _leg_key(event_id: str, market: str, side: str, line: float | None) -> str:
    """Stable leg identifier, qualified by the line.

    The line has to be in the key. Before alternate lines existed there was
    exactly one spread and one total per game, so ``event:market:side`` was
    unique; with every quoted number enumerated it is not, and two different
    bets sharing an id silently collapse in every dict keyed by it — the
    leg-reuse cap, the hand-built ticket lookup, settlement. ``event_id`` stays
    the first segment because callers recover the game with ``split(":")[0]``.
    """
    if line is None:
        return f"{event_id}:{market}:{side}"
    return f"{event_id}:{market}:{side}:{line:g}"


def _two_sided(
    game: GameLegInputs,
    prices: SidePrices,
    model: ModelTriple,
    *,
    market: str,
    side_a: str,
    side_b: str,
    label_a: str,
    label_b: str,
    team_a: str | None,
    team_b: str | None,
    line_a: float | None,
    line_b: float | None,
    fav_a: bool,
    fav_b: bool,
    over_a: bool | None,
    over_b: bool | None,
    shrink: EdgeShrink | None,
    cal: PlattMap | None,
    tau: float,
) -> list[LegCandidate]:
    """Build both sides of one two-way market, or nothing at all."""
    if not prices.complete():
        return []

    # Fair probabilities from the consensus pair, power-de-vigged. These are
    # probabilities *conditional on no push*, which is the right basis: the
    # book's two prices also only pay out when there is no push.
    if prices.fair_a is not None and prices.fair_b is not None:
        z = prices.fair_a + prices.fair_b
        fair_a, fair_b = (prices.fair_a / z, prices.fair_b / z) if z > 0 else (0.5, 0.5)
    else:
        fair_a, fair_b = odds_math.devig_power(
            [prices.consensus_a, prices.consensus_b]
        )

    m = model.normalized()
    room = max(1e-9, 1.0 - m.push)
    model_a = clamp(m.a / room, 1e-4, 1 - 1e-4)
    model_b = clamp(m.b / room, 1e-4, 1 - 1e-4)

    out: list[LegCandidate] = []
    for (side, label, team, opp, line, price, book, fair, mp, is_fav, is_over) in (
        (side_a, label_a, team_a, team_b, line_a, prices.best_a, prices.best_book_a,
         fair_a, model_a, fav_a, over_a),
        (side_b, label_b, team_b, team_a, line_b, prices.best_b, prices.best_book_b,
         fair_b, model_b, fav_b, over_b),
    ):
        if price is None:
            continue  # no price, no leg — never synthesize one
        out.append(_make_leg(
            game=game, market=market, side=side, label=label,
            team_id=team, opponent_id=opp, line=line, price=int(price), book=book,
            fair_prob=clamp(fair, 1e-4, 1 - 1e-4), raw_model_prob=mp,
            push_prob=clamp(m.push, 0.0, 0.5),
            is_favorite=is_fav, is_over=is_over,
            shrink=shrink, cal=cal, tau=tau, n_books=prices.n_books,
            is_alt=prices.is_alt, fair_source=prices.fair_source,
        ))
    return out


def build_game_legs(
    game: GameLegInputs,
    *,
    shrink: EdgeShrink | None = None,
    cal: PlattMap | None = None,
    tau: float = 0.14,
    markets: tuple[str, ...] = (MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL),
) -> list[LegCandidate]:
    """Every priceable leg for one game, across the enabled markets."""
    home = game.home_id or "HOME"
    away = game.away_id or "AWAY"
    legs: list[LegCandidate] = []

    if MARKET_MONEYLINE in markets and game.ml_prices and game.model_home_win is not None:
        legs += _two_sided(
            game, game.ml_prices,
            ModelTriple(a=float(game.model_home_win), push=0.0,
                        b=1.0 - float(game.model_home_win)),
            market=MARKET_MONEYLINE, side_a="home", side_b="away",
            label_a=f"{home} ML", label_b=f"{away} ML",
            team_a=game.home_id, team_b=game.away_id,
            line_a=None, line_b=None,
            fav_a=(game.favorite == "home"), fav_b=(game.favorite == "away"),
            over_a=None, over_b=None,
            shrink=shrink, cal=cal, tau=tau,
        )

    if MARKET_SPREAD in markets:
        for prices, model in _market_lines(game.spread_markets,
                                           game.spread_prices, game.model_spread):
            line = prices.line
            if line is None:
                continue
            legs += _two_sided(
                game, prices, model,
                market=MARKET_SPREAD, side_a="home", side_b="away",
                label_a=f"{home} {line:+g}", label_b=f"{away} {-line:+g}",
                team_a=game.home_id, team_b=game.away_id,
                line_a=line, line_b=-line,
                fav_a=(line < 0), fav_b=(line > 0),
                over_a=None, over_b=None,
                shrink=shrink, cal=cal, tau=tau,
            )

    if MARKET_TOTAL in markets:
        for prices, model in _market_lines(game.total_markets,
                                           game.total_prices, game.model_total):
            line = prices.line
            if line is None:
                continue
            legs += _two_sided(
                game, prices, model,
                market=MARKET_TOTAL, side_a="over", side_b="under",
                label_a=f"Over {line:g}", label_b=f"Under {line:g}",
                team_a=None, team_b=None,
                line_a=line, line_b=line,
                fav_a=False, fav_b=False,
                over_a=True, over_b=False,
                shrink=shrink, cal=cal, tau=tau,
            )

    return legs


def _market_lines(
    multi: tuple[tuple[SidePrices, ModelTriple], ...],
    single: SidePrices | None,
    single_model: ModelTriple | None,
) -> tuple[tuple[SidePrices, ModelTriple], ...]:
    """The (prices, model) pairs to build, preferring the multi-line form."""
    if multi:
        return multi
    if single is not None and single_model is not None:
        return ((single, single_model),)
    return ()


def build_leg_pool(
    games: list[GameLegInputs],
    *,
    shrink: EdgeShrink | None = None,
    cal: PlattMap | None = None,
    tau: float = 0.14,
    markets: tuple[str, ...] = (MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL),
    min_edge: float = 0.0,
    min_books: int = 1,
    max_price: int = 1000,
    min_price: int = -3000,
) -> list[LegCandidate]:
    """The slate-wide candidate pool, filtered to legs worth searching over.

    Filters, and why each one is not arbitrary:

    - ``min_edge`` — a leg with no edge over the fair number cannot make a
      parlay +EV no matter what it is stacked with; every leg has to carry its
      own weight, because the vig compounds.
    - ``min_books`` — a "consensus" from one book is not a consensus, and its
      de-vigged number is that book's opinion plus that book's hold.
    - ``max_price`` / ``min_price`` — both tails are traps. Beyond about +1000
      the de-vig is dominated by how the book loads its hold onto longshots, so
      the fair probability is the least reliable thing on the board. Below about
      -3000 the leg contributes essentially no payout while still being able to
      lose the ticket, which is the single most common way a retail parlay dies.
    """
    pool: list[LegCandidate] = []
    for g in games:
        for leg in build_game_legs(g, shrink=shrink, cal=cal, tau=tau, markets=markets):
            if leg.n_books < min_books:
                continue
            if not (min_price <= leg.price_american <= max_price):
                continue
            if leg.edge < min_edge:
                continue
            pool.append(leg)
    # A leg key must appear once. With alternate lines enumerated, two books
    # hanging the same number produce the same key; keep the one with the better
    # price rather than letting a dict lookup pick arbitrarily later.
    best: dict[str, LegCandidate] = {}
    for leg in pool:
        prev = best.get(leg.key)
        if prev is None or leg.decimal_odds > prev.decimal_odds:
            best[leg.key] = leg
    out = list(best.values())
    out.sort(key=lambda x: (x.edge, x.expected_value), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Price-sourcing helpers (pure; the service feeds them rows from the DB)
# --------------------------------------------------------------------------- #


def best_american(prices: list[int | float]) -> int | None:
    """The bettor-optimal American price from a list of quotes.

    Compared on decimal odds, which is the only ordering that works across the
    +/- discontinuity: +105 beats -105, and -105 beats -120.
    """
    clean = [int(p) for p in prices if p is not None and int(p) != 0]
    if not clean:
        return None
    return max(clean, key=odds_math.american_to_decimal)


def median_american(prices: list[int | float]) -> int | None:
    """Consensus price: median on the *decimal* scale, converted back.

    Taking a median of American odds directly is meaningless — the scale is
    discontinuous at zero and non-monotone across it.
    """
    clean = [odds_math.american_to_decimal(p) for p in prices if p is not None and p != 0]
    if not clean:
        return None
    clean.sort()
    n = len(clean)
    mid = clean[n // 2] if n % 2 else 0.5 * (clean[n // 2 - 1] + clean[n // 2])
    return odds_math.decimal_to_american(mid)
