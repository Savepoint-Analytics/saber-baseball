# Cold Nights and Thin Air

*Why the ballpark and the forecast matter — and why they matter less than you'd guess.*

---

Every fan knows the ball carries in Denver. Every fan knows a 40-degree night in April
in Cleveland is not a night for home runs. These aren't superstitions; they're physics,
and they show up hard in the data.

## Temperature

Here's runs per game by game-time temperature, outdoor games only:

| Temperature | Runs per game |
|---|---|
| Under 50°F | 8.43 |
| 50s | 8.43 |
| 60s | 8.61 |
| 70s | 8.98 |
| 80s | 9.47 |
| 90°F+ | **10.30** |

A hot afternoon produces almost **two more runs a game** than a cold night. That's
enormous — bigger than the gap between a good offense and a bad one.

It's air density. Warm air is thinner, the ball meets less resistance, and a fly ball
that dies on the track in April lands eight rows deep in August.

## The ballpark

Same idea, permanent. We measure each park by how many runs it yields compared to
league average, using the three seasons *before* the one we're modeling — so we're
never grading a park on the same games we're trying to predict.

| Park | Factor |
|---|---|
| Coors Field | **1.272** |
| Chase Field | 1.097 |
| Fenway Park | 1.061 |
| — league average — | 1.000 |
| Progressive Field | 0.912 |
| Oracle Park | 0.913 |
| T-Mobile Park | **0.866** |

Coors gives up 27% more runs than average. T-Mobile suppresses 13%. That is a bigger
swing than most trades.

## What we do with it

We combine the park and the weather into one number for each game — call it the **run
environment**. A hot July night at Coors with the wind blowing out comes out around
**1.54**. A cold April game in Seattle with the wind in comes out around **0.78**.

Then comes the part that took some care. Knowing a park yields 27% more runs doesn't
tell you *how*. Does it hand out more singles? More walks? So we measured it. In high
run environments, compared to low ones:

- Triples go up the most (biggest outfields, ball in the gap)
- Home runs and doubles follow
- **Strikeouts go down**

That last one is the tell. A rich run environment doesn't just multiply hits — it
converts strikeouts into extra-base hits. Balls that would have been swings and
misses become balls in play, and balls in play become doubles. If we'd simply assumed
"scale all the hits up 27%," we'd have gotten the right number of runs with the wrong
*kind* of runs — and the wrong kind of runs produces the wrong kind of games.

## Now the honest part

We expected this to fix a real weakness in our simulator: it produces too few
blowouts and too few shutouts. Park and weather vary game to game, so surely feeding
them in would spread the scores out.

It helped. It did not fix it.

Across a full season, **park and weather together explain only about 1.6%** of the
game-to-game variation in scoring. The reason is simple once you see it: most games
are played in ordinary parks in ordinary weather. The median game has a run
environment of 0.996 — dead average. Coors on a hot day is spectacular and it's also
*rare*.

So the effect is real, physically true, and worth having in the model. But if you're
looking for what makes one Tuesday a 2–1 game and the next Tuesday a 13–7 game, the
ballpark and the forecast are a small part of the answer.

The bigger part is who's pitching, and what kind of night he's having.

---

*Includes data © MLB Advanced Media / Baseball Savant.*
