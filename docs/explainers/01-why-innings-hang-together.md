# Why Innings Hang Together

*Or: what a simulator gets wrong when it treats every inning as a fresh start.*

---

Ask any fan to describe a game that got away, and you won't hear about one bad inning.
You'll hear about a night where the starter didn't have his breaking ball, so the
third inning was ugly, and the fourth was worse, and by the sixth the bullpen was
already up and the game was 9–2.

That's not nine independent innings. That's **one bad night**, spread across nine
innings.

Our simulator plays out games one plate appearance at a time. It knows who's batting,
who's pitching, who's on base, and how many outs there are. Then it rolls the dice.
And it's remarkably good at this — inning by inning, it's almost exactly right:

| Runs in a half-inning | Real baseball | Our simulator |
|---|---|---|
| 0 | 72.40% | 72.65% |
| 1 | 14.66% | 14.69% |
| 2 | 7.05% | 6.98% |
| 3 or more | 5.89% | 5.68% |

Look how close that is. If you asked our model "how often does a team put up a
crooked number?", it would tell you almost exactly right.

## So where does it go wrong?

**When you add the innings up into a game.**

Real games have a wider spread of scores than our simulator produces. We get the
average right — about 4.5 runs a side — but we don't produce enough 1–0 games and we
don't produce enough 12–3 games. Our simulated seasons are too *beige*. Everything
clusters near the middle.

Here's the arithmetic in plain terms. If innings were truly independent — if the
third inning genuinely told you nothing about the sixth — then a game's variability
would just be nine innings' worth of variability stacked up. We can check that
against real baseball, and real baseball is *more* spread out than that stacking
predicts. Roughly 60% of what we're missing comes from this: **innings in the same
game are correlated, and our model treats them as strangers.**

## Why would innings be correlated? Ask a fan.

Every explanation is something you already know:

- **The starter is the same guy for six innings.** If he's laboring in the second,
  he's probably laboring in the fifth. His stuff doesn't reset between frames.
- **The lineup is seeing him for the second and third time.** They're picking up the
  ball better each turn. That's not luck; that's information accumulating.
- **The bullpen is the same bullpen.** If your setup man threw 30 pitches last night,
  he's not available tonight, in the seventh *or* the eighth.
- **It's the same ballpark, the same wind, the same air** for all nine innings.
- **It's the same umpire** with the same strike zone all night.

None of that is exotic. It's just baseball. But a model that draws a fresh random
number for every plate appearance, with no memory of what kind of night this is,
throws all of it away.

## Why we care

If you only want to know *who will win*, this barely matters — being too beige
doesn't move a 50/50 game much off 50/50.

But we're not only asking who wins. We want **playoff odds**, and playoff odds live in
the tails. A team that makes the postseason is usually a team that had some games go
unusually well. If our simulator can't produce enough unusual games, it will tell you
a 92-win team is a lock when really they're a strong favorite — confident in a way the
sport doesn't earn.

That's the flaw we're fixing: not the average, but the **spread**.

---

*Includes data © MLB Advanced Media / Baseball Savant.*
