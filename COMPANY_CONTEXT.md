# COMPANY_CONTEXT.md

Company: **Savepoint Analytics**
Project: **Baseball Sabermetrics**
Owner/operator: **Alex Oswald**
Created: 2026-06-28

## Company purpose

Savepoint Analytics is an independent analytics consultancy focused on practical, end-to-end analytics systems. The consultancy is intended to combine data engineering, statistical modeling, experimentation, forecasting, machine learning, dashboarding, and domain expertise into affordable, useful analytical products.

This baseball sabermetrics project is Savepoint's **flagship vertical** — the first end-to-end proof that the multi-vertical platform thesis works — while still drawing on relevant experience from EA Sports FIFA / FC and broader game economy, telemetry, experimentation, and player-behavior analytics.

## Why baseball analytics fits Savepoint Analytics

Baseball analytics sits at the intersection of:

- player behavior and decision-making;
- team systems and incentives;
- noisy outcomes and process metrics;
- talent evaluation;
- pricing and salary efficiency;
- spatial-temporal analysis (pitch tracking, batted-ball, defensive positioning);
- coaching and product-style feedback loops;
- explainable public-facing analysis.

Baseball is also the most data-rich major sport — pitch-level Statcast tracking, a century-plus of play-by-play via Retrosheet, and season-level history via Lahman — which makes it the ideal vertical to demonstrate rigorous, reproducible modeling. These overlap strongly with Savepoint's existing strengths in game analytics, experimentation, telemetry design, predictive modeling, and decision support.

## Business goals

### Near-term

- Build a credible public portfolio in baseball analytics.
- Publish explainable analysis using public, properly-licensed datasets.
- Demonstrate the ability to ingest messy sports data, normalize it, model it, and communicate findings clearly.
- Ship a calibrated game-outcome prediction model and reusable player/team reports.

### Medium-term

- Offer affordable analysis packages to fans, youth/amateur coaches, media writers, agents, and small organizations.
- Use the work to pursue contract opportunities or employment in sports analytics, baseball data vendors, clubs, leagues, or media.
- Build season/playoff projection and counterfactual player-improvement tools on public data.
- Build a technical moat around reproducible pipelines and transparent models rather than one-off notebooks.

### Long-term

- Become known for rigorous, accessible baseball analytics that respects the complexity of the sport.
- Help identify undervalued talent, especially players overlooked by traditional metrics.
- Develop tactical and coaching analysis that can be understood by non-technical stakeholders.
- Build sport-agnostic analytics capabilities that can later extend to soccer, basketball, hockey, football, tennis, or other sports.

## Positioning

Savepoint Analytics should position the project as:

> Affordable, rigorous baseball analytics for people who want to understand the game empirically without reducing it to simplistic box-score metrics.

Potential audiences:

- MLB / MiLB analysts and scouts;
- club and front-office decision-makers;
- player agents and player-development staff;
- youth and amateur coaches and parents;
- baseball media and writers;
- fantasy/betting-adjacent analysts where legal and ethical boundaries are respected;
- data science hiring managers;
- sports data vendors.

## Differentiators

- Prior experience at EA Sports on the FIFA / FC franchise.
- Strong data engineering plus analytics modeling background.
- Ability to explain technical results to non-technical audiences.
- Familiarity with event telemetry, player behavior, experiments, retention, economy systems, and simulation-style thinking from games.
- Willingness to build from public, reproducible data rather than inaccessible proprietary datasets.
- License compliance treated as a first-class, professional differentiator.
- Emphasis on affordable analysis and practical decision-making.

## Service concepts

### Player value cards

Context-adjusted player profiles covering hitting (wOBA/wRC+-style rate stats, plate discipline, batted-ball quality), pitching (stuff, command, contact suppression), baserunning, defense, availability, salary/value, and uncertainty.

### Team fingerprints

Reports showing offensive and pitching profile, run-scoring and run-prevention drivers, bullpen usage, baserunning aggression, defensive efficiency, park-adjusted performance, and a "how this team wins (or loses)" decomposition via run expectancy and win probability added.

### Salary-efficiency reports

Player and team views that compare salary, playing time, age, role, performance metrics, and surplus value estimates.

### Scouting shortlists

Public-data shortlists for undervalued players by role, level, age, salary/market value, and projectability.

### Game & series breakdowns

Empirical reports that explain how a game or series was won or lost using run expectancy, win probability added, leverage, chance quality, and player-action value.

### Coaching explainers

Simple visual explanations of baseball concepts such as plate discipline, pitch sequencing, batted-ball quality, run expectancy, leverage, and decision-making.

### Prediction & simulation products

Calibrated pre-game win probabilities, season/playoff/World Series odds via Monte-Carlo simulation, and counterfactual "what if this player improved X" scenarios.

## Revenue paths

- Public reports and subscriptions.
- One-off team/player reports.
- Consulting packages for clubs or coaches.
- Data pipeline builds for small organizations.
- Contract analytics work.
- Portfolio-driven employment opportunities.
- Sponsored analysis or media partnerships, with conflict-of-interest disclosures.

## Ethical and commercial boundaries

- Be transparent about public-data limitations.
- Avoid overstating model precision; quantify uncertainty.
- Separate fan/media analysis from club-grade scouting claims.
- Do not use non-public or confidential data without permission.
- Respect all source licenses and user agreements (see `public-data-sources.md`).
- **Sell derived analysis; never resell or redistribute raw third-party data feeds.**
- Attribute sources clearly in published work, with required notices auto-stamped.
- Use gambling-related analysis cautiously and legally; keep the primary focus on sport understanding, player development, and team performance.

## Brand voice

Clear, empirical, practical, and humble. The tone should avoid hot takes and overconfidence. Baseball is complex; good analysis should quantify uncertainty and explain model limitations.

## Connection to games analytics

This project can showcase that the same core skills used in games analytics transfer to sport when adapted properly:

- telemetry ingestion → pitch/event data ingestion;
- player behavior modeling → player action valuation;
- economy balancing → roster and salary efficiency;
- experiment design → causal evaluation of tactics, lineups, and in-game decisions;
- retention/funnel analysis → player development and team performance progression;
- simulation → game outcome, season projection, and counterfactual scenario modeling.

## Company compliance stance

Savepoint Analytics should treat license compliance as a professional differentiator. Every public deliverable should include data source attribution, a description of limitations, and a note when analysis is based on public or estimated data rather than official club/vendor data. The technical enforcement of this stance lives in `src/compliance/attributions.py`, `src/ingestion/_throttle.py`, and is documented in `docs/compliance.md`.
