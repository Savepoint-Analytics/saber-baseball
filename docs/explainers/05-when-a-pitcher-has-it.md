# When a Pitcher Has It

*What "he didn't have his good stuff tonight" looks like in the numbers — and how much of it is real.*

---

Every broadcast says it. *He just doesn't have it tonight.* It sounds like filler
until you go looking for it, and then it turns out to be one of the most visible
things in baseball.

Take every starter and sort his outings against **his own** season — not against the
league, against himself. Best third, worst third. Here's what the plate appearances
look like:

| | His best starts | His worst starts |
|---|---|---|
| Strikeout rate | **26.0%** | 18.5% |
| Walk rate | **5.1%** | 10.0% |
| Singles allowed | 9.7% | **19.1%** |
| Home runs | 2.2% | **4.4%** |

Same pitcher, same season. Double the walks, double the home runs.

That table is dramatic, widely quoted, and **not usable for what we need.** Here's why
— and this is the part worth understanding, because it's the difference between a
model that works and one that just looks convincing.

## The trap: sorting on the answer

We sorted those starts **by how they turned out.** So the "worst third" isn't only
starts where he was genuinely off. It also contains starts where he was sharp and
three bloops fell in, where a routine grounder found a hole, where his center fielder
took a bad route.

Sort by results and you'll always find that bad results look bad. That's not a
discovery, it's a definition. The table is partly measuring **luck wearing a bad-night
costume.**

Our second attempt was subtler and still wrong: sort by strikeouts-minus-walks, which
is more about the pitcher and less about the defense. But strikeouts and walks are two
of the nine things we're trying to measure, so sorting on them *guarantees* they'll
show the biggest gap. Measured that way, walks appeared to more than double on a bad
night — an artifact of the sorting, not a fact about baseball.

## The fix: ask the radar gun

We needed something that tells us whether he has his stuff **before** we look at what
happened. Baseball hands us one: **velocity.**

Split each pitcher's starts by his own average fastball velocity — his hard nights
versus his soft nights, against his own baseline:

| | Velocity up ~0.9 mph | Velocity down ~0.9 mph |
|---|---|---|
| Strikeout rate | **23.4%** | 20.5% |
| Walk rate | 8.38% | 8.81% |
| Hits allowed | 21.9% | 22.7% |
| On-base allowed | **.308** | .321 |

Less dramatic than the first table. Also honest. A pitcher who's down a tick misses
fewer bats, walks a few more, and gets hit a little harder. Nothing is exaggerated
because nothing was sorted on the answer.

That shape — **strikeouts down, damage up** — is what we feed the simulator.

## Now: how *often* is he off?

Knowing the shape isn't enough. We need the size, and here the honest answer is much
smaller than the first table implies.

Even a machine that pitched identically every night would post wildly different lines,
because 22 batters is a tiny sample. Some nights the flares fall in. So the real
question isn't "how much do pitchers vary" — it's **"how much do they vary beyond what
pure chance already explains?"**

The answer: about **2% more than chance alone.** A starter's true form wobbles roughly
**4%** around his own baseline. Real, measurable, and modest.

Notice the radar gun independently agrees. Our velocity split moved on-base allowed
from .308 to .321 — a gap of .013. The variance math, done completely separately, says
a starter's true form varies by .0132. Two different methods, same number. That's the
kind of agreement that makes you trust a result.

## And what it bought us

We built it: before each simulated game, one roll decides what kind of night the
pitcher is having, and it rides for every batter he faces — so his innings finally
belong to the same evening instead of being strangers.

Then we measured what it fixed. **About 5% of the gap.**

We could have made it bigger. Nothing stops us from turning the dial until our
simulated seasons had the right number of blowouts. But we'd be claiming pitchers
vary far more night-to-night than the evidence supports, and every game we simulated
at Fenway in July would inherit that lie.

So the effect goes in at its measured size, and we tell you it didn't solve the
problem. The extra scatter in real baseball is coming from somewhere we haven't found
yet — and the next honest step is finding it, not inflating something we've already
measured.

---

*Includes data © MLB Advanced Media / Baseball Savant.*
