-- Silver: the player dimension — one row per MLBAM player id, with the ID crosswalk,
-- name, bio and handedness. Every silver and gold fact table keys players by their
-- MLBAM id (`batter`, `pitcher`); this is the table that turns those integers into
-- people.
--
-- Grain: (player_id), where player_id is the MLBAM id.
--
-- **The spine is every MLBAM id we have seen, Chadwick and Lahman enrich it.**
-- Chadwick's register is the open crosswalk and names nearly everyone, but it is
-- refreshed on its own schedule and trails a debut by days to weeks -- 54 of the
-- 2026 call-ups were in the Statcast lake before they were in the register. A
-- dimension that dropped them would orphan their rows in every mart, so the ids
-- observed in sv_plate_appearances are unioned in and carried with a null name
-- until the register catches up (`in_chadwick` says which). Lahman is released
-- once a year and lags a full season, so a rookie has no Lahman row until the
-- following winter. Every column Lahman supplies therefore has a fallback: the
-- name comes from Chadwick, and bats/throws are derived from the player's own
-- Statcast plate appearances.
--
-- **Handedness derived from Statcast** uses the batter's `stand` on each PA. A
-- switch hitter is the only player who ever appears on both sides, so any player
-- with a non-trivial share (>= 5%) of PAs from each side is recorded as 'B'. The
-- threshold exists only to absorb the rare mis-keyed pitch; a genuine platoon
-- pattern is impossible for a one-sided hitter. `assert_derived_handedness_matches_lahman`
-- checks the derivation against Lahman where both exist.
--
-- **Primary position** is the position with the most career games in Lahman's
-- Fielding table. Career rather than most-recent because a player's role is
-- what a value card should lead with, and the Fielding table stops at the last
-- Lahman release anyway; modern Lahman records outfielders as 'OF', not LF/CF/RF.
-- Lahman has no DH row, so a full-time DH is labelled by whatever he last
-- fielded -- Ohtani reads 'P'. Null for players Lahman has not yet catalogued.
--
-- Chadwick uses -1 as its "no id" sentinel. Rows with no MLBAM id are dropped
-- (they cannot join to anything here) and other -1 keys are nulled.
{{ config(materialized='table') }}

with chadwick as (
    select
        key_mlbam                                                   as player_id,
        nullif(key_retro, '')                                       as key_retro,
        nullif(key_bbref, '')                                       as key_bbref,
        case when key_fangraphs > 0 then key_fangraphs end          as key_fangraphs,
        name_first,
        name_last,
        mlb_played_first,
        mlb_played_last
    from {{ ref('br_chadwick_register') }}
    where key_mlbam > 0
),

-- A handful of 19th-century placeholder rows share a bbrefID with the real entry.
-- Prefer the row whose playerID is the bbrefID itself, then the lowest row id.
people as (
    select *
    from (
        select
            *,
            row_number() over (
                partition by bbref_id
                order by (player_id_lahman = bbref_id) desc, lahman_row_id
            ) as rn
        from {{ ref('br_lahman_people') }}
        where bbref_id is not null
    )
    where rn = 1
),

positions as (
    select player_id_lahman, position
    from (
        select
            player_id_lahman,
            position,
            row_number() over (
                partition by player_id_lahman order by sum(games) desc, position
            ) as rn
        from {{ ref('br_lahman_fielding') }}
        -- Pinch-hitter / pinch-runner rows are roles, not positions.
        where position not in ('PH', 'PR')
        group by 1, 2
    )
    where rn = 1
),

statcast_batting as (
    select
        batter                                          as player_id,
        count(*)                                        as pa,
        count(*) filter (where stand = 'L')             as pa_left,
        count(*) filter (where stand = 'R')             as pa_right
    from {{ ref('sv_plate_appearances') }}
    group by 1
),

statcast_pitching as (
    select
        pitcher                                         as player_id,
        count(*)                                        as bf,
        count(*) filter (where p_throws = 'L')          as bf_left,
        count(*) filter (where p_throws = 'R')          as bf_right
    from {{ ref('sv_plate_appearances') }}
    group by 1
),

spine as (
    select player_id from chadwick
    union
    select player_id from statcast_batting
    union
    select player_id from statcast_pitching
),

derived as (
    select
        c.player_id,
        case
            when b.pa is null or b.pa = 0 then null
            when least(b.pa_left, b.pa_right) >= 0.05 * b.pa then 'B'
            when b.pa_left > b.pa_right then 'L'
            else 'R'
        end                                             as bats_statcast,
        case
            when p.bf is null or p.bf = 0 then null
            when p.bf_left > p.bf_right then 'L'
            else 'R'
        end                                             as throws_statcast,
        coalesce(b.pa, 0)                               as statcast_pa,
        coalesce(p.bf, 0)                               as statcast_bf
    from spine c
    left join statcast_batting b using (player_id)
    left join statcast_pitching p using (player_id)
)

select
    d.player_id,
    c.key_retro,
    c.key_bbref,
    c.key_fangraphs,
    coalesce(l.name_first, c.name_first)                as name_first,
    coalesce(l.name_last, c.name_last)                  as name_last,
    -- concat_ws skips a null first name (some 19th-century entries have none).
    concat_ws(' ', coalesce(l.name_first, c.name_first), coalesce(l.name_last, c.name_last))
                                                        as name_full,
    make_date(l.birth_year, l.birth_month, l.birth_day) as birth_date,
    l.birth_country,
    coalesce(l.bats, d.bats_statcast)                   as bats,
    -- Lahman spells an ambidextrous pitcher 'S' in some releases and 'B' in others;
    -- 'B' matches the switch-hitter code on `bats`.
    case
        when l.throws in ('B', 'S') then 'B'
        else coalesce(l.throws, d.throws_statcast)
    end                                                 as throws,
    d.bats_statcast,
    d.throws_statcast,
    l.height_in,
    l.weight_lb,
    l.mlb_debut,
    pos.position                                        as primary_position,
    c.mlb_played_first                                  as first_mlb_season,
    c.mlb_played_last                                   as last_mlb_season,
    d.statcast_pa,
    d.statcast_bf,
    c.player_id is not null                             as in_chadwick,
    l.bbref_id is not null                              as in_lahman
from derived d
left join chadwick c using (player_id)
left join people l on l.bbref_id = c.key_bbref
left join positions pos on pos.player_id_lahman = l.player_id_lahman
