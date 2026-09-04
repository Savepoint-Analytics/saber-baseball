# The Same Trick, Four Times

*How we add anything to a baseball simulator without breaking it.*

---

Our simulator now knows about four things beyond who's batting and who's pitching: the
base-out situation, home field advantage, the ballpark and weather, and what kind of
night the pitcher is having.

Those sound like four different problems. They're the same problem, solved the same
way, four times. Once you see the pattern, the whole engine gets easier to reason
about — and easier to distrust in the right places.

Every one of them answers three questions in order.

## 1. Direction — *what changes?*

Never "things get better or worse." Always: **which specific outcomes move, and in
what proportion?**

| Adjustment | What actually moves |
|---|---|
| **Base-out situation** | With runners on second and third and first base open, walks jump from 8.5% to **15.0%** — pitchers work around the hitter. Nothing else changes much. |
| **Home field** | Strikeouts down 4%, walks up 5%. Power barely moves. |
| **Park & weather** | Triples up most, then homers and doubles. **Strikeouts go down** — balls that were whiffs become balls in play. |
| **Pitcher's night** | Strikeouts down, damage up — measured off the radar gun, not off the box score. |

Each of those is measured from the data, never assumed. That matters more than it
sounds. Two adjustments can produce the identical number of runs and completely
different baseball — one built from walks, one from home runs. The runs match; the
games don't. And since we care about the shape of games, not just their averages,
guessing the direction would quietly defeat the point.

## 2. Mean preservation — *don't smuggle in runs*

Here's a trap that catches people.

Suppose you want to add randomness — some nights the pitcher is on, some nights he's
off. Reasonable. So you make his rates wobble around his true talent, and you're
careful that the wobble is even in both directions: as much good as bad.

**Run scoring still goes up.** Every time.

The reason is that runs don't respond evenly to a pitcher's quality. A pitcher who's
10% worse gives up *more* than 10% extra runs, because baserunners compound —
runners on base turn a single into a run, and a bad night creates more baserunners for
each other. The good nights save you less than the bad nights cost you. Average an
equal mix and you land above where you started.

So every adjustment we add gets checked: **does this change how many runs score on an
average night?** If it does, we recenter it until it doesn't. Otherwise "adding
realistic variability" silently becomes "inflating offense," and you'd never see it —
the games would just look a bit livelier than baseball, and every number downstream
would be wrong.

## 3. Size — *how much, and who decides?*

The last question is how far to push. This is where it's easiest to cheat, so it's
worth being explicit about the two ways to answer it.

**You can measure it.** Ask the data how big the effect actually is, and use that,
whatever it says.

**Or you can tune it.** Turn the dial until the simulator's output matches the target
you already know is right.

Tuning always looks better. That's the problem. A model tuned to produce a 53% home
win rate will produce a 53% home win rate — and it will have learned nothing, so it
will have nothing to say about a situation you didn't tune for.

We measure. Sometimes that's satisfying: home field came out at 52.6% against a real
52.8%, from strikeouts and walks alone, with nothing tuned.

Sometimes it's disappointing, and we say so. We expected park and weather to fix our
shortage of blowouts and shutouts. Measured honestly, they explain only about 1.6% of
game-to-game scoring variation — real, worth having, and nowhere near enough. We
could have turned the ballpark dial up until the blowouts appeared. Then we'd have a
simulator that produced the right number of 12–3 games by pretending Coors Field is in
the stratosphere, and it would misprice every actual game played there.

Same for the pitcher's night. That dramatic best-third/worst-third table would let us
add a *lot* of variability. The honest measurement says a starter's true form wobbles
about 4% — much less. We built with 4%, and it closed about 5% of the gap we were
aiming at. Two adjustments now, both measured, both smaller than hoped, both reported
at their real size.

## What you should take from this

When we tell you a team has a 61% chance to win tonight, that number came out of a
machine where every adjustment was measured rather than fitted, and where we know
which parts are still missing.

We'll keep telling you which parts are still missing. Right now the honest list is:
our games are a little too orderly. We produce slightly too few 1–0 games and too few
12–3 games.

We chased that in three directions — ballpark and weather, whether the pitcher had his
stuff, and how good the defense behind him was. All three are real. All three are
small. Taken together with everything else we can know about a game *before it starts*,
they account for under a seventh of what's missing.

Which tells us something useful: the rest isn't a fact about the matchup. It's about
the *order things happen in* — a pitcher who loses the zone for three straight hitters,
a lineup that starts squaring him up in the fourth. Our model knows the bases and the
outs; it doesn't know what kind of half-inning this has been. That's the next real
problem, and it's a harder one than adding another adjustment.

Until then: our extremes are slightly too rare, and playoff odds from this engine run
a touch overconfident. You should know that's there.

---

*Includes data © MLB Advanced Media / Baseball Savant.*
