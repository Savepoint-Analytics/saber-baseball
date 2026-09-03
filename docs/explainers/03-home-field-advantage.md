# Home Field Advantage Is Real, and Smaller Than You Think

*What batting last is actually worth, and how we put it in the model.*

---

Home teams win about **53%** of the time. Not 60. Not 55. Fifty-three.

That number annoys people in both directions. Fans who believe in crowd energy and
sleeping in your own bed think it should be bigger. Skeptics who think it's all noise
think it should be zero. It's neither — it's small, it's stubbornly consistent, and
it shows up in the box score in ways you can point at.

## Where it actually comes from

We can look at what home hitters do differently from road hitters. Same league, same
season, millions of plate appearances:

| | Road batting | Home batting | Difference |
|---|---|---|---|
| Strikeout rate | 22.8% | 21.9% | **4% fewer** |
| Walk rate | 8.28% | 8.73% | **5% more** |
| Home runs | 3.03% | 3.11% | 3% more |
| Doubles | 4.23% | 4.31% | 2% more |

Home hitters strike out less and walk more. That's the core of it. Every other
difference is small.

Notice what that pattern suggests. It isn't that home hitters suddenly hit the ball
harder — the power difference is modest. It's that they're **seeing the ball better**
and getting slightly better calls. Familiar backdrop, familiar hitting eye, familiar
mound, no travel legs, and an umpire who is human and hearing 30,000 people react to
every borderline pitch.

## The part nobody mentions

There's a second home advantage hiding in the rules, and it has nothing to do with
the crowd: **the home team doesn't always bat in the ninth.**

If you're ahead after the top of the ninth, you go home. You never get those last
three outs. Which means across a season, home teams bat roughly **8.9 innings a game**
while road teams bat a full 9.

This has a funny consequence. Home teams score *about the same total runs* as road
teams — 4.49 versus 4.47 — but they do it in **fewer opportunities**. Per inning, the
home team's offense is meaningfully better than the road team's. The raw run totals
hide the advantage almost completely.

If you're building a simulator and you compare simulated home runs to actual home
runs and they match, you might conclude you've modeled home field correctly. You
haven't. You've matched a number that has the effect baked out of it.

## What we do about it

Our first working simulator was perfectly even-handed. Same rules for both teams, and
it correctly refused to let the home team bat in the ninth with a lead.

It produced a home win rate of **50.4%**. Real baseball is 52.8%. The model was
missing home field entirely.

So we added it — not as a thumb on the scale, but by feeding in the measured
differences above. Home hitters in our simulator strike out 4% less often and walk 5%
more often, because that's what home hitters actually do. The result:

| | Simulated | Actual |
|---|---|---|
| Home win rate | **52.6%** | **52.8%** |

Two-tenths of a percentage point off.

## Why we didn't just dial it to 53%

We could have. It would have been one line: nudge home teams up until the win rate
matched.

We didn't, for a reason worth stating. A model that's been tuned to produce the right
answer will produce the right answer — and nothing else. It won't tell you whether
home field is bigger in a hitter's park, or in a division race, or in the postseason,
because you didn't give it a *mechanism*, you gave it a target.

By building it out of strikeouts and walks, the advantage now flows through
everything downstream. It shows up more in games between good pitchers, because
strikeouts matter more there. That's an outcome we didn't program. It fell out.

---

*Includes data © MLB Advanced Media / Baseball Savant.*
