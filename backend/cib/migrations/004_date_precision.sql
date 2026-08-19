-- 004_date_precision.sql — how exactly a publication date is known.
--
-- Archive research routinely yields "October 2019" rather than a day. Every day_index is measured
-- from published_at, so writing a guessed day silently shifts a campaign's whole timeline by up to
-- a month — and days-to-peak, half-life and days-to-90% are exactly the figures that distorts.
--
-- Rather than invent a day or refuse the campaign, the tool records how precisely the date is
-- known and makes every day-aligned figure carry that caveat. Same principle as reach: the value
-- is stored, and the gap is impossible to miss.

ALTER TABLE campaigns ADD COLUMN published_at_precision TEXT NOT NULL DEFAULT 'day'
    CHECK (published_at_precision IN ('day', 'month', 'year'));
