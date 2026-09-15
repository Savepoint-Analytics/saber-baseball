# The Big Inning

*Why rallies snowball, and why that's hard to fake.*

---

A rally has a feel to it that a box score doesn't quite capture. Leadoff walk. Bloop
single. Now the infield's moving, the pitcher's working from the stretch, the crowd's
up, and the guy on the mound is throwing his fourth straight fastball because he's
lost the feel for anything else. Before you've settled back into your seat it's 5–0
and the manager's walking out.

Here's how often that actually happens:

| A half-inning with… | How often | Roughly |
|---|---|---|
| 3+ runs | 5.89% | 1 in 17 |
| 4+ runs | 2.60% | 1 in 39 |
| 5+ runs | 1.12% | 1 in 89 |
| 6+ runs | 0.49% | 1 in 205 |

Rare, but not *that* rare. You'll see a five-spot most weeks.

## The part our model gets right

Our simulator produces big innings at close to the correct rate. That surprises
people who expect a computer to be too tidy. The reason it works is that we don't
tell the model any rules about rallies — we **measured** what actually happens.

Take a runner on second with one out and a single to left. Does he score? Our model
doesn't guess. It looked at every single hit in that exact situation across a decade
of baseball and found the answer: with nobody out he scores 37.8% of the time, with
one out 51.7%, with two outs 79.7% — because with two outs he's running on contact.

Do that for all 24 combinations of runners and outs, for every kind of hit, and the
snowball builds itself. Runners on base make more runners on base. The model gets
that for free, because baseball gave it the numbers.

## The part it gets wrong

Our innings are still a little too orderly. Not much — about 40% of the total spread
we're missing traces back to inside the inning — but it's real.

What's missing is the thing a fan would call **"he lost it."** In our model, every
plate appearance is a fresh roll of the dice. The pitcher who just walked two straight
is exactly as likely to strike out the next guy as he was at the start of the inning.

Real pitchers don't work that way. When a guy loses the strike zone, he's often lost
it for the next three hitters too. When a lineup starts squaring him up, they keep
squaring him up. The rally *feeds itself* in a way that goes beyond "there are now
runners on base."

## Why this one is harder

Fixing the between-inning problem is tractable: you decide before the game whether
this is one of the pitcher's good nights, and let that ride all evening.

Fixing the within-inning problem means giving the model a **memory** — the sequence
so far, not just the current situation. That's a genuinely bigger change, because our
whole engine is built on the idea that if you tell it the bases and the outs, it knows
everything it needs.

We're being upfront that this piece isn't solved. The honest position is that our
simulator will produce slightly fewer disasters and slightly fewer masterpieces than
real baseball does, and anyone reading probabilities off it should know that.

---

*Includes data © MLB Advanced Media / Baseball Savant.*
